"""
Account and authentication views for The Daily Indaba.

Authentication is kept separate from the news app.  It leans on Django's
built-in class-based authentication views wherever possible.  Business logic
that is not tied to HTTP request/response handling has been extracted to
dedicated modules:

- ``accounts.models.User.assign_to_role_group`` — role-group assignment
- ``accounts.forms.PasswordResetRequestForm``   — reset-email delivery
- ``accounts.utils.get_safe_next_url``          — safe redirect helper
"""

# Flash message framework — adds one-off feedback (success, error, etc.)
# that survives a redirect via the session.
from django.contrib import messages
from django.conf import settings
# To get the active custom user model from Django settings, plus login() to
# attach the authenticated user to the session.
from django.contrib.auth import get_user_model, login  # Cf. Mele (2025:372)
# Redirects unauthenticated users to settings.LOGIN_URL and, by default,
# appends `?next=/requested/path/` so the auth flow can return the user to the
# original page after login.
# Cf. Guest et al., Web Development with Django 6, pp. 619-620.
from django.contrib.auth.decorators import login_required
# Django's built-in auth views already implement session handling, redirect
# logic, and password-reset token validation.  We subclass them only where the
# project needs role-aware redirects or namespaced success URLs.
# Cf.: https://docs.djangoproject.com/en/5.2/topics/auth/default/#all-authentication-views
from django.contrib.auth.views import (
    LoginView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetView,
)
from django.shortcuts import redirect, render, resolve_url
# NoReverseMatch is raised by reverse() when a URL name is not yet wired —
# caught here to gracefully fall back during early development:
from django.urls import NoReverseMatch, reverse, reverse_lazy

from .forms import (
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    ProfileUpdateForm,
    RegistrationForm,
)
from .models import TermsAcceptance, get_all_articles_monthly_fee
from .utils import get_safe_next_url


# Cf. Mele (2025:373). get_user_model() returns the custom user model used
# here to distinguish between reader, journalist, editor, and publisher
# accounts.
User = get_user_model()
REGISTRATION_ROLES = {choice[0] for choice in User.ROLE_CHOICES}


def _normalise_registration_role(raw_role):
    """Return a safe registration role, defaulting to ``reader``."""
    if raw_role in REGISTRATION_ROLES:
        return raw_role
    return "reader"


# =============================================================================
# DERIVED REQUIREMENT - Route authenticated users through safe post-auth
# redirects so registration/login flows honour valid next targets without
# reopening open-redirect risk.
# =============================================================================
def _get_post_auth_redirect(request, user):
    """
    Return the default post-login/register redirect URL for the
    authenticated user.

    Journalists land on their desk dashboard; editors land on the editorial
    dashboard.  Readers return to the homepage.  If the relevant URL is not
    yet wired (e.g. during early development), falls back gracefully to ``/``.

    A safe ``next`` URL in the request always takes priority over the role
    default.
    """
    # Honour a safe `next` URL first when one was supplied by the browser.
    # Django's built-in auth flow treats `next` as the post-login redirect
    # target; this helper keeps that convention while validating the URL.
    # Cf. Guest et al., Web Development with Django 6, pp. 619-620.
    next_url = get_safe_next_url(request)
    if next_url and next_url != "/":
        return next_url

    # Otherwise choose the default landing page based on the user's role.
    role_defaults = {
        "journalist": "news:journalist_dashboard",
        "editor": "news:editor_dashboard",
        "publisher": "news:publisher_dashboard",
    }
    url_name = role_defaults.get(user.role)
    if url_name:
        try:
            # Resolve the named URL while still allowing early-development fallbacks.
            return resolve_url(url_name)
        except NoReverseMatch:
            pass

    try:
        # Fall back to Django's configured global login redirect setting.
        return resolve_url(settings.LOGIN_REDIRECT_URL)
    except NoReverseMatch:
        # Use the site root as the last-resort redirect target.
        return "/"


# ------------------------------------------------------------------
# CORE REQUIREMENT - Authentication and password-recovery views required by
# the brief: register, log in, log out, reset password, and change password.
# ------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------

class DailyIndabaLoginView(LoginView):
    """
    Wrap Django's built-in ``LoginView`` with role-aware post-login routing.

    Django's built-in login view already validates credentials with
    ``AuthenticationForm`` and calls ``login()`` to attach the user to the
    session.  This subclass only customises the final redirect so journalists
    and editors land on their desks while readers return to the site homepage
    (unless a safe ``next`` URL was supplied).

    Cf. https://docs.djangoproject.com/en/5.2/topics/auth/default/#using-the-views
    """
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        # Local import avoids a circular dependency: accounts → daily_indaba
        # → accounts (via AUTH_USER_MODEL).  The import is cheap because
        # Django caches imported modules after the first load.
        from daily_indaba.models import ArticleNotification

        # If the user has unseen article-approval announcements, redirect
        # to the oldest unread one so notifications are drained in order.
        unseen = (
            ArticleNotification.objects
            # Add WHERE recipient_id = request.user.pk AND seen_at IS NULL.
            .filter(recipient=self.request.user, seen_at__isnull=True)
            .order_by("created_at")   # ORDER BY created_at ASC.
            .first()  # Return the oldest unread row or None.
        )
        if unseen:
            return reverse(
                "news:announcement_detail", kwargs={"pk": unseen.pk}
            )

        return _get_post_auth_redirect(self.request, self.request.user)


class DailyIndabaPasswordResetView(PasswordResetView):
    """
    Wrap Django's built-in ``PasswordResetView`` with project-specific
    templates and e-mail delivery.

    The token generation, user lookup, invalid-link protection, and expiry
    handling remain Django's responsibility.  Only the template names and the
    e-mail transport helper are customised here.
    """
    form_class = PasswordResetRequestForm
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/emails/password_reset_email.txt"
    html_email_template_name = "accounts/emails/password_reset_email.html"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class DailyIndabaPasswordResetConfirmView(PasswordResetConfirmView):
    """
    Wrap Django's built-in ``PasswordResetConfirmView`` with the project's
    template and namespaced success URL.

    Django validates the ``uidb64`` + token pair with
    ``PasswordResetTokenGenerator`` and expires earlier tokens according to
    ``settings.PASSWORD_RESET_TIMEOUT``.
    """
    form_class = PasswordResetConfirmForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class DailyIndabaPasswordChangeView(PasswordChangeView):
    """
    Expose Django's built-in password-change flow to authenticated users.

    ``PasswordChangeView`` updates the current session hash after a successful
    password change, so the user stays signed in instead of being logged out
    immediately after changing their own password.
    """
    template_name = "accounts/password_change_form.html"
    success_url = reverse_lazy("accounts:password_change_done")


def register_user(request):
    """
    Register a new user account and assign them to their role group.

    On successful registration the user is logged in immediately and
    redirected to their role's default landing page.

    Args:
        request (HttpRequest): The current HTTP request.

    Returns:
        HttpResponse: On successful POST, redirects to the role default or
        ``next``.  On GET or invalid POST, renders ``accounts/register.html``
        with:

        - ``form`` — a bound or unbound ``RegistrationForm``
        - ``next`` — the ``next`` query-string value
    """
    # ----------------------------------------------------------------------
    # Preserve the post-registration redirect target and bind the form:
    # ----------------------------------------------------------------------
    # Preserve any incoming `next` target so it survives a failed validation
    # round-trip. Mele demonstrates the same hidden-field pattern for Django's
    # login workflow when a user first arrives with `?next=/target/`.
    # Cf. Mele, Django 5 By Example, p. 347.
    next_url = request.GET.get("next", "")
    selected_role = _normalise_registration_role(
        request.GET.get("role", "reader")
    )
    # Bind POST data when present; otherwise initialise the blank registration form.
    form = RegistrationForm(
        request.POST or None,
        initial={"role": selected_role},
    )

    if request.method == "POST":
        # Refresh `next` from POST because hidden form fields travel in the
        # body. This keeps the original redirect target alive after form
        # validation errors instead of losing it between requests.
        # Cf.: https://www.geeksforgeeks.org/python/django-redirect-to-previous-page-after-login/
        next_url = request.POST.get("next", "")
        selected_role = _normalise_registration_role(
            request.POST.get("role", "reader")
        )
        form = RegistrationForm(
            request.POST,
            initial={"role": selected_role},
        )
        if form.is_valid():
            # ------------------------------------------------------------------
            # Create the new user account on valid submission:
            # ------------------------------------------------------------------
            # Save the new user account to the database.
            user = form.save()
            # Record that the user has accepted the terms and conditions.
            TermsAcceptance.record_for(user)
            # ------------------------------------------------------------------
            # Log the new user in and show any role-specific guidance:
            # ------------------------------------------------------------------
            # Log the user in immediately after successful registration.
            login(request, user)
            if user.role in {"editor", "publisher"}:
                # For editors and publishers, show a message with their system-generated username.
                messages.info(
                    request,
                    f"Your {user.role} login username is {user.username}.",
                )
            # Send the new user to either a safe `next` URL or their role landing page.
            return redirect(_get_post_auth_redirect(request, user))

    # ----------------------------------------------------------------------
    # Render the registration form on GET or validation failure:
    # ----------------------------------------------------------------------
    # On GET, or on invalid POST, re-render the registration form.
    return render(
        request,
        "accounts/register.html",
        {
            "form": form,
            "next": next_url,
            "role_choices": User.ROLE_CHOICES,
            "selected_role": selected_role,
        },
    )


# ------------------------------------------------------------------
# DERIVED REQUIREMENT - Present and persist role-aware terms-and-conditions
# acceptance so registration leaves an auditable consent trail.
# ------------------------------------------------------------------
# Terms and conditions
# ------------------------------------------------------------------

def terms_and_conditions(request):
    """
    Display the role-specific terms and conditions.

    Accepts an optional ``role`` query parameter (``reader``,
    ``journalist``, ``editor``, or ``publisher``) to highlight the
    relevant section. Invalid values are silently ignored and the
    full page is shown without a pre-selected role.

    This view is intentionally public — users must be able to read
    the terms before they register.

    Args:
        request (HttpRequest): The current HTTP request.

    Returns:
        HttpResponse: Renders ``accounts/terms_and_conditions.html``
        with ``role`` in context (empty string when absent/invalid).
    """
    # Read the optional role hint so the template can highlight the matching section.
    role = request.GET.get("role", "")
    if role not in REGISTRATION_ROLES:
        # Discard invalid values instead of trusting arbitrary query-string input.
        role = ""
    return render(
        request,
        "accounts/terms_and_conditions.html",
        {"role": role},
    )


# ------------------------------------------------------------------
# Profile
# ------------------------------------------------------------------

@login_required
def account_profile(request):
    """
    Display the logged-in user's profile summary.

    Shows the user's role, bio, and profile picture.  Provides links to edit
    profile and (for readers) manage subscriptions.

    Returns:
        HttpResponse: Renders ``accounts/profile.html``.
    """
    return render(request, "accounts/profile.html")


@login_required
def update_profile(request):
    """
    Update the logged-in user's profile information.

    Handles first/last name, email, bio, and profile picture.  On a valid
    POST, saves the changes and redirects to the profile page with a success
    message.

    Returns:
        HttpResponse: On valid POST, redirects to profile.  Otherwise renders
        ``accounts/update_profile.html`` with:

        - ``form`` — a bound or unbound ``ProfileUpdateForm``
    """
    # Keep the previous username so editor-name changes can be highlighted after save.
    previous_username = request.user.username
    # Guest et al. describe uploaded images as media files handled through the
    # ordinary Django form pipeline: bind POST data plus request.FILES to the
    # ModelForm so the profile picture is validated and stored under MEDIA_ROOT.
    # See Web Development with Django 6, Packt, pp. 509-523.
    form = ProfileUpdateForm(
        request.POST or None,
        request.FILES or None,
        instance=request.user,
    )

    if request.method == "POST" and form.is_valid():
        # Save the profile edits through the ModelForm.
        user = form.save()
        messages.success(request, "Your profile has been updated.")
        if (
            user.role == "editor"
            and getattr(form, "editor_username_changed", False)
        ):
            # Editors receive an explicit reminder when the system rewrites their username.
            messages.info(
                request,
                f"Your editor login username is now {user.username}.",
            )
        elif user.role == "editor" and user.username != previous_username:
            # Fallback branch covering any rename path that changes the username.
            messages.info(
                request,
                f"Your editor login username is now {user.username}.",
            )
        return redirect(reverse("accounts:profile"))

    # On GET, or on invalid POST, re-render the bound profile form.
    return render(request, "accounts/update_profile.html", {"form": form})


@login_required
def subscriptions(request):
    """
    Display and manage the reader's active subscriptions.

    Only meaningful for readers; journalists and editors see an
    informational placeholder.  Unsubscribe POST actions are handled
    by ``news:toggle_subscription`` in the daily_indaba app.

    Returns:
        HttpResponse: Renders ``accounts/subscriptions.html`` with:

        - ``publisher_subs``  — Subscription rows for publishers
        - ``journalist_subs`` — Subscription rows for journalists
        - ``pricing``         — Dict with discount tier and monthly total
    """
    from decimal import Decimal

    # ----------------------------------------------------------------------
    # Initialise safe default values for the subscriptions page:
    # ----------------------------------------------------------------------
    # Default empty values let the same template render safely for non-readers too.
    publisher_subs = []
    journalist_subs = []
    pricing = {}
    available_publishers = []
    available_journalists = []
    subscribed_publisher_ids = set()
    subscribed_journalist_ids = set()
    all_articles_fee = get_all_articles_monthly_fee()
    pricing_tiers = [
        {
            "title": "Single Source",
            "headline": "1 subscription",
            "detail": "Pay the listed monthly fee for one publisher or journalist.",
            "accent": "border-primary-subtle",
        },
        {
            "title": "Double Source",
            "headline": "2 subscriptions",
            "detail": "20% discount applied to the combined monthly total.",
            "accent": "border-success-subtle",
        },
        {
            "title": "Reader Bundle",
            "headline": "3+ subscriptions",
            "detail": "30% discount applied to the combined monthly total.",
            "accent": "border-warning-subtle",
        },
        {
            "title": "All Access",
            "headline": f"R{all_articles_fee}/month",
            "detail": "Unlimited access to every article on the platform.",
            "accent": "border-dark-subtle",
        },
    ]

    # ----------------------------------------------------------------------
    # Load the reader's current subscriptions and available sources:
    # ----------------------------------------------------------------------
    if request.user.is_reader:
        # Import locally to keep the accounts app decoupled from the news
        # models until the subscriptions page is actually requested.
        from daily_indaba.models import Publisher, Subscription

        # Load the reader's publisher subscriptions and their related publisher rows.
        publisher_subs = list(
            Subscription.objects
            # Add WHERE reader_id = request.user.pk AND publisher_id IS NOT NULL.
            .filter(reader=request.user, publisher__isnull=False)
            # Tell Django to join the publisher table up front for efficiency.
            .select_related("publisher")
            # Add ORDER BY publisher.name ASC.
            .order_by("publisher__name")
        )
        # Load the reader's journalist subscriptions and their related user rows.
        journalist_subs = list(
            Subscription.objects
            # Add WHERE reader_id = request.user.pk AND journalist_id IS NOT NULL.
            .filter(reader=request.user, journalist__isnull=False)
            # Tell Django to join the journalist table up front for efficiency.
            .select_related("journalist")
            # Add ORDER BY journalist.username ASC.
            .order_by("journalist__username")
        )
        # Record subscribed IDs so the template can disable duplicate subscribe buttons.
        subscribed_publisher_ids = {
            sub.publisher_id for sub in publisher_subs
        }
        subscribed_journalist_ids = {
            sub.journalist_id for sub in journalist_subs
        }

        # Load all available publishers and journalists for the discovery cards.
        available_publishers = list(
            # Add ORDER BY name ASC.
            Publisher.objects.order_by("name")
        )
        available_journalists = list(
            User.objects
            # Add WHERE role = 'journalist'.
            .filter(role="journalist")
            # Add ORDER BY username ASC.
            .order_by("username")
        )

        # ------------------------------------------------------------------
        # Calculate the reader's subscription pricing summary:
        # ------------------------------------------------------------------
        # Count the total number of subscriptions to determine discount tier.
        total_subs = len(publisher_subs) + len(journalist_subs)

        # Apply discount based on number of subscriptions: 20% for 2, 30% for 3+.
        if total_subs == 2:
            discount_pct = 20
        elif total_subs >= 3:
            discount_pct = 30
        else:
            discount_pct = 0

        # Calculate the pre-discount monthly total from all subscriptions.
        raw_total = (
            sum(s.publisher.monthly_fee for s in publisher_subs)
            + sum(
                s.journalist.journalist_monthly_fee
                for s in journalist_subs
            )
        )
        # Apply the bundle discount to get the final monthly price.
        discounted_total = (
            raw_total * (1 - Decimal(discount_pct) / 100)
        )

        # Package all pricing information for the template.
        pricing = {
            "total_subs": total_subs,
            "discount_pct": discount_pct,
            "raw_total": raw_total,
            "discounted_total": discounted_total,
            "all_articles_fee": all_articles_fee,
            "all_articles_active": request.user.all_articles_plan,
        }

        # Determine a human-readable title for the current subscription plan.
        current_plan_title = "No subscriptions yet"
        if request.user.all_articles_plan:
            current_plan_title = "All-Articles plan active"
        elif total_subs >= 3:
            current_plan_title = "Reader bundle (30% off)"
        elif total_subs == 2:
            current_plan_title = "Double source discount (20% off)"
        elif total_subs == 1:
            current_plan_title = "Single source pricing"

        pricing["current_plan_title"] = current_plan_title

    # ----------------------------------------------------------------------
    # Render the subscriptions page:
    # ----------------------------------------------------------------------
    # Render the same page for readers and non-readers, with context adjusted above.
    return render(
        request,
        "accounts/subscriptions.html",
        {
            "publisher_subs": publisher_subs,
            "journalist_subs": journalist_subs,
            "pricing": pricing,
            "available_publishers": available_publishers,
            "available_journalists": available_journalists,
            "subscribed_publisher_ids": subscribed_publisher_ids,
            "subscribed_journalist_ids": subscribed_journalist_ids,
            "pricing_tiers": pricing_tiers,
        },
    )
