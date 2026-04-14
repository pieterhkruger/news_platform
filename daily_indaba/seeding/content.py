"""
Content- and taxonomy-related seed steps for Daily Indaba demo data.

This module groups the categories, articles, newsletters, and comments that
form the editorial side of the seed graph.
"""

from typing import TYPE_CHECKING

from django.core.management.base import CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from daily_indaba.models import Article, Comment, Newsletter, NewsletterCategory

if TYPE_CHECKING:
    from .demo_news import SeedDependencies


def _pick_approval_editor(
    article_data,
    publisher,
    publisher_editors,
    independent_editors,
    editors,
):
    """Choose a deterministic demo editor for an approved article."""
    # Honour an explicit "approved_by" username from the seed snapshot first.
    approved_by_username = article_data.get("approved_by")
    if approved_by_username:
        editor = next(
            (user for user in editors if user.username == approved_by_username),
            None,
        )
        if editor:
            return editor

    # Pending/unapproved articles should not have an approving editor at all.
    if not article_data.get("approved", True):
        return None

    candidates = []
    if publisher is not None:
        # Publisher-backed articles should, where possible, be approved by one
        # of that publisher's editors.
        candidates = publisher_editors.get(publisher.name, [])
    else:
        # Publisher-less articles use the editors who curate independent
        # journalists.
        candidates = independent_editors
    if not candidates:
        # Final fallback: any editor in the demo data can be used.
        candidates = editors
    if not candidates:
        # If no editors exist at all, leave approved_by empty.
        return None

    # Derive a stable index from the title so the same article keeps the same
    # approving editor across re-seeds.
    seed_value = sum(ord(char) for char in article_data.get("title", ""))
    return candidates[seed_value % len(candidates)]


def _seed_categories(data, update_existing: bool):
    """
    Seed NewsletterCategory rows and build two lookup dicts.

    Also pre-computes article_category_slugs from the newsletter ->
    article_titles relationships in the seed file. Older seed snapshots
    attached categories only at the newsletter level rather than directly on
    each article; this fallback map preserves compatibility. An article
    inherits a category only when all newsletters that reference it agree on
    the same slug.
    """
    # ------------------------------------------------------------------
    # Newsletter categories must exist before newsletters are seeded.
    # They are created here as well so manual re-seeds stay self-contained,
    # even though a fresh `manage.py migrate` can trigger this seed process
    # automatically via a post_migrate bootstrap hook.
    # ------------------------------------------------------------------

    # ================================
    # Seed category rows from the JSON snapshot
    # ================================
    for category_data in data.get("categories", []):
        # ================================
        # Retrieve or create the category row
        # ================================
        # Category slug is the natural key because newsletters and articles
        # refer to categories by slug in the seed data.
        category_obj, created = NewsletterCategory.objects.get_or_create(
            slug=category_data["slug"],
            defaults={
                "name": category_data["name"],
                "description": category_data.get("description", ""),
            },
        )

        # ================================
        # Update existing category fields when requested
        # ================================
        if not created and update_existing:
            changed = False
            # Exact-sync mode aligns the category's editable fields with the
            # current JSON snapshot.
            desired_values = {
                "name": category_data["name"],
                "description": category_data.get("description", ""),
            }
            for field, value in desired_values.items():
                if getattr(category_obj, field) != value:
                    setattr(category_obj, field, value)
                    changed = True
            if changed:
                category_obj.save()

    # ================================
    # Build the category lookup map
    # ================================
    # Reload every category from the database so the lookup includes both newly
    # created rows and categories that already existed before this run.
    categories_by_slug = {
        category.slug: category
        for category in NewsletterCategory.objects.all()
    }

    # ================================
    # Derive article-category fallback mappings
    # ================================
    article_category_slugs = {}
    for newsletter_data in data.get("newsletters", []):
        category_slug = newsletter_data.get("category_slug")
        for article_title in newsletter_data.get("article_titles", []):
            # Track every category implied for an article through newsletter
            # membership so older snapshots can still infer article.category.
            article_category_slugs.setdefault(article_title, set())
            if category_slug:
                article_category_slugs[article_title].add(category_slug)

    return categories_by_slug, article_category_slugs


def _seed_articles(
    data,
    users_by_username,
    publishers_by_name,
    publisher_editors_by_name,
    independent_editors,
    editor_users,
    categories_by_slug,
    article_category_slugs,
    update_existing: bool,
    dependencies: "SeedDependencies",
):
    """
    Seed Article rows with author, publisher, category, and approval data.

    article_category_slugs provides a fallback category for articles that carry
    no explicit category_slug in the seed file, inferred from newsletter ->
    article_titles relationships built in _seed_categories.
    """
    # ------------------------------------------------------------------
    # Articles
    # ------------------------------------------------------------------
    articles_by_title = {}
    created_articles = 0

    for article_data in data.get("articles", []):
        # ================================
        # Resolve article dependencies and derived fields
        # ================================
        # Resolve the required author and optional publisher from the lookup
        # caches built by earlier seed stages.
        author = users_by_username.get(article_data["author"])
        publisher = (
            publishers_by_name.get(article_data["publisher"])
            if article_data.get("publisher")
            else None
        )

        category_slug = article_data.get("category_slug")
        if not category_slug:
            # Older seed snapshots only implied article categories through the
            # newsletters they appeared in, so fall back to that mapping when
            # the article itself has no explicit category_slug.
            fallback_slugs = article_category_slugs.get(
                article_data["title"],
                set(),
            )
            if len(fallback_slugs) == 1:
                # Only inherit the fallback category when every referencing
                # newsletter agrees on the same slug.
                category_slug = next(iter(fallback_slugs))
        category = (
            categories_by_slug.get(category_slug)
            if category_slug
            else None
        )
        image_filename = article_data.get("image")
        # Store the logical media path in the model row; the actual file copy
        # into storage happens later via sync_article_image(...).
        seeded_image_name = (
            f"articles/images/{image_filename}"
            if image_filename
            else None
        )
        if (
            article_data["importance"] == Article.FRONT_PAGE
            and not image_filename
        ):
            # Front Page articles must satisfy the same image requirement as the
            # live app, so fail fast on invalid seed data.
            raise CommandError(
                "Front Page seed articles must define an image: "
                f"{article_data['title']}"
            )

        approved = article_data.get("approved", True)
        # Choose a deterministic demo editor for approved articles so the
        # approved_by field looks realistic across re-runs.
        approved_by = dependencies.pick_approval_editor(
            article_data,
            publisher,
            publisher_editors_by_name,
            independent_editors,
            editor_users,
        )

        pub_date_raw = article_data.get("publication_date")
        pub_date = (
            # Honour an explicit publication timestamp from JSON when supplied.
            parse_datetime(pub_date_raw)
            if pub_date_raw
            # Otherwise, approved demo articles should appear published now,
            # while pending ones keep publication_date unset.
            else (timezone.now() if approved else None)
        )

        # ================================
        # Retrieve or create the article row
        # ================================
        # Article title acts as the natural key for idempotent seeding.
        article_obj, created = Article.objects.get_or_create(
            title=article_data["title"],
            defaults={
                # content may be inline HTML or a filename that points at a
                # bundled article-body fragment on disk.
                "content": dependencies.load_article_content(
                    article_data["content"]
                ),
                "importance": article_data["importance"],
                "image": seeded_image_name,
                "author": author,
                "publisher": publisher,
                "category": category,
                "approved": approved,
                "approved_by": approved_by,
                "publication_date": pub_date,
                "disclaimer": article_data.get("disclaimer", ""),
            },
        )
        if created:
            created_articles += 1

        # ================================
        # Update existing article fields when requested
        # ================================
        else:
            # Even in non-destructive mode, force one image sync when the DB row
            # lacks the actual file attachment but the seed snapshot expects it.
            should_force_image_sync = bool(image_filename) and not article_obj.image
            changed = False
            if update_existing:
                # Exact-sync mode aligns the stored article fields with the
                # current JSON snapshot.
                desired_values = {
                    "content": dependencies.load_article_content(
                        article_data["content"]
                    ),
                    "importance": article_data["importance"],
                    "image": seeded_image_name,
                    "author": author,
                    "publisher": publisher,
                    "category": category,
                    "approved": approved,
                    "approved_by": approved_by,
                    "publication_date": pub_date,
                    "disclaimer": article_data.get("disclaimer", ""),
                }
                for field, value in desired_values.items():
                    # Only touch fields that really changed so re-seeding stays
                    # as quiet and idempotent as possible.
                    if getattr(article_obj, field) != value:
                        setattr(article_obj, field, value)
                        changed = True
            elif approved_by and article_obj.approved_by_id is None:
                # Non-destructive re-seeds may still backfill approved_by for
                # older rows that were missing it.
                article_obj.approved_by = approved_by
                changed = True
                if should_force_image_sync:
                    # Keep the DB field aligned with the image file that will be
                    # attached immediately afterwards.
                    article_obj.image = seeded_image_name

            if changed:
                # Persist all scalar/FK changes in one save().
                article_obj.save()
        if created:
            # Brand-new rows still need their image file copied into storage.
            should_force_image_sync = True

        # ================================
        # Sync the article image and cache the lookup
        # ================================
        # Attach or clear the media file through the storage-aware helper after
        # the Article row exists.
        dependencies.sync_article_image(
            article_obj,
            image_filename,
            update_existing or should_force_image_sync,
        )
        # Cache by title so newsletters and comments can resolve article FKs
        # without extra queries.
        articles_by_title[article_obj.title] = article_obj

    return articles_by_title, created_articles


def _seed_newsletters(
    data,
    users_by_username,
    articles_by_title,
    categories_by_slug,
    update_existing: bool,
):
    """
    Seed Newsletter rows and their article M2M relationships.

    New newsletters receive the exact seeded article set immediately. Existing
    newsletters only receive exact-sync M2M updates when update_existing=True;
    otherwise, new links are added without removing pre-existing manual
    curation.
    """
    # ------------------------------------------------------------------
    # Newsletters
    # ------------------------------------------------------------------
    created_newsletters = 0
    for newsletter_data in data.get("newsletters", []):
        # ================================
        # Resolve newsletter foreign-key dependencies
        # ================================
        # Newsletters link back to an author account and an optional category.
        author = users_by_username.get(newsletter_data["author"])
        category = (
            categories_by_slug.get(newsletter_data["category_slug"])
            if newsletter_data.get("category_slug")
            else None
        )

        # ================================
        # Retrieve or create the newsletter row
        # ================================
        # Newsletter title is used as the natural key for idempotent seeding.
        newsletter_obj, created = Newsletter.objects.get_or_create(
            title=newsletter_data["title"],
            defaults={
                "description": newsletter_data.get("description", ""),
                "author": author,
                "category": category,
            },
        )
        if created:
            created_newsletters += 1

        # ================================
        # Update existing newsletter fields when requested
        # ================================
        elif update_existing:
            changed = False
            # Exact-sync mode aligns the scalar fields with the seed snapshot.
            desired_values = {
                "description": newsletter_data.get("description", ""),
                "author": author,
                "category": category,
            }
            for field, value in desired_values.items():
                if getattr(newsletter_obj, field) != value:
                    setattr(newsletter_obj, field, value)
                    changed = True
            if changed:
                newsletter_obj.save()

        # ================================
        # Sync the newsletter's article relationships
        # ================================
        # Resolve each referenced article title to the actual Article instance,
        # skipping missing titles so a partial seed file does not crash here.
        desired_articles = [
            article
            for title in newsletter_data.get("article_titles", [])
            for article in [articles_by_title.get(title)]
            if article is not None
        ]
        if created or update_existing:
            # New rows and exact-sync runs should match the seed snapshot
            # exactly.
            newsletter_obj.articles.set(desired_articles)
        else:
            # Non-destructive re-seeds only add missing seeded links and keep
            # any manual curation that already exists.
            for article in desired_articles:
                newsletter_obj.articles.add(article)

    return created_newsletters


def _seed_comments(
    data,
    articles_by_title,
    users_by_username,
    update_existing: bool,
    dependencies: "SeedDependencies",
    stderr,
):
    """
    Seed Comment rows in dependency order even when the seed file is not
    already parent-first.

    Comments are inserted via bulk_create rather than get_or_create so that
    model-level UX guards (word-count, comment-count limits) do not block seed
    data. Those constraints exist to prevent reader abuse, not to constrain
    demo content.
    """
    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------
    created_comments = 0

    # ================================
    # Initialize comment lookup state
    # ================================
    comments_by_seed_id = {}
    last_comment_at_by_article = {}

    # ================================
    # Process comments until all parent links are resolved
    # ================================
    pending_comments = list(data.get("comments", []))
    while pending_comments:
        # Each pass tries to seed every currently-resolvable comment. Replies
        # whose parents are still missing are deferred to the next pass.
        progressed = False
        remaining_comments = []

        for comment_data in pending_comments:
            # ================================
            # Resolve comment dependencies
            # ================================
            article = articles_by_title.get(comment_data["article_title"])
            author = users_by_username.get(comment_data["author"])
            if not article or not author:
                # Skip broken seed references but report them so the operator
                # can fix the JSON snapshot if needed.
                stderr.write(
                    "Skipping comment (article or author not found): "
                    f"{comment_data.get('id', '?')}"
                )
                progressed = True
                continue

            # ================================
            # Defer replies whose parent is not ready yet
            # ================================
            parent_seed_id = comment_data.get("parent_id")
            if parent_seed_id and parent_seed_id not in comments_by_seed_id:
                remaining_comments.append(comment_data)
                continue

            # ================================
            # Compute the comment hierarchy and timestamp
            # ================================
            parent = (
                comments_by_seed_id.get(parent_seed_id)
                if parent_seed_id
                else None
            )
            depth = (parent.depth + 1) if parent else 1
            created_at = dependencies.resolve_comment_created_at(
                comment_data,
                article,
                parent,
                last_comment_at_by_article.get(article.title),
            )

            # ================================
            # Retrieve or create the comment row
            # ================================
            # Treat article + author + body as the seed identity so re-runs do
            # not create duplicate demo comments.
            qs = Comment.objects.filter(
                article=article,
                author=author,
                body=comment_data["body"],
            )
            if qs.exists():
                comment_obj = qs.first()
                if update_existing:
                    # Exact-sync mode can still correct the comment's tree
                    # position or seeded timestamp on an existing row.
                    updates = {}
                    if comment_obj.parent_id != (parent.pk if parent else None):
                        updates["parent_id"] = parent.pk if parent else None
                    if comment_obj.depth != depth:
                        updates["depth"] = depth
                    if comment_obj.created_at != created_at:
                        updates["created_at"] = created_at
                    if updates:
                        # QuerySet.update writes the values directly, including
                        # created_at, without re-running model save logic.
                        Comment.objects.filter(pk=comment_obj.pk).update(**updates)
                        # Keep the in-memory object aligned for the rest of the
                        # current seeding pass.
                        if "parent_id" in updates:
                            comment_obj.parent = parent
                        if "depth" in updates:
                            comment_obj.depth = depth
                        if "created_at" in updates:
                            comment_obj.created_at = created_at
            else:
                # bulk_create bypasses model save/validation hooks so demo
                # comments are not blocked by reader-facing UX limits.
                [comment_obj] = Comment.objects.bulk_create([
                    Comment(
                        article=article,
                        author=author,
                        body=comment_data["body"],
                        parent_id=parent.pk if parent else None,
                        depth=depth,
                        created_at=created_at,
                    )
                ])
                if comment_obj.pk is None:
                    # Some MySQL backends insert the row successfully but do
                    # not populate the primary key back onto objects returned
                    # by bulk_create(). Re-fetch the just-created seed comment
                    # so later replies always see a persisted parent with a pk.
                    comment_obj = qs.order_by("-pk").first()
                    if comment_obj is None:
                        raise CommandError(
                            "Seed comment insert succeeded without a "
                            "recoverable primary key."
                        )
                # Force the seeded timestamp back onto the row in case model-
                # level auto_now_add/default behavior overrode it on insert.
                Comment.objects.filter(pk=comment_obj.pk).update(
                    created_at=created_at
                )
                comment_obj.created_at = created_at
                created_comments += 1

            # ================================
            # Register the persisted comment for later replies
            # ================================
            if "id" in comment_data:
                comments_by_seed_id[comment_data["id"]] = comment_obj
            last_comment_at_by_article[article.title] = comment_obj.created_at
            progressed = True

        # ================================
        # Fail fast if parent references cannot be resolved
        # ================================
        if not progressed:
            unresolved_ids = ", ".join(
                str(comment_data.get("id", "?"))
                for comment_data in remaining_comments
            )
            raise CommandError(
                "Could not resolve parent_id references for seed "
                f"comments: {unresolved_ids}"
            )

        # Loop again with only the replies that were waiting on parents from
        # the pass that just completed.
        pending_comments = remaining_comments

    return created_comments
