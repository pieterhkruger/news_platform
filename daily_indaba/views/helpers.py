"""
Shared helpers, constants, and utilities used across view modules.

Constants:
- SITE_DESCRIPTION: One-line platform tagline used on the home page.
- IMPORTANCE_LABELS: Human-readable labels for article importance levels.

Role-gate helpers:
- _require_role: Redirect unauthorized or unauthenticated users; returns
  False when access is allowed.
- _user_has_full_access: Return True when an authenticated user may read
  the full article body (staff role, all-articles plan, or active sub).

Email helpers:
- _notify_subscribers: Email all relevant subscribers when an article
  is approved; delivery failures are logged but never propagated.
- _send_subscription_confirmation: Email a reader when they create a
  new publisher or journalist subscription.

Text helpers:
- _first_sentence: Extract the first sentence of article content as
  plain text (max 250 chars) for use as a card teaser.

Form/queryset helpers:
- _limit_publisher_choices: Restrict the publisher dropdown to the
  journalist's affiliated publishers.
- _editor_can_curate_independent_articles: Return True when the editor
  is affiliated with an independent-desk publisher.
- _filter_articles_for_editor: Scope an article queryset to the
  editor's assigned publisher range.
- _editor_can_manage_article: Return True when the editor may curate
  the given article.
- _publisher_account_owns_publisher: Return True when the signed-in
  publisher account owns the given publisher.
- _user_can_manage_publisher_settings: Return True when the user may
  manage a publisher's fee and settings.
"""

# imported so that html.unescape() can be used to convert any
#   HTML entities (e.g. &amp; -> &) back to plain text.
import html

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
# To get the active custom user model from Django settings.
from django.contrib.auth import get_user_model  # Cf. Mele (2025:372)
from django.shortcuts import redirect
from django.urls import reverse
# strip_tags() removes all HTML/XML tags from a string, leaving plain text.
# Used together with html.unescape() to produce safe article teasers.
from django.utils.html import strip_tags

from accounts.utils import send_email_with_fallback

from ..models import Article, Subscription

# Cf. Mele (2025:373). get_user_model() returns the custom user model used
# here to distinguish between reader, journalist, and editor accounts.
User = get_user_model()

# ---------------------------------------------------------------------------
# Site-wide constants
# ---------------------------------------------------------------------------

SITE_DESCRIPTION = (
    "The Daily Indaba is South Africa's independent digital news "
    "platform — connecting readers, journalists, and publishers "
    "through honest, fearless reporting."
)

IMPORTANCE_LABELS = [
    (Article.FRONT_PAGE, "Front Page"),
    (Article.TOP_STORY, "Top Story"),
    (Article.STANDARD, "Standard"),
]


# ---------------------------------------------------------------------------
# Role-gate helpers
# ---------------------------------------------------------------------------

# =============================================================================
# DERIVED REQUIREMENT - Enforce role-based access in the views themselves, so
# users cannot bypass permissions by manually entering or guessing URLs that
# may be hidden in the templates.
# =============================================================================

def _require_role(request, role):
    """Return True if the user has *role*, otherwise flash and redirect.

    Usage::

        if _require_role(request, 'journalist'):
            return redirect('news:home')

    Returns ``True`` when access should be denied (caller must return).
    Returns ``False`` when access is allowed.
    """
    # Unauthenticated users cannot enter the protected view, so redirect them
    # to the configured login page:
    if not request.user.is_authenticated:
        return redirect(settings.LOGIN_URL)
    # Authenticated users whose role does not match the required role are
    # denied access and shown an explanatory flash message.
    if request.user.role != role:
        messages.error(
            request,
            f"This area is restricted to {role}s.",
        )
        return redirect('news:home')
    # Returning False tells the caller that the user passed the role check and
    # the protected view logic may continue.
    return False


def _user_has_full_access(user, article):
    """Return True if the user may read the full article body.

    This helper expects an authenticated user.

    Full access is granted when ANY of the following hold:

    * The user is a journalist, editor, or publisher account.
    * The user has the all-articles flat-rate plan active.
    * The user has an active :class:`Subscription` targeting the article's
      author (journalist).
    * The user has an active :class:`Subscription` targeting the article's
      publisher (when the article belongs to a publisher).

    Returns ``False`` for authenticated readers with no qualifying
    subscription.
    """
    if not user.is_authenticated:
        # Developer-facing invariant: this helper should only be called with
        # an authenticated user.
        assert False, (
            "_user_has_full_access() should only be called "
            "for authenticated users."
        )
    # Journalists, Editors and Publishers have full access to articles:
    if user.role in ("journalist", "editor", "publisher"):
        return True
    # Readers on the "all articles" plan have full access to articles:
    if getattr(user, "all_articles_plan", False):
        return True
    # Readers subscribed to the current journalist have access to this article:
    if Subscription.objects.filter(   # Build a queryset with WHERE
        reader=user,                  # Subscription.reader_id = user.pk AND
        # Subscription.journalist_id = article.author_id:
        journalist=article.author
    ).exists():  # Determine if there is at least one matching subscription.
        return True
    # Readers subscribed to the article's publisher have access when the
    # article belongs to a publisher:
    if (
        article.publisher_id and
        Subscription.objects.filter(  # Build a queryset with WHERE
            reader=user,              # reader_id=user.pk AND
            # Subscription.publisher_id=article.publisher_id:
            publisher_id=article.publisher_id
        ).exists()  # Determine if there is at least one matching subscription.
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

# =============================================================================
# CORE REQUIREMENT - When an editor approves an article, notify subscribers as
# part of the article-approval flow.
# =============================================================================


# =============================================================================
# DERIVED REQUIREMENT - Keep article-approval recipient selection in one helper
# so subscriber emails and in-app approval notifications cannot drift apart on
# which subscribed readers should be informed.
# =============================================================================
def _article_approval_recipient_ids(article, *, include_author=False):
    """Return unique user IDs to inform when *article* is approved.

    Recipients always include readers subscribed to the article's publisher
    (if applicable) and readers subscribed to the article's author directly.
    When ``include_author`` is True, the article's author is added as well so
    the in-app announcement queue can include the journalist who wrote the
    article.
    """
    recipient_ids = set()  # Use a set for user IDs to avoid duplicates.

    if include_author:
        # The journalist who wrote the article may be included for the in-app
        # approval-notification flow.
        recipient_ids.add(article.author_id)

    # ----------------------------------------------------------------------
    # Determine which readers should be notified because they subscribe to the
    # article's PUBLISHER:
    # ----------------------------------------------------------------------
    if article.publisher_id:
        # Find subscriptions to this article's publisher:
        publisher_subscriber_ids = Subscription.objects.filter(
            # Subscription.publisher_id = article.publisher_id
            publisher_id=article.publisher_id
            # Return only the selected field, not full Subscription objects:
        ).values_list(
            "reader_id",  # Read the subscriber IDs from the matching rows.
            flat=True,    # Return a flat sequence of IDs instead of 1-tuples.
        )
        # Developer's note:
        # 'reader_id' is the actual database column name for the reader (=user)
        # ForeignKey on the Subscription model (Django automatically appends
        # _id to FK fields). Cf.:
        # https://docs.djangoproject.com/en/5.2/ref/models/querysets/#values-list

        # Append publisher subscribers to the set of recipients:
        recipient_ids.update(publisher_subscriber_ids)

    # ----------------------------------------------------------------------
    # Determine which readers should be notified because they subscribe to the
    # article's JOURNALIST:
    # ----------------------------------------------------------------------
    author_subscriber_ids = Subscription.objects.filter(
        # Subscription.journalist_id = article.author_id:
        journalist_id=article.author_id
        # Return only the selected field instead of full Subscription objects:
    ).values_list(
        "reader_id",  # Read the subscriber IDs from the matching rows.
        flat=True,    # Return a flat sequence of IDs instead of 1-tuples.
    )
    # Append journalist subscribers to the set of recipients:
    recipient_ids.update(author_subscriber_ids)

    return recipient_ids


def _notify_subscribers(article):
    """Email all relevant subscribers when an article is approved.

    Collects unique subscriber emails from:
    - Subscriptions to the article's publisher (if applicable).
    - Subscriptions to the article's author.

    Email failures are logged but never propagate - article approval
    must not be rolled back on delivery failure.
    """
    # Reuse the shared recipient-selection helper so the email flow and the
    # in-app notification flow stay aligned on which subscribed readers are
    # informed about article approval.
    subscriber_ids = _article_approval_recipient_ids(
        article,
        include_author=False,
    )

    if not subscriber_ids:
        return

    # ----------------------------------------------------------------------
    # Obtain the email addresses of the readers who should be notified:
    # ----------------------------------------------------------------------
    recipients = list(
        # Build a queryset with WHERE user ID is in the list of subscriber IDs
        User.objects.filter(id__in=subscriber_ids)
        # Return only a flat sequence of email addresses:
        .values_list('email', flat=True)
    )

    # ----------------------------------------------------------------------
    # Build the publication email:
    # ----------------------------------------------------------------------
    author_name = article.author.public_name
    base_url = getattr(
        settings, 'SITE_BASE_URL', 'http://localhost:8000'
    ).rstrip('/')
    article_url = (
        f"{base_url}"
        f"{reverse('news:article_detail', kwargs={'pk': article.pk})}"
    )
    subject = f"New article: {article.title}"
    body = (
        f"{author_name} has published a new article on "
        f"The Daily Indaba:\n\n"
        f"{article.title}\n\n"
        f"{article.content}...\n\n"
        f"Read the full article on The Daily Indaba:\n{article_url}"
    )

    # ----------------------------------------------------------------------
    # Send the publication email to those readers:
    # ----------------------------------------------------------------------
    send_email_with_fallback(
        subject=subject,
        body=body,
        recipient_list=recipients,
        from_email=settings.DEFAULT_FROM_EMAIL,
        description="subscriber notification email",
        console_heading="ARTICLE APPROVAL",
        log_context=f"article_id={article.pk}",
    )


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _first_sentence(text):
    """Return the first sentence of *text* as plain text, up to 250 chars."""
    plain_text = html.unescape(strip_tags(text))
    for sep in ('. ', '! ', '? ', '.\n', '!\n', '?\n'):
        idx = plain_text.find(sep)
        if idx != -1:
            return plain_text[:idx + 1]
    return plain_text[:250] if len(plain_text) > 250 else plain_text


# ---------------------------------------------------------------------------
# Form helpers
# ---------------------------------------------------------------------------


def _limit_publisher_choices(form, user):
    """Restrict the publisher dropdown to the user's affiliated publishers.

    For editors the full publisher list is shown.
    """
    if user.role == 'journalist':
        # Return the journalist's affiliated publishers through the reverse
        # many-to-many relation.
        form.fields['publisher'].queryset = (
            user.journalist_publishers.all()
        )


def _editor_can_curate_independent_articles(user):
    """Return True when the editor is affiliated with an independent desk."""
    return (
        user.is_authenticated
        and user.role == "editor"
        # Build a queryset with WHERE curates_independent_journalists = true,
        # then determine whether at least one affiliated publisher row matches.
        and user.editor_publishers.filter(
            curates_independent_journalists=True
        ).exists()
    )


def _filter_articles_for_editor(queryset, user):
    """Restrict an article queryset to the editor's publisher scope."""
    if not user.is_authenticated or user.role != "editor":
        return queryset.none()

    publisher_ids = list(
        # Return only the editor's affiliated publisher primary-key values.
        user.editor_publishers.values_list("pk", flat=True)
    )
    # Build a Q object equivalent to WHERE publisher_id IN publisher_ids.
    scope = Q(publisher_id__in=publisher_ids)
    if _editor_can_curate_independent_articles(user):
        # Extend the scope with OR publisher_id IS NULL for independent desks.
        scope |= Q(publisher__isnull=True)
    # Apply the combined editor-scope filter to the supplied article queryset.
    return queryset.filter(scope)


def _editor_can_manage_article(user, article):
    """Return True when *user* may curate the supplied article."""
    if not user.is_authenticated or user.role != "editor":
        return False
    if article.publisher_id:
        # Build a queryset with WHERE pk = article.publisher_id, then determine
        # whether this editor is affiliated with that publisher.
        return user.editor_publishers.filter(pk=article.publisher_id).exists()
    return _editor_can_curate_independent_articles(user)


def _publisher_account_owns_publisher(user, publisher):
    """Return True when the signed-in publisher account owns *publisher*."""
    return (
        user.is_authenticated
        and user.role == "publisher"
        and publisher.account_id == user.pk
    )


def _user_can_manage_publisher_settings(user, publisher):
    """Return True when the user may manage the publisher's settings."""
    if _publisher_account_owns_publisher(user, publisher):
        return True
    return (
        user.is_authenticated
        and user.role == "editor"
        # Build a queryset with WHERE pk = user.pk, then determine whether the
        # editor is attached to this publisher's many-to-many editors relation.
        and publisher.editors.filter(pk=user.pk).exists()
    )


def _send_subscription_confirmation(user, *, publisher=None, journalist=None):
    # Developer's note: the bare * after user makes publisher and journalist
    # keyword-only arguments.  The caller cannot pass them positionally, which
    # prevents accidentally swapping the two optional arguments.
    # Cf.: https://docs.python.org/3/glossary.html#term-parameter
    """Send a confirmation email when a reader creates a new subscription.

    Exactly one of ``publisher`` or ``journalist`` must be supplied.
    Email failures are logged but never propagate - subscription must not
    be rolled back on delivery failure.
    """
    if publisher:
        subject = f"Subscription confirmed: {publisher.name}"
        fee = publisher.monthly_fee
        target_name = publisher.name
        fee_label = f"R{fee}/month (publisher subscription)"
    elif journalist:
        subject = f"Subscription confirmed: {journalist.public_name}"
        fee = journalist.journalist_monthly_fee
        target_name = journalist.public_name
        fee_label = f"R{fee}/month (journalist subscription)"
    else:
        return

    if not user.email:
        return

    body = (
        f"Hi {user.public_name},\n\n"
        f"You are now subscribed to {target_name} on The Daily Indaba.\n\n"
        f"Subscription fee: {fee_label}\n\n"
        f"You will receive an email whenever a new article is published "
        f"by this source.\n\n"
        f"Manage your subscriptions at any time from your account page.\n\n"
        f"— The Daily Indaba"
    )

    send_email_with_fallback(
        subject=subject,
        body=body,
        recipient_list=[user.email],
        from_email=settings.DEFAULT_FROM_EMAIL,
        description="subscription confirmation email",
        console_heading="SUBSCRIPTION CONFIRMATION",
        log_context=f"user_id={user.pk}",
    )
