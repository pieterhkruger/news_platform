"""Announcement / notification views for The Daily Indaba."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.utils import get_safe_next_url

from ..models import ArticleNotification


@login_required
def announcement_detail(request, pk):
    """Show an article-approval announcement to its recipient.

    Marks the notification as seen on first view, then renders a
    role-aware page so the user can navigate to the article or their
    normal post-login destination.

    If the user has more unseen notifications after this one, the
    template's "Continue" button links to the next one so they are
    drained one at a time.
    """
    # Only the intended recipient may view this notification.
    notification = get_object_or_404(
        ArticleNotification,
        pk=pk,
        recipient=request.user,
    )

    # Stamp seen_at on first view; no-op if already seen.
    notification.mark_seen()

    article = notification.article
    role = request.user.role

    # Role-aware primary call-to-action.
    if role == "journalist":
        primary_url = reverse(
            "news:article_detail", kwargs={"pk": article.pk}
        )
        primary_label = "View your published article"
        fallback_url = reverse("news:journalist_dashboard")
        fallback_label = "Go to my dashboard"
    else:
        # Readers (and any other role that may receive a notification).
        primary_url = reverse(
            "news:article_detail", kwargs={"pk": article.pk}
        )
        primary_label = "Read the article"
        fallback_url = reverse("news:home")
        fallback_label = "Go to home"

    # Check for additional unseen notifications so the template can offer the
    # next oldest unread notification rather than jumping around the queue.
    next_notification = (
        ArticleNotification.objects
        # Add WHERE recipient_id = request.user.pk AND seen_at IS NULL.
        .filter(recipient=request.user, seen_at__isnull=True)
        .order_by("created_at")  # ORDER BY created_at ASC.
        .first()  # Return the oldest remaining unread row or None.
    )

    if next_notification:
        continue_url = reverse(
            "news:announcement_detail", kwargs={"pk": next_notification.pk}
        )
        continue_label = "See next announcement"
    else:
        continue_url = fallback_url
        continue_label = fallback_label

    return render(request, "news/announcement_detail.html", {
        "notification": notification,
        "article": article,
        "primary_url": primary_url,
        "primary_label": primary_label,
        "continue_url": continue_url,
        "continue_label": continue_label,
    })


@login_required
def dismiss_notification(request, pk):
    """Mark a notification as seen without viewing the announcement page.

    POST-only.  Used by the dismiss button if a user wants to clear a
    notification from a list without navigating away.
    """
    if request.method != "POST":
        return redirect("news:home")

    notification = get_object_or_404(
        ArticleNotification,
        pk=pk,
        recipient=request.user,
    )
    notification.mark_seen()
    # Dismissal is another state-changing POST flow that accepts a browser
    # supplied `next` value, so it uses the shared redirect-safety helper
    # instead of trusting raw POST data.
    return redirect(
        get_safe_next_url(request, default=reverse("news:home"))
    )
