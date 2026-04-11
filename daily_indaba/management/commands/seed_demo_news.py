"""
Opt-in demo data seeding for The Daily Indaba local showcase environments.

  * Reads a single custom-format JSON file from
    ``daily_indaba/seed_data/default_news_data.json``.
  * Uses ``get_or_create`` throughout - safe to run multiple times.
  * Accepts ``--password`` (plain text) for all demo accounts so that
    no password hash is stored in source control.
  * Accepts ``--update-existing`` to refresh fields on already-seeded
    records to match the current JSON snapshot.
  * Calls ``assign_to_role_group()`` after every user creation / update
    so Django auth groups stay in sync.

Usage
-----
  python manage.py seed_demo_news
  python manage.py seed_demo_news --password indaba2026
  python manage.py seed_demo_news --update-existing
"""

# BaseCommand is the thin Django CLI entrypoint. The heavy lifting now lives in
# the seeding package so the command itself can stay focused on argument
# parsing and terminal output.
from django.core.management.base import BaseCommand

from daily_indaba.seeding.content import _pick_approval_editor
from daily_indaba.seeding.demo_news import DemoNewsSeeder, SeedDependencies
from daily_indaba.seeding.helpers import (
    _COMMENT_SEED_CUTOFF,
    _attach_image,
    _attach_profile_picture,
    _load_article_content,
    _load_seed_data,
    _resolve_comment_created_at as _base_resolve_comment_created_at,
    _stable_seed_value,
    _sync_article_image as _base_sync_article_image,
    _sync_profile_picture as _base_sync_profile_picture,
    _to_aware_seed_datetime,
)


def _sync_article_image(article, filename, update_existing):
    """
    Command-level wrapper for article image syncing.

    This keeps the historical patch point inside the command module intact for
    tests that monkeypatch ``_attach_image`` here rather than in the seeding
    service module.
    """
    return _base_sync_article_image(
        article,
        filename,
        update_existing,
        attach_image_func=_attach_image,
    )


def _sync_profile_picture(user, filename, update_existing):
    """
    Command-level wrapper for profile-picture syncing.

    The wrapper mirrors the old command-module patch point so tests can swap in
    a fake ``_attach_profile_picture`` implementation without reaching into the
    new seeding package.
    """
    return _base_sync_profile_picture(
        user,
        filename,
        update_existing,
        attach_profile_picture_func=_attach_profile_picture,
    )


def _resolve_comment_created_at(comment_data, article, parent, last_comment_at):
    """
    Command-level wrapper for comment timestamp generation.

    The wrapper passes the command module's helper aliases into the service
    implementation so tests can continue patching helpers on this module.
    """
    return _base_resolve_comment_created_at(
        comment_data,
        article,
        parent,
        last_comment_at,
        to_aware_seed_datetime_func=_to_aware_seed_datetime,
        stable_seed_value_func=_stable_seed_value,
        cutoff=_COMMENT_SEED_CUTOFF,
    )


class Command(BaseCommand):
    # Because this file lives under management/commands/, Django exposes the
    # class as `python manage.py seed_demo_news`. Django calls add_arguments()
    # first to define CLI flags, then handle() to execute the command body.
    help = (
        "Seed opt-in demo users, publishers, articles, newsletters, "
        "subscriptions and comments for The Daily Indaba."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="demo1234",
            help=(
                "Shared plain-text password set on every demo user "
                "account. Default: demo1234."
            ),
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help=(
                "Overwrite existing demo records to match the current "
                "JSON snapshot."
            ),
        )

    def handle(self, *args, **options):
        """
        Run the extracted seeding service and print the final one-line summary.

        The command intentionally stays thin: it parses CLI options, injects
        the command module's helper aliases for backwards-compatible test
        patching, and then delegates the actual seeding workflow.
        """
        seeder = DemoNewsSeeder(
            dependencies=SeedDependencies(
                load_seed_data=_load_seed_data,
                load_article_content=_load_article_content,
                sync_article_image=_sync_article_image,
                sync_profile_picture=_sync_profile_picture,
                pick_approval_editor=_pick_approval_editor,
                resolve_comment_created_at=_resolve_comment_created_at,
            ),
            stderr=self.stderr,
        )
        summary = seeder.run(
            password=options["password"],
            update_existing=options["update_existing"],
        )
        self.stdout.write(self.style.SUCCESS(summary.success_message()))
