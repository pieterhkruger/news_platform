"""
Profile and publisher-management views for The Daily Indaba.

Internal helper:
- _build_publisher_page_context: Builds the shared template context
  for both the public profile and owner dashboard publisher views.

Publisher views:
- publisher_profile: Public publisher profile showing approved
  articles and subscription controls.
- publisher_dashboard: Publisher account's management dashboard
  (owner only); reuses the profile template with management controls.
- publisher_manage_editors: Publisher assigns or removes affiliated
  editors (POST only; owner only).

Journalist views:
- journalist_profile: Journalist's public profile with their approved
  articles and a subscribe toggle for readers.
- journalist_list: Public alphabetical directory of all journalists.

Public directories:
- publisher_list: Public alphabetical directory of all publishers.
"""

# Flash messages explain the result of publisher-management actions.
from django.contrib import messages
# Publisher dashboards are available only to signed-in accounts.
from django.contrib.auth.decorators import login_required
# Shortcuts for loading profile rows, redirecting, and rendering templates.
from django.shortcuts import get_object_or_404, redirect, render

# Forms used when managing publisher editors and pricing.
from ..forms import PublisherEditorAssignmentForm, PublisherFeeForm
# Publisher and Subscription power the public/source profile pages.
from ..models import Publisher, Subscription
# Shared access helpers keep role checks and ownership rules consistent.
from .helpers import (
    User,
    _publisher_account_owns_publisher,
    _require_role,
    _user_can_manage_publisher_settings,
)


def _build_publisher_page_context(request, publisher, *, is_dashboard=False):
    """Return the template context shared by public and owner publisher views."""
    # ----------------------------------------------------------------------
    # Collect the publisher's public article data:
    # ----------------------------------------------------------------------
    # Show only approved articles on both the public profile and owner dashboard.
    articles = (
        # Build a QuerySet from the publisher -> articles reverse relation.
        publisher.articles
        # Add WHERE approved = true so unpublished articles stay hidden.
        .filter(approved=True)
        # Tell Django to join the author table up front for efficiency.
        .select_related('author')
        # Add ORDER BY importance ASC, publication_date DESC.
        .order_by('importance', '-publication_date')
    )
    # ----------------------------------------------------------------------
    # Determine the current reader's subscription state:
    # ----------------------------------------------------------------------
    # Readers can subscribe to a publisher, so expose the current subscription state.
    is_subscribed = False
    if request.user.is_authenticated and request.user.role == 'reader':
        # Build a queryset with WHERE reader_id = request.user.pk AND
        # publisher_id = publisher.pk, then determine whether a matching
        # subscription row exists.
        is_subscribed = Subscription.objects.filter(
            reader=request.user,
            publisher=publisher,
        ).exists()

    # ----------------------------------------------------------------------
    # Determine which management controls should be shown:
    # ----------------------------------------------------------------------
    # Determine which management controls the current signed-in user should see.
    can_manage_editors = _publisher_account_owns_publisher(
        request.user,
        publisher,
    )
    can_manage_settings = _user_can_manage_publisher_settings(
        request.user,
        publisher,
    )
    # Only owners/editors who may manage pricing get the fee form in context.
    fee_form = None
    if can_manage_settings:
        fee_form = PublisherFeeForm(
            initial={'monthly_fee': publisher.monthly_fee}
        )

    # Only the publisher account owner may change editor assignments.
    editor_assignment_form = None
    if can_manage_editors:
        editor_assignment_form = PublisherEditorAssignmentForm(
            initial={
                # Add ORDER BY first_name ASC, last_name ASC, username ASC for
                # the initial many-to-many editor selection list.
                'editors': publisher.editors.order_by(
                    'first_name',
                    'last_name',
                    'username',
                )
            }
        )

    # ----------------------------------------------------------------------
    # Assemble the shared publisher-page context:
    # ----------------------------------------------------------------------
    # Package all shared template values in one place so both profile views stay aligned.
    return {
        'publisher': publisher,
        'articles': articles,
        'is_subscribed': is_subscribed,
        'fee_form': fee_form,
        # Add ORDER BY first_name ASC, last_name ASC, username ASC for the
        # assigned-editors display list.
        'assigned_editors': publisher.editors.order_by(
            'first_name',
            'last_name',
            'username',
        ),
        'can_manage_editors': can_manage_editors,
        'editor_assignment_form': editor_assignment_form,
        'is_dashboard': is_dashboard,
    }


@login_required
def publisher_profile(request, pk):
    """Display a publisher's public profile and its approved articles."""
    # Load the publisher plus related account/editor rows used by the template.
    publisher = get_object_or_404(
        # Tell Django to join the account table and prefetch the many-to-many
        # editors relation up front for efficiency.
        Publisher.objects.select_related('account').prefetch_related('editors'),
        pk=pk,
    )
    return render(
        request,
        'news/publisher_profile.html',
        _build_publisher_page_context(request, publisher),
    )


@login_required
def publisher_dashboard(request):
    """Display the signed-in publisher account's management dashboard."""
    # Only publisher accounts should reach their management dashboard.
    if _require_role(request, 'publisher'):
        return redirect('news:home')

    # Resolve the publisher record owned by the signed-in publisher account.
    publisher = get_object_or_404(
        # Tell Django to join the account table and prefetch the many-to-many
        # editors relation up front for efficiency.
        Publisher.objects.select_related('account').prefetch_related('editors'),
        account=request.user,
    )
    return render(
        request,
        'news/publisher_profile.html',
        _build_publisher_page_context(
            request,
            publisher,
            is_dashboard=True,
        ),
    )


@login_required
def publisher_manage_editors(request, pk):
    """Allow a publisher account to assign or remove affiliated editors."""
    # Only publisher accounts may change which editors are attached to a publisher.
    if _require_role(request, 'publisher'):
        return redirect('news:home')

    # Restrict the target publisher to the one owned by the current account.
    publisher = get_object_or_404(
        # Prefetch the editors relation so the management template and form can
        # reuse those rows without extra queries.
        Publisher.objects.prefetch_related('editors'),
        pk=pk,
        account=request.user,
    )

    # This endpoint only processes the submitted assignment form.
    if request.method != 'POST':
        return redirect('news:publisher_dashboard')

    # Bind the submitted editor selections and replace the current many-to-many set.
    form = PublisherEditorAssignmentForm(request.POST)
    if form.is_valid():
        publisher.editors.set(form.cleaned_data['editors'])
        messages.success(
            request,
            "Publisher editor assignments updated.",
        )
    else:
        messages.error(
            request,
            "Could not update the editor assignments.",
        )

    return redirect('news:publisher_dashboard')


@login_required
def journalist_profile(request, pk):
    """Display a journalist's profile and their approved articles."""
    # Load the requested journalist account and ensure the role matches.
    journalist = get_object_or_404(
        User,
        pk=pk,
        role='journalist',
    )
    # Public journalist profiles show only approved articles.
    articles = (
        # Build a QuerySet from the journalist -> articles reverse relation.
        journalist.articles
        # Add WHERE approved = true so unpublished articles stay hidden.
        .filter(approved=True)
        # Tell Django to join the publisher table up front for efficiency.
        .select_related('publisher')
        # Add ORDER BY importance ASC, publication_date DESC.
        .order_by('importance', '-publication_date')
    )
    # Readers can subscribe directly to an individual journalist feed.
    is_subscribed = False
    if request.user.role == 'reader':
        # Build a queryset with WHERE reader_id = request.user.pk AND
        # journalist_id = journalist.pk, then determine whether a matching
        # subscription row exists.
        is_subscribed = Subscription.objects.filter(
            reader=request.user,
            journalist=journalist,
        ).exists()

    # Render the journalist bio plus their approved article feed.
    return render(request, 'news/journalist_profile.html', {
        'journalist': journalist,
        'articles': articles,
        'is_subscribed': is_subscribed,
    })


def journalist_list(request):
    """Public directory of all journalists on the platform."""
    # List every journalist alphabetically for the public directory page.
    journalists = (
        User.objects
        # Add WHERE role = 'journalist'.
        .filter(role='journalist')
        # Add ORDER BY first_name ASC, last_name ASC.
        .order_by('first_name', 'last_name')
    )
    return render(request, 'news/journalist_list.html', {
        'journalists': journalists,
    })


def publisher_list(request):
    """Public directory of all publishers on the platform."""
    # List all publishers alphabetically for the public directory page.
    # Add ORDER BY name ASC.
    publishers = Publisher.objects.order_by('name')
    return render(request, 'news/publisher_list.html', {
        'publishers': publishers,
    })
