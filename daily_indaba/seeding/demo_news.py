"""
Demo news seeding orchestration for The Daily Indaba.

The management command remains the CLI entrypoint, but the seeding workflow is
now split into focused modules:

  * helpers.py  - file loading, datetime generation, and media syncing helpers
  * accounts.py - pricing, users, publishers, and subscriptions
  * content.py  - categories, articles, newsletters, and comments

This module keeps the high-level service and dependency container so callers
only need one public orchestrator entrypoint.
"""

import sys
from dataclasses import dataclass
from typing import Any, Callable

from django.db import transaction

from .accounts import (
    _seed_pricing,
    _seed_publishers,
    _seed_subscriptions,
    _seed_users,
)
from .content import (
    _pick_approval_editor,
    _seed_articles,
    _seed_categories,
    _seed_comments,
    _seed_newsletters,
)
from .helpers import (
    _load_article_content,
    _load_seed_data,
    _resolve_comment_created_at,
    _sync_article_image,
    _sync_profile_picture,
)


@dataclass(frozen=True)
class SeedDependencies:
    """Injectable hooks used by DemoNewsSeeder during a seed run."""

    load_seed_data: Callable[[], Any] = _load_seed_data
    load_article_content: Callable[[Any], Any] = _load_article_content
    sync_article_image: Callable[[Any, Any, bool], Any] = _sync_article_image
    sync_profile_picture: Callable[[Any, Any, bool], Any] = _sync_profile_picture
    pick_approval_editor: Callable[..., Any] = _pick_approval_editor
    resolve_comment_created_at: Callable[..., Any] = _resolve_comment_created_at


@dataclass(frozen=True)
class SeedSummary:
    """Counts returned after a successful demo seed run."""

    created_pricing: bool
    created_users: int
    created_publishers: int
    created_articles: int
    created_newsletters: int
    created_subscriptions: int
    created_comments: int

    def success_message(self):
        """Return the one-line summary shown by the management command."""
        return (
            "Seeded demo news data - "
            f"pricing_policy={1 if self.created_pricing else 0}, "
            f"users={self.created_users}, "
            f"publishers={self.created_publishers}, "
            f"articles={self.created_articles}, "
            f"newsletters={self.created_newsletters}, "
            f"subscriptions={self.created_subscriptions}, "
            f"comments={self.created_comments}."
        )


class DemoNewsSeeder:
    """Seed opt-in demo users, publishers, articles, newsletters, and comments."""

    def __init__(self, *, dependencies=None, stderr=None):
        self.dependencies = dependencies or SeedDependencies()
        self.stderr = stderr or sys.stderr

    def run(self, *, password, update_existing):
        """
        Orchestrate the full demo-data seed in foreign-key dependency order.

        Each private seed step handles one data category. The sequence below
        follows the FK graph so every referenced object exists before it is
        linked:

          1. pricing      - no FK deps; must exist before subscriptions.
          2. categories   - no FK deps; must exist before articles/newsletters.
          3. users        - no FK deps; must exist before all content.
          4. publishers   - depends on users (account, editors, journalists).
          5. articles     - depends on users, publishers, categories.
          6. newsletters  - depends on users, articles, categories.
          7. subscriptions - depends on users, publishers.
          8. comments     - depends on articles and users.
        """
        # Read the committed seed snapshot once before any writes occur, then
        # pass the parsed data through the category-specific seed stages below.
        data = self.dependencies.load_seed_data()

        # Keep the database portion of the seed process atomic so a failure in
        # a later stage does not leave a half-seeded dataset behind.
        #
        # Important limitation: Django database transactions do not roll back
        # files written to media storage. The helper functions are therefore
        # still written to be idempotent, but a hard failure after an image
        # write may still leave an orphaned file on disk.
        with transaction.atomic():
            created_pricing = _seed_pricing(data, update_existing)

            categories_by_slug, article_category_slugs = _seed_categories(
                data,
                update_existing,
            )

            users_by_username, created_users = _seed_users(
                data,
                password,
                update_existing,
                self.dependencies,
            )

            (
                publishers_by_name,
                publisher_editors_by_name,
                independent_editors,
                editor_users,
                created_publishers,
            ) = _seed_publishers(data, users_by_username, update_existing)

            articles_by_title, created_articles = _seed_articles(
                data,
                users_by_username,
                publishers_by_name,
                publisher_editors_by_name,
                independent_editors,
                editor_users,
                categories_by_slug,
                article_category_slugs,
                update_existing,
                self.dependencies,
            )

            created_newsletters = _seed_newsletters(
                data,
                users_by_username,
                articles_by_title,
                categories_by_slug,
                update_existing,
            )

            created_subscriptions = _seed_subscriptions(
                data,
                users_by_username,
                publishers_by_name,
            )

            created_comments = _seed_comments(
                data,
                articles_by_title,
                users_by_username,
                update_existing,
                self.dependencies,
                self.stderr,
            )

        return SeedSummary(
            created_pricing=created_pricing,
            created_users=created_users,
            created_publishers=created_publishers,
            created_articles=created_articles,
            created_newsletters=created_newsletters,
            created_subscriptions=created_subscriptions,
            created_comments=created_comments,
        )
