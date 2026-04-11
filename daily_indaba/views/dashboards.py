"""
Dashboard and subscription views for The Daily Indaba.

Role dashboards:
- journalist_dashboard: Journalist's personal desk showing their
  articles, newsletters, and subscription fee form.
- editor_dashboard: Editor overview with pending approval queue
  summary and recent approvals.
- approval_queue: Full pending article queue for editors, with
  inline rejection form.

Subscription views (POST only, reader role):
- toggle_subscription: Subscribe or unsubscribe a reader to/from a
  publisher or journalist; sends confirmation email on new sub.
- subscribe_all_articles: Toggle the reader's flat-rate all-articles
  plan; sends confirmation email on activation.

Fee management views (POST only):
- set_journalist_fee: Journalist updates their monthly reader
  subscription fee within the policy-defined range.
- publisher_set_fee: Publisher owner or affiliated editor updates the
  publisher's monthly subscription fee.
"""

# Flash messages report the outcome of dashboard actions after redirects.
from django.contrib import messages
from django.core.exceptions import ValidationError
# Dashboard, pricing, and subscription actions require an authenticated user.
from django.contrib.auth.decorators import login_required
# Shortcuts for fetching database rows, redirecting, and rendering templates.
from django.shortcuts import get_object_or_404, redirect, render
# reverse() builds named URLs for safe post-action redirects.
from django.urls import reverse
# State-changing dashboard actions should only accept POST requests.
from django.views.decorators.http import require_POST

# Pricing helpers expose the configured fee rules used by dashboard forms.
from accounts.models import (
    get_all_articles_monthly_fee,
    get_journalist_fee_bounds,
    get_publisher_fee_bounds,
)
# Shared helpers centralise safe redirects and email delivery fallbacks.
from accounts.utils import (
    get_safe_redirect_url,
    send_email_with_fallback,
)

# Forms validate fee updates and article rejection reasons.
from ..forms import JournalistFeeForm, PublisherFeeForm, RejectionForm
# Core models used by the journalist/editor/publisher dashboards.
from ..models import Article, Newsletter, Publisher, Subscription
# View helpers keep role checks, article scoping, and subscription email
# behaviour consistent across dashboard endpoints.
from .helpers import (
    User,
    _filter_articles_for_editor,
    _user_can_manage_publisher_settings,
    _require_role,
    _send_subscription_confirmation,
)


# =============================================================================
# CORE REQUIREMENT - Journalist/editor operational screens that surface the
# article/newsletter work queues behind the brief's create/review/approve
# workflows.
# =============================================================================
@login_required
def journalist_dashboard(request):
    """Render a journalist's personal desk (articles + newsletters)."""
    # Only journalists may open their own writing dashboard.
    if _require_role(request, 'journalist'):
        return redirect('news:home')

    # Load the journalist's articles newest-first for the dashboard table.
    my_articles = (
        Article.objects
        # Add WHERE author_id = request.user.pk.
        .filter(author=request.user)
        # Tell Django to join the publisher table up front for efficiency.
        .select_related('publisher')
        # Add ORDER BY created_at DESC.
        .order_by('-created_at')
    )
    # Load the journalist's newsletters for the second dashboard panel.
    my_newsletters = (
        Newsletter.objects
        # Add WHERE author_id = request.user.pk.
        .filter(author=request.user)
        # Tell Django to join the category table up front for efficiency.
        .select_related('category')
        # Add ORDER BY created_at DESC.
        .order_by('-created_at')
    )
    # Pre-fill the fee form with the journalist's current subscription price.
    fee_form = JournalistFeeForm(
        initial={'journalist_monthly_fee': request.user.journalist_monthly_fee}
    )
    return render(request, 'news/journalist_dashboard.html', {
        'my_articles': my_articles,
        'my_newsletters': my_newsletters,
        'fee_form': fee_form,
    })


@login_required
def editor_dashboard(request):
    """Render the editor overview dashboard with approval queue summary."""
    # Only editors may access the editorial dashboard.
    if _require_role(request, 'editor'):
        return redirect('news:home')

    # Show the oldest pending articles first so review happens in queue order.
    pending = (
        _filter_articles_for_editor(
            # Tell Django to join the author and publisher tables up front for
            # efficiency before the editor-scope filter is applied.
            Article.objects.select_related('author', 'publisher'),
            request.user,
        )
        # Add WHERE status = pending.
        .filter(status=Article.STATUS_PENDING)
        # Add ORDER BY created_at ASC.
        .order_by('created_at')
    )
    # Show the latest published items as a quick editorial activity feed.
    recent = (
        _filter_articles_for_editor(
            # Tell Django to join these related tables up front for efficiency
            # before the editor-scope filter is applied.
            Article.objects.select_related('author', 'publisher', 'approved_by'),
            request.user,
        )
        # Add WHERE approved = true.
        .filter(approved=True)
        # Add ORDER BY publication_date DESC, then LIMIT 10.
        .order_by('-publication_date')[:10]
    )
    return render(request, 'news/editor_dashboard.html', {
        'pending': pending,
        'recent': recent,
    })


@login_required
def approval_queue(request):
    """Render the full article approval queue (editor only)."""
    # Only editors may review pending submissions.
    if _require_role(request, 'editor'):
        return redirect('news:home')

    # Pull all still-unapproved articles with enough related data for the template.
    pending = (
        _filter_articles_for_editor(
            # Tell Django to join the author and publisher tables up front for
            # efficiency before the editor-scope filter is applied.
            Article.objects.select_related('author', 'publisher'),
            request.user,
        )
        # Add WHERE status = pending.
        .filter(status=Article.STATUS_PENDING)
        # Add ORDER BY created_at ASC.
        .order_by('created_at')
    )
    # Create one blank rejection form that queue rows can post back.
    rejection_form = RejectionForm()
    return render(request, 'news/approval_queue.html', {
        'pending': pending,
        'rejection_form': rejection_form,
    })


@login_required
@require_POST
def toggle_subscription(request):
    """Toggle a reader's subscription to a publisher or journalist.

    Expects POST fields ``type`` (``publisher`` | ``journalist``) and
    ``id`` (primary key of the target).  On a new subscription a
    confirmation email is sent to the reader.  Redirects back to the
    referring page (or home if no referrer is set).
    """
    # Only readers may subscribe to publishers and journalists.
    if _require_role(request, 'reader'):
        return redirect('news:home')

    # ----------------------------------------------------------------------
    # Read the requested subscription action:
    # ----------------------------------------------------------------------
    # Read the target type and primary key from the submitted button form.
    sub_type = request.POST.get('type', '')
    target_id = request.POST.get('id', '')
    # Resolve a safe in-project redirect URL for after the toggle completes.
    next_url = get_safe_redirect_url(
        request,
        default=reverse('accounts:subscriptions'),
    )

    # ----------------------------------------------------------------------
    # Handle subscriptions to publishers:
    # ----------------------------------------------------------------------
    if sub_type == 'publisher':
        # Load the publisher being subscribed to or unsubscribed from.
        publisher = get_object_or_404(Publisher, pk=target_id)
        # Build a queryset with WHERE reader_id = request.user.pk AND
        # publisher_id = publisher.pk AND journalist_id IS NULL. Then fetch the
        # matching row or create it if none exists.
        sub, created = Subscription.objects.get_or_create(
            reader=request.user,
            publisher=publisher,
            journalist=None,
        )
        if created:
            # New subscriptions get a confirmation email and success message.
            _send_subscription_confirmation(request.user, publisher=publisher)
            messages.success(
                request, f"Subscribed to {publisher.name}."
            )
        else:
            # Clicking again toggles the existing subscription off.
            sub.delete()
            messages.success(
                request, f"Unsubscribed from {publisher.name}."
            )

    # ----------------------------------------------------------------------
    # Handle subscriptions to journalists:
    # ----------------------------------------------------------------------
    elif sub_type == 'journalist':
        # Load the journalist while also enforcing the correct role.
        journalist = get_object_or_404(
            User, pk=target_id, role='journalist'
        )
        # Build a queryset with WHERE reader_id = request.user.pk AND
        # journalist_id = journalist.pk AND publisher_id IS NULL. Then fetch
        # the matching row or create it if none exists.
        sub, created = Subscription.objects.get_or_create(
            reader=request.user,
            journalist=journalist,
            publisher=None,
        )
        if created:
            # New journalist subscriptions follow the same confirmation flow.
            _send_subscription_confirmation(
                request.user, journalist=journalist
            )
            messages.success(
                request,
                f"Subscribed to {journalist.public_name}.",
            )
        else:
            # A repeat click removes the journalist subscription.
            sub.delete()
            messages.success(
                request,
                f"Unsubscribed from {journalist.public_name}.",
            )
    else:
        # Reject any malformed subscription payload rather than guessing.
        messages.error(request, "Invalid subscription request.")

    # ----------------------------------------------------------------------
    # Return the reader to the originating page:
    # ----------------------------------------------------------------------
    # Return the reader to the page that initiated the subscription action.
    return redirect(next_url)


@login_required
@require_POST
def subscribe_all_articles(request):
    """Toggle the reader's configured all-articles flat-rate plan.

    Flips :attr:`~accounts.models.User.all_articles_plan` and saves.
    When activated, a confirmation email is sent to the reader.
    Redirects to the subscriptions management page.
    Only readers may access this view.
    """
    # Only readers may toggle the platform-wide flat-rate plan.
    if _require_role(request, 'reader'):
        return redirect('news:home')

    # Flip the boolean flag and persist only that one changed field.
    user = request.user
    user.all_articles_plan = not user.all_articles_plan
    user.save(update_fields=['all_articles_plan'])

    if user.all_articles_plan:
        all_articles_fee = get_all_articles_monthly_fee()
        # Send the plan-activation email only when the plan is turned on.
        if user.email:
            send_email_with_fallback(
                subject="All-Articles plan activated — The Daily Indaba",
                body=(
                    f"Hi {user.public_name},\n\n"
                    "Your All-Articles plan is now active.\n\n"
                    f"Fee: R{all_articles_fee}/month — full access to all articles from "
                    "all journalists and publishers on "
                    "The Daily Indaba.\n\n"
                    "Manage your subscriptions at any time "
                    "from your account page.\n\n"
                    "— The Daily Indaba"
                ),
                recipient_list=[user.email],
                description="all-articles plan confirmation email",
                console_heading="ALL-ARTICLES PLAN",
                log_context=f"user_id={user.pk}",
            )
        messages.success(
            request,
            f"All-Articles plan activated (R{all_articles_fee}/month). "
            "You now have full access to all articles.",
        )
    else:
        # A second click simply cancels the plan.
        messages.success(request, "All-Articles plan cancelled.")

    return redirect(reverse('accounts:subscriptions'))


@login_required
@require_POST
def set_journalist_fee(request):
    """Allow a journalist to set their configured monthly subscription fee.

    Validates the submitted fee and updates
    :attr:`~accounts.models.User.journalist_monthly_fee` on the user.
    Redirects back to the journalist dashboard.
    """
    # Only journalists may set their own monthly reader subscription fee.
    if _require_role(request, 'journalist'):
        return redirect('news:home')

    # Bind the submitted fee to the dedicated validation form.
    form = JournalistFeeForm(request.POST)
    if form.is_valid():
        # Save the validated fee directly onto the authenticated user record.
        fee = form.cleaned_data['journalist_monthly_fee']
        request.user.journalist_monthly_fee = fee
        request.user.save(update_fields=['journalist_monthly_fee'])
        messages.success(
            request,
            f"Your subscription fee has been updated to R{fee}/month.",
        )
    else:
        minimum, _, maximum = get_journalist_fee_bounds()
        # Invalid values fall back to the documented valid range message.
        messages.error(
            request,
            (
                "Invalid fee. Please enter a value between "
                f"R{minimum:.2f} and R{maximum:.2f}."
            ),
        )

    return redirect('news:journalist_dashboard')


@login_required
@require_POST
def publisher_set_fee(request, pk):
    """Allow a publisher owner or affiliated editor to set the fee.

    Valid range comes from the seeded pricing policy.
    Redirects back to the publisher's profile page.
    """
    # Load the publisher whose subscription fee is being updated.
    publisher = get_object_or_404(Publisher, pk=pk)

    # Publisher owners and affiliated editors may change the fee.
    if not _user_can_manage_publisher_settings(request.user, publisher):
        messages.error(
            request,
            "You are not allowed to manage this publisher.",
        )
        return redirect('news:publisher_profile', pk=pk)

    # Bind the posted fee to the publisher-fee form for validation.
    form = PublisherFeeForm(request.POST)
    if form.is_valid():
        # Save only the one field touched by this dashboard action.
        fee = form.cleaned_data['monthly_fee']
        publisher.monthly_fee = fee
        try:
            publisher.save(update_fields=['monthly_fee'])
            messages.success(
                request,
                f"{publisher.name} subscription fee updated to R{fee}/month.",
            )
        except ValidationError as exc:
            minimum, _, maximum = get_publisher_fee_bounds()
            messages.warning(
                request,
                " ".join(exc.messages) or (
                    "Invalid fee. Please enter a value between "
                    f"R{minimum:.2f} and R{maximum:.2f}."
                ),
            )
    else:
        minimum, _, maximum = get_publisher_fee_bounds()
        # Invalid prices are rejected with the documented range.
        messages.warning(
            request,
            (
                "Invalid fee. Please enter a value between "
                f"R{minimum:.2f} and R{maximum:.2f}."
            ),
        )

    return redirect('news:publisher_profile', pk=pk)
