"""
Management command: create_role_groups

Creates or updates the Readers, Journalists, and Editors permission groups
required by the Daily Indaba registration and authorisation flow.

Usage:
    python manage.py create_role_groups
"""

from django.core.management.base import BaseCommand

from daily_indaba.role_groups import sync_role_groups


class Command(BaseCommand):
    # Django discovers BaseCommand subclasses inside management/commands/ and
    # exposes them through the CLI using the module filename as the command
    # name, so this class powers `python manage.py create_role_groups`.
    help = "Create or update the Readers, Journalists, and Editors groups."

    def handle(self, *args, **options):
        # Reuse the shared sync helper so manual CLI runs and automatic
        # post_migrate bootstrap always apply the exact same group rules.
        for result in sync_role_groups():
            action = "Created" if result["created"] else "Updated"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{action} '{result['name']}': "
                    f"{result['permission_count']} permission(s), "
                    f"{result['user_count']} user(s)"
                )
            )
