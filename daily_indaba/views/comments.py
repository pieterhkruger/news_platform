"""
Comment views for The Daily Indaba.

Reader-only; all views require login and the 'reader' role.

- post_comment: Post a new comment or threaded reply on an approved
  article; requires an active subscription or all-articles plan.
- comment_confirm_delete: Render a confirmation page before deleting
  the reader's own comment thread.
- comment_delete: Delete the reader's own comment thread (POST only).
"""

# Flash messages survive the redirect back to the article detail page.
from django.contrib import messages
# ValidationError lets the view surface model-level comment rules cleanly.
from django.core.exceptions import ValidationError
# Only signed-in users may post or delete comments.
from django.contrib.auth.decorators import login_required
# Shortcuts for loading rows or returning the browser to the detail page.
from django.shortcuts import get_object_or_404, redirect, render
# State-changing comment actions should only accept POST requests.
from django.views.decorators.http import require_POST

# Form used for validating new comments and replies.
from ..forms import CommentForm
# Article is the discussion target; Comment stores the threaded discussion rows.
from ..models import Article, Comment
# Shared helpers centralise access checks used across the news views.
from .helpers import _user_has_full_access, _require_role


@login_required
@require_POST
def post_comment(request, pk):
    """Create a comment or reply on an article (readers only).

    On validation failure the user is redirected back to the article
    with an error message rather than re-rendering the form, to keep
    the detail view simple.
    """
    # Only readers may post comments on articles.
    if _require_role(request, 'reader'):
        return redirect('news:home')

    # Load the approved article that the new comment belongs to.
    article = get_object_or_404(Article, pk=pk, approved=True)
    # Check that this reader has enough access to participate in discussion.
    if not _user_has_full_access(request.user, article):
        messages.error(
            request,
            "You need an active subscription or All-Articles plan to comment on this article.",
        )
        return redirect('news:article_detail', pk=pk)

    # Bind the submitted form data to the comment form.
    form = CommentForm(request.POST)

    if form.is_valid():
        # Treat the submission as a top-level comment unless a reply target was supplied.
        # Start with no parent (top-level comment) unless a parent_id is provided.
        parent = None
        parent_id = form.cleaned_data.get('parent_id')
        if parent_id:
            # Use .first() so an invalid/tampered parent_id simply behaves like
            # "no parent" instead of raising an extra 404 here.
            # If replying, find the parent comment in the same article.
            parent = Comment.objects.filter(  # Build a queryset with WHERE
                pk=parent_id, article=article
            ).first()  # Return the first matching Comment row or None.

        # Create the comment instance with all required fields set explicitly.
        comment = Comment(
            article=article,
            author=request.user,
            parent=parent,
            body=form.cleaned_data['body'],
        )
        try:
            # Attempt to save the comment, allowing model validation to run.
            comment.save()
            # If successful, show success message.
            messages.success(request, "Comment posted.")
        except ValidationError as exc:
            # If model validation fails, show the error messages.
            messages.error(request, '; '.join(exc.messages))
    else:
        # If form validation fails, show a generic error message.
        messages.error(request, "Your comment could not be saved.")

    # Always redirect back to the article detail page after processing.
    return redirect('news:article_detail', pk=pk)


@login_required
def comment_confirm_delete(request, article_pk, comment_pk):
    """Render a confirmation page before deleting a reader comment thread."""
    # Only readers may delete their own comments.
    if _require_role(request, 'reader'):
        return redirect('news:home')

    comment = get_object_or_404(
        Comment,
        pk=comment_pk,
        article_id=article_pk,
        author=request.user,
    )
    return render(
        request,
        'news/comment_confirm_delete.html',
        {
            'comment': comment,
        },
    )


@login_required
@require_POST
def comment_delete(request, article_pk, comment_pk):
    """Allow a reader to delete their own comment thread."""
    # Only readers may delete their own comments.
    if _require_role(request, 'reader'):
        return redirect('news:home')

    # Fetch the reader's own comment on the specified article.
    comment = get_object_or_404(
        Comment, pk=comment_pk, article_id=article_pk,
        author=request.user,
    )
    # Read this before deletion so the success message can explain whether the
    # delete also removed nested replies.
    had_replies = comment.has_replies
    comment.delete()
    if had_replies:
        messages.success(request, "Your comment and its replies have been deleted.")
    else:
        messages.success(request, "Your comment has been deleted.")
    return redirect('news:article_detail', pk=article_pk)
