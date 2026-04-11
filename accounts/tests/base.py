"""Shared fixtures for the retained account-flow tests."""

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


class AccountFlowBaseTestCase(TestCase):
    """Shared fixtures and helpers for account-flow tests."""

    def setUp(self):
        # Cf. Mele (2025:373). get_user_model() returns the custom user model
        # used here to distinguish between reader, journalist, and editor
        # accounts.
        self.User = get_user_model()

        self.reader = self.User.objects.create_user(
            username="reader",
            password="reader-pass-123",
            email="reader@example.com",
            role="reader",
        )
        self.editor = self.User.objects.create_user(
            username="legacy.editor",
            password="editor-pass-123",
            email="editor@example.com",
            role="editor",
            first_name="Legacy",
            last_name="Editor",
        )

    def build_password_reset_confirm_url(self, user, token=None):
        """
        Build the reset-confirm URL for the given user.

        Django's built-in password-reset confirm URL contains:
        1. ``uidb64`` - the user's primary key encoded for safe use in a URL
        2. ``token``  - Django's stateless password-reset token
        """
        token = token or default_token_generator.make_token(user)
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        return reverse("accounts:password_reset_confirm", args=[uidb64, token])

    def prime_password_reset_session(self, user):
        """
        Prime Django's reset-confirm session and return the redirected URL.

        PasswordResetConfirmView first validates the incoming token and then
        redirects to the same view with an internal session marker. Posting to
        that redirected URL mirrors the real browser flow.
        """
        response = self.client.get(self.build_password_reset_confirm_url(user))
        self.assertEqual(response.status_code, 302)
        return response["Location"]
