"""
Article views for The Daily Indaba.

Public views:
- home: Home page showing the 8 most recent approved articles with
  teasers; accessible to all visitors.

Authenticated article views:
- article_list: Approved article listing with optional importance
  filter; editors also see pending articles in their scope.
- article_detail: Single article with threaded comments; full content
  gated by subscription or staff role.

Journalist/editor management views:
- article_create: Journalist creates a new article (pending approval).
- article_edit: Journalist edits their own unapproved article, or
  editor edits any article in their scope.
- article_delete: Journalist deletes their own unapproved article, or editor
  deletes any article in their scope; confirmation page on GET.

Editorial workflow views (POST only):
- article_approve: Editor approves a pending article; triggers
  subscriber notification emails.
- article_reject: Editor returns a pending article for revision;
  optionally emails the journalist a rejection reason.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
# Q objects allow complex ORM queries combining conditions with | (OR) and
# & (AND) operators, making it possible to filter on multiple fields in one
# .filter() call.
# Guest et al. illustrate this `Q(... ) | Q(... )` / `Q(... ) & Q(... )`
# pattern as Django's way of expressing SQL-style OR/AND logic in WHERE
# clauses. See: Guest, Chris et al., Web Development with Django 6,
# Packt, pp. 186-188.
# Cf.: https://docs.djangoproject.com/en/5.2/topics/db/queries/
#      #complex-lookups-with-q-objects
from django.shortcuts import get_object_or_404, redirect, render
# @require_POST rejects GET requests with HTTP 405 Method Not Allowed.
from django.views.decorators.http import require_POST

from accounts.models import get_all_articles_monthly_fee
from accounts.utils import send_email_with_fallback

from ..forms import (
    ArticleForm,
    CommentForm,
    RejectionForm,
)
from ..models import Article, Comment
from ..editorial_workflows import (
    publish_article,
    return_article_for_revision,
)
from .helpers import (
    IMPORTANCE_LABELS,
    SITE_DESCRIPTION,
    _editor_can_manage_article,
    _filter_articles_for_editor,
    _first_sentence,
    _user_has_full_access,
    _limit_publisher_choices,
    _require_role,
)

# =============================================================================
# CORE REQUIREMENT - Public-facing approved-article browsing so visitors and
# readers can discover published news content from the site home page onward.
# =============================================================================
# ---------------------------------------------------------------------------
# Public views
# ---------------------------------------------------------------------------


def home(request):
    """Render the public home page.

    Accessible to all visitors (no login required).  Displays a
    one-sentence site description and the eight most recent approved
    articles, each shown with its title and the first sentence of its
    content only.
    """
    # Build a QuerySet for the public homepage articles.
    articles = (
        Article.objects
        # Add WHERE approved = true so only published articles are included.
        .filter(approved=True)
        # Tell Django to join these related tables up front for efficiency.
        .select_related('author', 'publisher', 'category')
        # Add ORDER BY importance ASC, publication_date DESC.
        .order_by('importance', '-publication_date')[:8]
        # Slice the QuerySet so the SQL includes LIMIT 8.
    )
    # Pair each article with its first-sentence teaser for the home cards.
    teasers = [
        {'article': a, 'teaser': _first_sentence(a.content)}
        for a in articles
    ]
    return render(request, 'news/home.html', {
        'site_description': SITE_DESCRIPTION,
        'teasers': teasers,
    })


# ---------------------------------------------------------------------------
# Article views
# ---------------------------------------------------------------------------

# =============================================================================
# CORE REQUIREMENT - Authenticated article listing/detail views so readers can
# view articles and editors/journalists can inspect content appropriate to
# their role and approval state.
# =============================================================================

@login_required
def article_list(request):
    """Render the authenticated article listing page.

    Supports filtering by importance level (GET param ``importance``).
    The left sidebar displays importance-level links mirroring the
    eCommerce category panel.
    """
    # Read the optional importance filter from the query string.
    importance_param = request.GET.get('importance', '').strip()

    # Build the base QuerySet for the article list:
    query_set = Article.objects.select_related(
        # Tell Django to join these related tables up front for efficiency.
        'author', 'publisher', 'category', 'approved_by'
    )
    if request.user.role == 'editor':
        # For editors, include both pending and approved articles they can
        # manage.
        query_set = _filter_articles_for_editor(
            query_set, request.user
            ).order_by(
            # Add ORDER BY approved ASC, importance ASC, publication_date DESC,
            # created_at DESC.
            'approved',
            'importance',
            '-publication_date',
            '-created_at',
        )
    else:
        # For other users, only show approved articles.
        query_set = query_set.filter(
            # Add WHERE approved = true.
            approved=True
        ).order_by(
            # Add ORDER BY importance ASC, publication_date DESC.
            'importance',
            '-publication_date',
        )

    selected_importance = None
    if importance_param:
        try:
            # Try to convert the importance parameter to an integer.
            selected_importance = int(importance_param)
            # Add WHERE importance = selected_importance.
            query_set = query_set.filter(importance=selected_importance)
        except ValueError:
            # If the parameter is not a valid integer, ignore it.
            selected_importance = None

    return render(request, 'news/article_list.html', {
        'articles': query_set,
        'importance_labels': IMPORTANCE_LABELS,
        'selected_importance': selected_importance,
    })


@login_required
def article_detail(request, pk):
    """Render a single article with its threaded comment section.

    Readers without a qualifying subscription see only the title, teaser
    (first sentence), and a subscribe prompt.  Full access requires an
    active :class:`~daily_indaba.models.Subscription` to the article's
    author or publisher, or the all-articles flat-rate plan.  Journalists
    and editors always have full access.
    """
    # ----------------------------------------------------------------------
    # Determine which article this user is allowed to read:
    # ----------------------------------------------------------------------
    # Build the base QuerySet for article-detail lookups:
    article_qs = Article.objects.select_related(
        # Tell Django to join these related tables up front for efficiency.
        'author', 'publisher', 'category', 'approved_by'
    )
    if request.user.role == 'editor':
        # Editors may only read articles that fall inside their curation scope.
        article = get_object_or_404(
            _filter_articles_for_editor(article_qs, request.user),
            pk=pk,
        )
    elif request.user.role == 'journalist':
        # This `Q(approved=True) | Q(author=request.user)` clause is another
        # direct example of the OR composition described by Guest et al.,
        # Web Development with Django 6, pp. 186-188.
        article = get_object_or_404(
            # Add WHERE approved = true OR author_id = request.user.pk.
            article_qs.filter(
                Q(approved=True) | Q(author=request.user)
            ),
            pk=pk,
        )
    else:
        article = get_object_or_404(
            # Readers may only fetch rows where pk = the requested article ID
            # AND approved = true.
            article_qs,
            pk=pk,
            approved=True,
        )

    # ----------------------------------------------------------------------
    # Determine whether the user has full access to this article:
    # ----------------------------------------------------------------------
    full_access = _user_has_full_access(request.user, article)
    teaser = _first_sentence(article.content)

    # ----------------------------------------------------------------------
    # Prepare subscription and discussion data for the page:
    # ----------------------------------------------------------------------
    # Prepare subscription information for readers who don't have full access.
    subscribe_fee_info = None
    if not full_access and request.user.role == 'reader':
        subscribe_fee_info = {
            'journalist': article.author,
            'journalist_fee': article.author.journalist_monthly_fee,
            'publisher': article.publisher,
            'publisher_fee': (
                article.publisher.monthly_fee if article.publisher else None
            ),
        }

    # Keep reader discussion behind the same access gate as the full article.
    comment_roots = []
    comment_count = 0
    reply_parent = None
    if full_access:
        all_comments = list(
            # Add WHERE article_id = article.pk through the reverse relation.
            article.comments
            # Tell Django to join the author table up front for efficiency.
            .select_related('author')
            # Add ORDER BY created_at ASC so comments render oldest-first.
            .order_by('created_at')
        )
        comments_by_id = {c.id: c for c in all_comments}
        for c in all_comments:
            c.children = []
        for c in all_comments:
            if c.parent_id is None:
                # This is a root comment (no parent).
                comment_roots.append(c)
            elif c.parent_id in comments_by_id:
                # This is a reply, add it to its parent's children.
                comments_by_id[c.parent_id].children.append(c)
        comment_count = len(all_comments)

        reply_to_param = request.GET.get('reply_to')
        if reply_to_param and request.user.role == 'reader':
            try:
                reply_to_id = int(reply_to_param)
            except (TypeError, ValueError):
                reply_to_id = None

            candidate = comments_by_id.get(reply_to_id)
            if candidate is not None and candidate.depth < Comment.MAX_DEPTH:
                reply_parent = candidate

    # Only show comment form to readers who have full access to the article.
    if request.user.role == 'reader' and full_access:
        initial = {'parent_id': reply_parent.id} if reply_parent else None
        comment_form = CommentForm(initial=initial)
    else:
        comment_form = None

    # ----------------------------------------------------------------------
    # Render the article detail page:
    # ----------------------------------------------------------------------
    return render(request, 'news/article_detail.html', {
        'article': article,
        'full_access': full_access,
        'teaser': teaser,
        'all_articles_fee': get_all_articles_monthly_fee(),
        'subscribe_fee_info': subscribe_fee_info,
        'comment_roots': comment_roots,
        'comment_count': comment_count,
        'comment_form': comment_form,
        'reply_parent': reply_parent,
    })


# =============================================================================
# CORE REQUIREMENT - Journalists must be able to create, edit, and delete
# their own articles, while editors can intervene across the article workflow.
# =============================================================================
@login_required
def article_create(request):
    """Allow a journalist to create a new article."""
    if _require_role(request, 'journalist'):
        return redirect('news:home')

    if request.method == 'POST':
        # Guest et al. present file uploads as the standard Django forms
        # pattern of binding POST data together with request.FILES so uploaded
        # media validates and saves through the ModelForm / ImageField pair.
        # See Web Development with Django 6, Packt, pp. 509-523.
        form = ArticleForm(request.POST, request.FILES)
        _limit_publisher_choices(form, request.user)
        if form.is_valid():
            article = form.save(commit=False)
            article.author = request.user
            article.approved = False
            article.status = Article.STATUS_PENDING
            article.save()
            messages.success(request, "Article saved. Awaiting approval.")
            return redirect('news:journalist_dashboard')
    else:
        form = ArticleForm()
        _limit_publisher_choices(form, request.user)

    return render(request, 'news/article_form.html', {
        'form': form,
        'form_title': 'Write New Article',
    })


@login_required
def article_edit(request, pk):
    """Allow a journalist to edit their own unapproved article,
    or an editor to edit any article.
    """
    article = get_object_or_404(Article, pk=pk)

    if request.user.role == 'journalist':
        if article.author != request.user or article.approved:
            messages.error(
                request,
                "You cannot edit this article.",
            )
            return redirect('news:journalist_dashboard')
    elif request.user.role != 'editor':
        messages.error(request, "Access denied.")
        return redirect('news:home')
    elif not _editor_can_manage_article(request.user, article):
        messages.error(
            request,
            "You are not assigned to curate this article.",
        )
        return redirect('news:editor_dashboard')

    if request.method == 'POST':
        # File uploads in Django forms require request.FILES alongside the POST
        # payload; this keeps article-image updates on the normal ModelForm
        # validation path described by Guest et al.  See Web Development with
        # Django 6, Packt, pp. 509-523.
        form = ArticleForm(request.POST, request.FILES, instance=article)
        _limit_publisher_choices(form, request.user)
        if form.is_valid():
            article = form.save(commit=False)
            resubmitted = (
                request.user.role == 'journalist'
                and article.status == Article.STATUS_RETURNED
            )
            if resubmitted:
                article.resubmit_for_approval()
            article.save()
            if resubmitted:
                messages.success(
                    request,
                    "Article updated and resubmitted for approval.",
                )
            else:
                messages.success(request, "Article updated.")
            return redirect('news:article_detail', pk=article.pk)
    else:
        form = ArticleForm(instance=article)
        _limit_publisher_choices(form, request.user)

    return render(request, 'news/article_form.html', {
        'form': form,
        'form_title': 'Edit Article',
        'article': article,
    })


@login_required
def article_delete(request, pk):
    """Allow a journalist (own unapproved article only) or editor to delete an
    article.  Renders a confirmation page on GET; deletes on POST.
    """
    # Load the article selected for deletion.
    article = get_object_or_404(Article, pk=pk)

    if request.user.role == 'journalist':
        # Journalists may only delete articles they authored themselves while
        # those articles are still unapproved drafts.
        if article.author != request.user:
            messages.error(request, "You cannot delete this article.")
            return redirect('news:journalist_dashboard')
        if article.approved:
            messages.error(
                request,
                "Approved articles cannot be deleted by journalists.",
            )
            return redirect('news:journalist_dashboard')
    elif request.user.role != 'editor':
        # Readers and other roles cannot delete articles.
        messages.error(request, "Access denied.")
        return redirect('news:home')
    elif not _editor_can_manage_article(request.user, article):
        messages.error(
            request,
            "You are not assigned to curate this article.",
        )
        return redirect('news:editor_dashboard')

    if request.method == 'POST':
        # Only delete after the confirmation form is submitted.
        article.delete()
        messages.success(request, "Article deleted.")
        if request.user.role == 'editor':
            # Editors return to the editorial dashboard after deletion.
            return redirect('news:editor_dashboard')
        # Journalists return to their personal desk after deletion.
        return redirect('news:journalist_dashboard')

    # On GET, render the confirmation page instead of deleting immediately.
    return render(request, 'news/article_confirm_delete.html', {
        'article': article,
    })


# =============================================================================
# CORE REQUIREMENT - Editors must be able to approve or reject pending article
# submissions so only editorially cleared content becomes publicly visible.
# =============================================================================
@login_required
@require_POST
def article_approve(request, pk):
    """Approve an article (editor only).

    Sets ``approved=True`` and ``publication_date``.  Subscriber emails are
    dispatched by the article's post-save signal after the approval is saved.
    """
    # Only editors may approve pending articles.
    if _require_role(request, 'editor'):
        return redirect('news:home')

    # Load the still-unapproved article chosen in the approval queue:
    article = get_object_or_404(
        Article,
        # Add WHERE pk = the requested article ID AND approved = false AND
        # status = pending.
        pk=pk,
        approved=False,
        status=Article.STATUS_PENDING,
    )
    if not _editor_can_manage_article(request.user, article):
        messages.error(
            request,
            "You are not assigned to curate this article.",
        )
        return redirect('news:approval_queue')
    # Run the shared publish workflow so web and API behaviour stay aligned.
    publish_article(article, editor=request.user)
    messages.success(
        request,
        f'"{article.title}" approved and published.',
    )
    return redirect('news:approval_queue')


@login_required
@require_POST
def article_reject(request, pk):
    """Reject (return) an article submission (editor only).

    The article leaves the approval queue and is marked as returned so the
    journalist can revise and resubmit it. If a reason is supplied it is
    emailed to the journalist and stored on the article for in-app display.
    """
    # ----------------------------------------------------------------------
    # Ensure the editor may act on this pending article:
    # ----------------------------------------------------------------------
    # Only editors may return articles for revision.
    if _require_role(request, 'editor'):
        return redirect('news:home')

    # Load the still-unapproved article being returned to its author:
    article = get_object_or_404(
        Article,
        # Add WHERE pk = the requested article ID AND approved = false AND
        # status = pending.
        pk=pk,
        approved=False,
        status=Article.STATUS_PENDING,
    )
    if not _editor_can_manage_article(request.user, article):
        messages.error(
            request,
            "You are not assigned to curate this article.",
        )
        return redirect('news:approval_queue')
    # ----------------------------------------------------------------------
    # Obtain the rejection reason from the submitted form:
    # ----------------------------------------------------------------------
    # Bind the optional rejection reason from the approval-queue form.
    form = RejectionForm(request.POST)
    reason = ''
    if form.is_valid():
        reason = form.cleaned_data.get('reason', '')

    # ----------------------------------------------------------------------
    # Return the article to the journalist for revision:
    # ----------------------------------------------------------------------
    return_article_for_revision(article, reason=reason)

    # ----------------------------------------------------------------------
    # Notify the journalist and return to the approval queue:
    # ----------------------------------------------------------------------
    # Send the rejection email only when a reason was supplied and email exists.
    if reason and article.author.email:
        send_email_with_fallback(
            subject=f'Article returned: {article.title}',
            body=(
                f'Your article "{article.title}" has been '
                f'returned by the editor.\n\n'
                f'Reason: {reason}\n\n'
                f'Please revise and resubmit.'
            ),
            recipient_list=[article.author.email],
            description="article rejection email",
            console_heading="ARTICLE REJECTION",
            log_context=f"article_id={article.pk}",
        )

    # Show a warning message and return the editor to the approval queue.
    messages.warning(
        request,
        f'"{article.title}" returned to the journalist.',
    )
    return redirect('news:approval_queue')
