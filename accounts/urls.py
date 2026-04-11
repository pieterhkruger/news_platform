"""URL configuration for the accounts app.

All URLs are namespaced under ``accounts``.  The namespace is picked up
automatically from :attr:`app_name` by Django's URL resolver.

URL structure by implementation classification
----------------------------------------------
All paths below are relative to ``/accounts/``.

Core requirements
~~~~~~~~~~~~~~~~~
/login/                        login
/logout/                       logout
/register/                     register new account
/password-change/              change password (authenticated)
/password-change/done/         password change confirmation
/password-reset/               request password-reset email
/password-reset/done/          password-reset email sent confirmation
/reset/<uidb64>/<token>/       password-reset confirmation form
/reset/done/                   password-reset completion page

Derived requirements
~~~~~~~~~~~~~~~~~~~~
/terms/                        public role-aware terms page supporting
                               recorded terms acceptance during registration

Good-to-haves
~~~~~~~~~~~~~
/profile/                      logged-in user profile summary
/profile/edit/                 edit logged-in user profile

Nice extensions
~~~~~~~~~~~~~~~
/subscriptions/                reader subscription management and pricing

Optional features
~~~~~~~~~~~~~~~~~
No standalone ``accounts`` routes are classified as optional-only.  The
public terms page is also an informational surface, but this URL is
listed under derived requirements because it directly supports the
registration consent flow.

Approved support functionality
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
No public runtime URLs.  Approved support functionality for this app
lives in management commands, demo-data tooling, and educational
annotations elsewhere in the codebase.
"""

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------
    path("profile/", views.account_profile, name="profile"),
    path("profile/edit/", views.update_profile, name="update_profile"),
    path("subscriptions/", views.subscriptions, name="subscriptions"),
    # ------------------------------------------------------------------
    # Terms and conditions
    # ------------------------------------------------------------------
    path("terms/", views.terms_and_conditions, name="terms_and_conditions"),
    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    # Django's built-in auth views provide the core login/logout/password
    # flows. Cf.: Melé, Antonio. Django 5 By Example (pp. 344-345).
    # Project subclass (DailyIndabaLoginView) only
    # customise template names and redirect destinations.
    # Cf.: https://docs.djangoproject.com/en/5.2/topics/auth/default/#all-authentication-views
    # & https://docs.djangoproject.com/en/5.2/topics/auth/default/#using-the-views
    path("login/", views.DailyIndabaLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(
        template_name="accounts/logout.html"), name="logout"),
    path("register/", views.register_user, name="register"),
    path(
        "password-change/",
        views.DailyIndabaPasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "password-change/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="accounts/password_change_done.html"
        ),
        name="password_change_done",
    ),
    # ------------------------------------------------------------------
    # Password recovery
    # ------------------------------------------------------------------
    path(
        "password-reset/",
        views.DailyIndabaPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        views.DailyIndabaPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]
