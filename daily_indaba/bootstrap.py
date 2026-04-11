"""
Post-migrate bootstrap helpers for Daily Indaba.
Note: Bootstrap helpers are setup functions that run after migrations to seed
the database with initial data (e.g. default roles and permissions).

The fresh-database seed is intentionally kept separate from the signal wiring
in apps.py so the same guard logic can be tested or reused without importing
AppConfig side effects.
"""

import sys

# call_command() runs a management command from Python code instead of the CLI.
# Cf.: https://docs.djangoproject.com/en/5.2/ref/django-admin/#running-management-commands-from-your-code
from django.core.management import call_command

from .models import Article


def seed_demo_news_if_fresh():
    """
    Seed showcase data only when migrate is targeting a fresh local database.

    The seed command itself remains available for manual re-seeding. This hook
    only exists to make `python manage.py migrate` leave the project in a
    usable demo state, mirroring the eCommerce pattern used in M06T06/M06T07.
    """
    if _is_test_run():
        # Test runs create temporary databases repeatedly; automatic seeding
        # would make those tests slower and less deterministic.
        return False

    # Determine whether at least one Article row already exists.
    if Article.objects.exists():
        # Any existing article means the database already holds content, so
        # leave the current data untouched.
        return False

    # call_command() invokes the seed_demo_news management command from within
    # the running migrate process, as if it were typed in the terminal:
    call_command("seed_demo_news", verbosity=0)
    return True


def _is_test_run():
    """Return True when Django is building a test database."""
    # Django's test runner invokes commands such as `manage.py test`, so
    # scanning argv is a simple project-level guard against seeding demo data
    # into temporary test databases.
    # sys.argv is a Python built-in list of CLI arguments, e.g.
    # ["manage.py", "test"]. The loop variable `arg` takes each value
    # from that list (skipping index 0, the script name).
    return any(arg == "test" for arg in sys.argv[1:])
