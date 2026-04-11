"""
App configuration and post-migrate bootstrap for Daily Indaba.

This module is Django's entry point for the app: DailyIndabaConfig.ready()
registers two post_migrate signal handlers that run automatically after every
`manage.py migrate`. The actual bootstrap logic lives in separate modules to
keep this file focused on wiring:

- role_groups.py  creates/updates the Readers, Journalists, Editors, and
  Publishers permission groups (runs first).
- bootstrap.py    seeds demo articles and users via call_command(), but only
  when the database is fresh (runs second, so seeded users can be assigned to
  the groups that already exist).
"""

from django.apps import AppConfig
from django.db.models.signals import post_migrate


class DailyIndabaConfig(AppConfig):
    """Configure startup hooks for the news app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "daily_indaba"

    def ready(self):
        """
        Register post-migrate bootstrap hooks for role groups and demo data.

        AppConfig.ready() is Django's documented startup hook and is called
        once after all models are imported and the app registry is fully
        loaded. It is the earliest safe point to connect signal handlers
        without risking circular imports.

        Signals are Django's built-in observer/event system. Connecting a
        handler to post_migrate tells Django to call that handler automatically
        after every successful `manage.py migrate` run, removing the need for
        a separate manual setup command on a fresh database.

        The receivers are connected in dependency order so the role groups and
        permissions are synchronised before the demo seed command creates
        reader / journalist / editor / publisher accounts that depend on
        those groups.

        NOTE: intentional for this demo project. In a production app, remove
        the seeding hook or gate it behind a setting (for example DEBUG=True)
        so demo data never lands in a live environment.
        """
        from . import signals  # noqa: F401

        post_migrate.connect(
            _sync_role_groups_after_migrate,
            sender=self,
            dispatch_uid="daily_indaba.sync_role_groups_after_migrate",
        )
        post_migrate.connect(
            _seed_demo_news_after_migrate,
            sender=self,
            dispatch_uid="daily_indaba.seed_demo_news_after_migrate",
        )


def _sync_role_groups_after_migrate(sender, using, **kwargs):
    """
    Create or update the Readers, Journalists, Editors, and Publishers groups.

    Called automatically by Django's post_migrate signal.
    """
    from .role_groups import sync_role_groups

    # `using` is required here because post_migrate sends it as part of the
    # signal payload. It means "which database alias did Django just migrate?"
    # Docs: https://docs.djangoproject.com/en/stable/ref/signals/#post-migrate
    # Docs: https://docs.djangoproject.com/en/stable/topics/db/multi-db/#manually-selecting-a-database
    sync_role_groups()


def _seed_demo_news_after_migrate(sender, using, **kwargs):
    """
    Seed the showcase dataset after group setup when the database is fresh.

    This handler runs second so the permission groups already exist when demo
    users are created and assigned to those groups.
    """
    from .bootstrap import seed_demo_news_if_fresh

    # `using` again comes from post_migrate and names the database alias
    # targeted by `migrate`; this project accepts it because Django sends it.
    # Docs: https://docs.djangoproject.com/en/stable/ref/signals/#post-migrate
    # Docs: https://docs.djangoproject.com/en/stable/topics/db/multi-db/#manually-selecting-a-database
    seed_demo_news_if_fresh()
