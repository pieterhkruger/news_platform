"""Role and permission regression tests for the accounts app."""

from django.contrib.auth.models import Group

from .base import AccountFlowBaseTestCase


class RoleAndPermissionTests(AccountFlowBaseTestCase):
    """Tests for retained role synchronisation and permission coverage."""

    def test_user_save_keeps_role_group_membership_in_sync(self):
        """
        Verify a plain ``User.save()`` call is enough to realign role groups.
        """
        # -----------------------------------------------------------------
        # ARRANGE: Ensure the four role groups exist, then create a reader
        #          via the normal ORM path rather than the registration view
        # -----------------------------------------------------------------
        Group.objects.get_or_create(name="Readers")
        Group.objects.get_or_create(name="Journalists")
        Group.objects.get_or_create(name="Editors")
        Group.objects.get_or_create(name="Publishers")

        user = self.User.objects.create_user(
            username="role_sync_user",
            password="RoleSync-5522",
            role="reader",
        )
        # Checks: newly created reader user is placed in the Readers group
        self.assertEqual(
            list(user.groups.values_list("name", flat=True)),
            ["Readers"],
        )

        # -----------------------------------------------------------------
        # ACT: Change the user's role and save the model directly
        # -----------------------------------------------------------------
        user.role = "editor"
        user.save()

        # -----------------------------------------------------------------
        # ASSERT: Verify the user's auth-group membership matches the edited
        #         role without needing an extra manual sync call
        # -----------------------------------------------------------------
        # Checks: group membership updates to Editors after role change
        self.assertEqual(
            list(user.groups.values_list("name", flat=True)),
            ["Editors"],
        )
