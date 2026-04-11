"""
Shared helper functions for Daily Indaba demo-data seeding.

These utilities are intentionally kept separate from the orchestration service
so file I/O, timestamp generation, and media syncing can be reused and tested
in isolation from the multi-step seed workflow.
"""

import json
from datetime import timedelta, timezone as dt_timezone
from pathlib import Path

from django.core.files import File
from django.utils import timezone
from django.utils.dateparse import parse_datetime

_SEED_FILE = (
    Path(__file__).resolve().parent.parent
    / "seed_data"
    / "default_news_data.json"
)

_IMAGES_DIR = _SEED_FILE.parent / "images"
_ARTICLES_DIR = _SEED_FILE.parent / "articles"
_COMMENT_SEED_CUTOFF = parse_datetime("2026-03-29T00:00:00Z")


# ==============================================================================
# DATA LOADING
# Reads and returns the raw seed snapshot from disk. The seeder calls this once
# per run, then passes the parsed dict through the private _seed_* steps.
# ==============================================================================

def _load_seed_data():
    # Read the bundled JSON snapshot from disk in one go.
    # utf-8-sig tolerates a BOM if the file was saved by Windows tools.
    return json.loads(_SEED_FILE.read_text(encoding="utf-8-sig"))


# ==============================================================================
# DATETIME / TIMESTAMP UTILITIES
# Helpers for parsing and generating timezone-aware datetimes used during
# comment seeding, where realistic timestamps must be derived or validated.
# ==============================================================================

def _to_aware_seed_datetime(value):
    """Return a timezone-aware datetime parsed from a seed value."""
    # Missing timestamps stay missing so callers can choose a fallback.
    if value is None:
        return None
    # Strings from JSON need parsing; existing datetime objects can pass through.
    parsed = parse_datetime(value) if isinstance(value, str) else value
    # Invalid strings (or unsupported values) are treated as absent timestamps.
    if parsed is None:
        return None
    # Seed timestamps without timezone info are assumed to be UTC.
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, dt_timezone.utc)
    # Already-aware datetimes can be returned unchanged.
    return parsed


def _stable_seed_value(*parts):
    """Return a deterministic integer derived from the supplied strings."""
    # Collapse the supplied identity fields into one stable string so the same
    # seed input always produces the same derived ordering/timestamps.
    text = "|".join(str(part) for part in parts if part is not None)
    # Weight each character by its position to reduce collisions compared with a
    # plain sum of ordinals while keeping the function simple and deterministic.
    return sum((index + 1) * ord(char) for index, char in enumerate(text))


def _resolve_comment_created_at(
    comment_data,
    article,
    parent,
    last_comment_at,
    *,
    to_aware_seed_datetime_func=None,
    stable_seed_value_func=None,
    cutoff=_COMMENT_SEED_CUTOFF,
):
    """
    Return a realistic, deterministic timestamp for a seeded comment.

    Comments must appear after the article's publication date, replies must
    follow the parent comment they respond to, and generated timestamps should
    remain before 29 March 2026 so the demo timeline stays coherent.
    """
    # Allow tests to inject stub helpers while production code uses the module
    # defaults.
    to_aware_seed_datetime_func = (
        to_aware_seed_datetime_func or _to_aware_seed_datetime
    )
    stable_seed_value_func = stable_seed_value_func or _stable_seed_value

    # Establish the floor: comments must post-date the article they belong to.
    # Use publication_date if available (approved articles), fall back to
    # created_at (drafts/pending), and finally to now() as a last resort.
    article_published_at = to_aware_seed_datetime_func(
        article.publication_date)
    article_created_at = to_aware_seed_datetime_func(article.created_at)
    base_time = article_published_at or article_created_at or timezone.now()

    # If the seed file supplies an explicit timestamp, honour it directly
    # rather than generating one. This allows hand-crafted timelines in JSON.
    explicit_created_at = to_aware_seed_datetime_func(
        comment_data.get("created_at")
    )
    if explicit_created_at is not None:
        created_at = explicit_created_at
    else:
        # Derive a deterministic offset from identity fields so the same seed
        # file produces the same timeline across re-runs and environments.
        seed_value = stable_seed_value_func(
            comment_data.get("id"),
            comment_data.get("article_title"),
            comment_data.get("author"),
        )
        if parent is not None:
            # Reply: place 7-48 minutes after the parent so threads read in a
            # natural order (seed_value % 41 gives 0-40, plus 7).
            created_at = parent.created_at + timedelta(
                minutes=7 + (seed_value % 41)
            )
        elif last_comment_at is None:
            # First top-level comment on an article: place it 25-200 minutes
            # after the article base time (seed_value % 175 gives 0-174, plus
            # 25).
            created_at = base_time + timedelta(
                minutes=25 + (seed_value % 175)
            )
        else:
            # Subsequent top-level comment: place it 18-168 minutes after the
            # previous comment to simulate a realistic discussion pace.
            created_at = last_comment_at + timedelta(
                minutes=18 + (seed_value % 150)
            )

    # Guard 1: comments may not pre-date their article.
    if created_at <= base_time:
        created_at = base_time + timedelta(minutes=5)
    # Guard 2: replies must follow their parent chronologically.
    if parent is not None and created_at <= parent.created_at:
        created_at = parent.created_at + timedelta(minutes=5)
    # Guard 3: clamp all seeded comments to the demo cutoff window.
    if cutoff is not None and created_at >= cutoff:
        created_at = cutoff - timedelta(minutes=1)
    return created_at


# ==============================================================================
# CONTENT AND IMAGE HELPERS
# Low-level helpers that read article body HTML from disk and attach image
# files to model instances via Django's storage backend.
# ==============================================================================

def _load_article_content(value):
    """Return article body HTML."""
    # If the seed value points at an .html fragment, load the file contents
    # from the bundled article-body directory.
    if isinstance(value, str) and value.endswith(".html"):
        path = _ARTICLES_DIR / value
        if path.exists():
            return path.read_text(encoding="utf-8")
    # Otherwise treat the value as inline HTML/plain text already present in
    # the JSON snapshot.
    return value


def _attach_image(article, filename):
    """Save a seed image file to article.image if not already set."""
    # Resolve the source asset inside the seed-data images directory.
    image_path = _IMAGES_DIR / filename
    if not image_path.exists():
        return False
    # Ask Django's storage layer where this file should live for this model
    # field so local storage and alternative backends behave consistently.
    storage_name = article.image.field.generate_filename(article, filename)
    if article.image.storage.exists(storage_name):
        # Remove an existing media file first so re-seeds reuse the same
        # logical filename instead of creating suffixed variants.
        article.image.storage.delete(storage_name)
    # Save through the model field so Django updates both storage and the DB
    # field value together.
    with image_path.open("rb") as fh:
        article.image.save(filename, File(fh), save=True)
    return True


def _attach_profile_picture(user, filename):
    """Save a seed image file to user.profile_picture."""
    # Resolve the source asset inside the shared seed-data images directory.
    image_path = _IMAGES_DIR / filename
    if not image_path.exists():
        return False

    storage = user.profile_picture.storage
    # Generate the storage path Django expects for this user's upload.
    target_name = user.profile_picture.field.generate_filename(user, filename)
    current_name = user.profile_picture.name

    if current_name and current_name != target_name and storage.exists(
        current_name
    ):
        # Delete the old file when the seed now points at a different filename.
        storage.delete(current_name)
    if storage.exists(target_name):
        # Delete the target first so re-seeds overwrite cleanly instead of
        # producing suffixed filenames.
        storage.delete(target_name)

    # Save through the ImageField so the model and storage stay in sync.
    with image_path.open("rb") as fh:
        user.profile_picture.save(filename, File(fh), save=True)
    return True


def _sync_article_image(article,
                        filename,
                        update_existing, *, attach_image_func=None):
    """Bring article.image into line with the current seed snapshot."""
    attach_image_func = attach_image_func or _attach_image
    if filename:
        # The seed expects an image file. Attach it on first seed, or replace
        # the existing one when update_existing requests an exact sync.
        if update_existing or not article.image:
            return attach_image_func(article, filename)
        return False

    if update_existing and article.image:
        # The current snapshot no longer includes an image, so remove any old
        # one when exact-sync mode is enabled.
        article.image = None
        article.save(update_fields=["image"])
        return True
    return False


def _sync_profile_picture(
    user,
    filename,
    update_existing,
    *,
    attach_profile_picture_func=None,
):
    """Bring user.profile_picture into line with the current seed snapshot."""
    attach_profile_picture_func = (
        attach_profile_picture_func or _attach_profile_picture
    )
    if filename:
        # The seed expects a profile picture. Attach it on first seed, or
        # replace the existing one during an exact-sync update.
        if update_existing or not user.profile_picture:
            return attach_profile_picture_func(user, filename)
        return False

    if update_existing and user.profile_picture:
        # Exact-sync mode also clears pictures that were removed from the seed
        # snapshot.
        user.profile_picture.delete(save=False)
        user.save(update_fields=["profile_picture"])
        return True
    return False
