"""
Shared role-group definitions and sync helpers for Daily Indaba.

Both the post_migrate signal and the create_role_groups management command use
the functions in this module. That keeps the group names, permission
definitions, and ORM logic in one place so future permission changes only need
to be made once.
"""

# To get the active custom user model from Django settings.
from django.contrib.auth import get_user_model  # Cf. Mele (2025:372)
# Group stores named bundles of permissions; Permission rows live in
# Django's `auth_permission` table and are created during migrate.
# Cf.: https://docs.djangoproject.com/en/5.2/topics/auth/default/#groups
from django.contrib.auth.models import Group, Permission

# =============================================================================
# CORE REQUIREMENT - Define the Readers, Journalists, Editors, and Publishers
# groups plus their model-permission codenames so the brief's role matrix is
# explicit in one central module.
# =============================================================================
READER_GROUP_NAME = "Readers"
JOURNALIST_GROUP_NAME = "Journalists"
EDITOR_GROUP_NAME = "Editors"
PUBLISHER_GROUP_NAME = "Publishers"

READER_PERMISSION_CODENAMES = (
    "view_article",
    "view_newsletter",
)

JOURNALIST_PERMISSION_CODENAMES = (
    "add_article",
    "change_article",
    "delete_article",
    "view_article",
    "add_newsletter",
    "change_newsletter",
    "delete_newsletter",
    "view_newsletter",
)

EDITOR_PERMISSION_CODENAMES = (
    "add_newsletter",
    "change_article",
    "delete_article",
    "view_article",
    "change_newsletter",
    "delete_newsletter",
    "view_newsletter",
)

PUBLISHER_PERMISSION_CODENAMES = (
    "change_publisher",
    "view_publisher",
    "view_article",
    "view_newsletter",
)

ROLE_GROUPS = (
    ("reader", READER_GROUP_NAME, READER_PERMISSION_CODENAMES),
    ("journalist", JOURNALIST_GROUP_NAME, JOURNALIST_PERMISSION_CODENAMES),
    ("editor", EDITOR_GROUP_NAME, EDITOR_PERMISSION_CODENAMES),
    ("publisher", PUBLISHER_GROUP_NAME, PUBLISHER_PERMISSION_CODENAMES),
)
# Keeping the role -> group -> permission mapping in one tuple lets both the
# post_migrate hook and the management command iterate the exact same source
# of truth.


# =============================================================================
# DERIVED REQUIREMENT - Synchronise role groups idempotently so every migrate
# run can rebuild the authorisation baseline without manual admin setup.
# =============================================================================
def sync_role_groups():
    """
    Create or update every Daily Indaba role group and return a summary.

    The helper is idempotent: it can run after every migrate without creating
    duplicate groups or memberships.
    """
    return [
        _sync_one_group(
            role=role,
            group_name=group_name,
            codenames=codenames,
        )
        for role, group_name, codenames in ROLE_GROUPS
    ]


def _sync_one_group(*, role, group_name, codenames):
    """
    Synchronise one role group and return a summary dict.

    Parameters:
        role: Internal `User.role` value used to select matching users.
        group_name: Django auth-group name to create or update.
        codenames: Permission codenames that should belong to the group.
    """
    # Cf. Mele (2025:373). get_user_model() returns the custom user model used
    # here to distinguish between reader, journalist, and editor accounts:
    User = get_user_model()

    # get_or_create() makes repeated sync runs idempotent - safe after every
    # migrate and safe from the manual `create_role_groups` command too:
    group, created = Group.objects.get_or_create(name=group_name)
    # Permissions must be fetched as database rows, not supplied as bare
    # codename strings, because Group.permissions is a ManyToMany relation.

    # Look in the auth_permission table:
    permissions = Permission.objects.filter(
        # Filter by the related django_content_type table
        # where row has app_label = 'daily_indaba
        content_type__app_label="daily_indaba",
        # Cf. Example code: M06T06 – Django – eCommerce Application Part 1\
        #         Examples\AuthLog\grabsomore\views.py
        # Filter by auth_permission's codename field:
        codename__in=codenames,
    )
    # `.set(...)` replaces the group's full permission membership in one step,
    # which removes drift if the codename list changes later.
    group.permissions.set(permissions)

    users = User.objects.filter(  # Build a queryset with WHERE
        role=role  # role = role
    )
    group.user_set.set(users)
    # `group.user_set` is the reverse ManyToMany manager for `User.groups`.
    # Updating from the group side bulk-syncs every matching user to the role.

    return {
        "role": role,
        "name": group_name,
        "created": created,
        "permission_count": permissions.count(),
        "user_count": users.count(),
    }
