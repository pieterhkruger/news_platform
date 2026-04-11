"""
Account utility functions for The Daily Indaba.

Keeps email dispatch and URL-safety logic out of views.py so each concern
is independently testable.  Mirrors the pattern from the eCommerce accounts
app (accounts/utils.py), but the active reset flow uses Django's built-in auth
views and rendered reset templates.
"""

import logging  # Cf. https://docs.python.org/3/library/logging.html
# smtplib exposes low-level SMTP error types for precise exception matching.
import smtplib
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import (
    BadHeaderError,
    EmailMultiAlternatives,
    get_connection,
)
# Developer's note:
# BadHeaderError is raised when email headers contain newline characters
# (a header-injection attack vector).  EmailMultiAlternatives supports both a
# plain-text body and an optional HTML alternative.  get_connection() opens a
# backend connection by class name, used to route messages through a specific
# backend at runtime.
# Cf.: https://docs.djangoproject.com/en/5.2/topics/email/
from django.utils.http import url_has_allowed_host_and_scheme
# Developer's note:
# url_has_allowed_host_and_scheme() is Django's host/scheme guard for
# redirect targets. Django's own built-in views use the same pattern when
# handling browser-supplied `next` values.
# Cf.: https://docs.djangoproject.com/en/5.0/_modules/django/utils/http/
# Cf.: https://docs.djangoproject.com/en/4.2/_modules/django/views/i18n/

logger = logging.getLogger(__name__)

# A tuple of exception types covering all email delivery failure modes.
# Using a named tuple allows `except _EMAIL_DELIVERY_ERRORS` to catch all
# of them in one clause without a broad bare `except Exception`.
_EMAIL_DELIVERY_ERRORS = (BadHeaderError,
                          smtplib.SMTPException, OSError, RuntimeError)

# Backends that write to a local sink — for these we print a simplified
# plain-text copy of the rendered message instead of attempting live delivery.
_CONSOLE_LIKE_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
}


# =============================================================================
# DERIVED REQUIREMENT - Normalise and validate redirect targets so login,
# registration, and subscription flows cannot be tricked into redirecting to
# external hosts.
# =============================================================================
# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _coerce_safe_local_url(request, url):
    """Return *url* as a safe local path or ``None`` when it is unsafe.

    The project mostly passes relative URLs such as ``/accounts/profile/`` in
    ``next`` fields, but browsers send absolute URLs in ``Referer`` headers.
    This helper accepts both forms when they point back to the current host,
    then normalises them to a local path so downstream views can safely hand
    the value to :func:`django.shortcuts.redirect`.
    """
    if not url:
        return None

    if url.startswith("/") and not url.startswith("//"):
        return url

    if not url_has_allowed_host_and_scheme(
        url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return None

    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    if parts.fragment:
        path = f"{path}#{parts.fragment}"
    if path.startswith("//"):
        return None
    return path


def get_safe_next_url(request, default=None):
    """
    Return the ``next`` query / POST parameter when it resolves safely.

    Django's authentication flow standardizes on ``next`` as the default
    redirect field.  ``login_required()`` redirects anonymous users to
    ``LOGIN_URL`` with ``?next=/requested/path/``, and ``LoginView`` /
    ``redirect_to_login()`` redirect back to that ``next`` target after a
    successful login when it is present.  This helper keeps the same
    convention while validating the supplied destination before redirecting.
    Guest et al. describe the same flow for ``login_required()`` and the
    ``next`` query parameter; Mele likewise shows that the login form can
    preserve the redirect target by posting a hidden ``next`` field back to
    the login view.

    Relative URLs such as ``/daily-indaba/articles/`` are accepted directly.
    Same-host absolute URLs are also accepted and reduced back down to their
    local path so that browser-supplied absolute ``next`` values remain
    usable without reopening the door to open redirects.

    Cf.: https://docs.djangoproject.com/en/5.2/topics/auth/default/
    Cf. Guest et al., Web Development with Django 6, pp. 619-620.
    Cf. Mele, Django 5 By Example, p. 347.
    Cf.: https://www.geeksforgeeks.org/python/django-redirect-to-previous-page-after-login/

    Args:
        request (HttpRequest): The current HTTP request.
        default (str | None): Fallback URL when ``next`` is absent or unsafe.

    Returns:
        str: A safe redirect destination, or ``default`` (or ``"/"`` if
        ``default`` is also ``None``).
    """
    # The first hop from login_required() arrives as GET `?next=...`; after
    # the login/register form posts back, the same value is typically carried
    # forward in a hidden POST field so it survives validation round-trips.
    # This is the same pattern described by Mele for Django's login form and
    # echoed in the GeeksforGeeks walkthrough of redirecting to the previous
    # page after a successful login.
    next_url = request.GET.get("next") or request.POST.get("next", "")
    safe_next = _coerce_safe_local_url(request, next_url)
    if safe_next:
        return safe_next
    return default or "/"


def get_safe_referrer_url(request, default=None):
    """Return a safe local URL derived from the request's ``Referer`` header."""
    referrer = request.META.get("HTTP_REFERER", "")
    safe_referrer = _coerce_safe_local_url(request, referrer)
    if safe_referrer:
        return safe_referrer
    return default or "/"


def get_safe_redirect_url(request, default=None):
    """Prefer a safe ``next`` value, otherwise fall back to a safe referrer."""
    next_url = request.GET.get("next") or request.POST.get("next", "")
    safe_next = _coerce_safe_local_url(request, next_url)
    if safe_next:
        return safe_next
    return get_safe_referrer_url(request, default=default)


# =============================================================================
# DERIVED REQUIREMENT - Provide resilient email delivery with fallback/logging
# so core flows such as password recovery and subscriber notification degrade
# safely instead of failing silently or crashing the request.
# =============================================================================
# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def _build_email_message(
    *,
    subject,
    body,
    recipient_list,
    from_email=None,
    html_body=None,
    connection=None,
):
    """
    Build an outbound email message for the configured recipient list.
    """
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        to=recipient_list,
        connection=connection,
    )
    if html_body:
        message.attach_alternative(html_body, "text/html")
    return message


def _send_via_backend(
    *,
    subject,
    body,
    recipient_list,
    from_email=None,
    html_body=None,
    backend=None,
):
    """Send an email via the supplied backend or Django's default."""
    # get_connection(backend=...) opens a connection to the named backend class.
    # fail_silently=False ensures exceptions propagate to the caller rather than
    # being swallowed, so the shared fallback logic can detect and handle
    # failures consistently across all email flows.
    connection = (
        get_connection(backend=backend, fail_silently=False)
        if backend else None
    )
    _build_email_message(
        subject=subject,
        body=body,
        recipient_list=recipient_list,
        from_email=from_email,
        html_body=html_body,
        connection=connection,
    ).send(
        fail_silently=False
    )


def _print_email_to_console(*, recipient_list, subject, body, console_heading):
    """Print a plain-text email body for console-like fallback backends."""
    sep = "=" * 60
    print(
        f"\n{sep}\n"
        f"{console_heading} (console fallback - no live email delivery)\n"
        f"To: {', '.join(recipient_list)}\n"
        f"{sep}\n"
        f"Subject: {subject}\n\n"
        f"{body}\n"
        f"{sep}\n"
    )


def _format_log_context(*, log_context):
    """Return a human-readable suffix for optional structured log context."""
    return f" ({log_context})" if log_context else ""


def send_email_with_fallback(
    *,
    subject,
    body,
    recipient_list,
    from_email=None,
    html_body=None,
    description="email",
    console_heading="EMAIL",
    log_context="",
):
    """
    Send an email through Django's configured backend with optional fallback.

    The primary backend is always tried first. If that delivery fails and
    ``EMAIL_FALLBACK_ENABLED`` / ``EMAIL_FALLBACK_BACKEND`` are configured,
    the helper retries through the fallback backend. Console-like backends
    receive a simplified plain-text printout so local development remains easy
    to inspect even when no SMTP server is running.

    Args:
        subject (str): Rendered subject line.
        body (str): Plain-text message body.
        recipient_list (Iterable[str]): One or more recipient addresses.
        from_email (str | None): Optional sender override.
        html_body (str | None): Optional HTML alternative body.
        description (str): Human-readable label used in log messages.
        console_heading (str): Heading printed for console-like fallbacks.
        log_context (str): Optional short identifier such as ``user_id=5``.

    Returns:
        bool: ``True`` if primary or fallback delivery succeeded;
        ``False`` if all attempts failed or no recipients were supplied.
    """
    recipients = [email for email in recipient_list if email]
    if not recipients:
        return False

    context_suffix = _format_log_context(log_context=log_context)

    # --- Primary attempt ---
    try:
        _send_via_backend(
            subject=subject,
            body=body,
            recipient_list=recipients,
            from_email=from_email,
            html_body=html_body,
        )
        return True
    except _EMAIL_DELIVERY_ERRORS:
        logger.warning(
            "Failed to send %s via primary backend=%s%s.",
            description,
            settings.EMAIL_BACKEND,
            context_suffix,
            exc_info=True,
        )

    # --- Fallback attempt ---
    fallback_enabled = getattr(settings, "EMAIL_FALLBACK_ENABLED", False)
    fallback_backend = getattr(settings, "EMAIL_FALLBACK_BACKEND", "").strip()

    # Skip fallback if it is disabled, missing, or would just retry the same
    # backend that already failed.
    if (
        not fallback_enabled
        or not fallback_backend
        or fallback_backend == settings.EMAIL_BACKEND
    ):
        return False

    try:
        if fallback_backend in _CONSOLE_LIKE_BACKENDS:
            _print_email_to_console(
                recipient_list=recipients,
                subject=subject,
                body=body,
                console_heading=console_heading,
            )
        else:
            _send_via_backend(
                subject=subject,
                body=body,
                recipient_list=recipients,
                from_email=from_email,
                html_body=html_body,
                backend=fallback_backend,
            )
    except _EMAIL_DELIVERY_ERRORS:
        logger.warning(
            "Fallback %s delivery also failed using backend=%s%s.",
            description,
            fallback_backend,
            context_suffix,
            exc_info=True,
        )
        return False

    logger.warning(
        "Primary %s delivery failed%s. Message copied to fallback backend=%s instead.",
        description,
        context_suffix,
        fallback_backend,
    )
    return True


def send_password_reset_email(
    *,
    user,
    subject,
    body,
    to_email,
    from_email=None,
    html_body=None,
):
    """
    Send a password reset email rendered by Django's built-in reset form and
    optionally fall back to a development backend.

    The primary backend is tried first.  On failure, the optional
    ``EMAIL_FALLBACK_ENABLED`` / ``EMAIL_FALLBACK_BACKEND`` settings are
    respected so developers can still see the rendered reset message in the
    console
    even when no SMTP server is running.

    Args:
        user (User): The ``User`` instance requesting a password reset.
        subject (str): Rendered subject line from the password-reset subject
            template.
        body (str): Rendered plain-text body from the password-reset email
            template.
        to_email (str): Recipient address for the reset email.
        from_email (str | None): Optional override for the sender address.
        html_body (str | None): Optional rendered HTML alternative.

    Returns:
        bool: ``True`` if the email (or console fallback) was delivered
        successfully; ``False`` if all delivery attempts failed.
    """
    return send_email_with_fallback(
        subject=subject,
        body=body,
        recipient_list=[to_email],
        from_email=from_email,
        html_body=html_body,
        description="password reset email",
        console_heading="PASSWORD RESET",
        log_context=f"user_id={user.pk}",
    )
