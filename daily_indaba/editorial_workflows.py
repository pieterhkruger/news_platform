"""Editorial workflow services for Daily Indaba articles.

This module centralises the two article-review transitions that both the web
views and REST API need to perform:

- ``publish_article()`` marks an article as approved/published, records the
  approving editor, stamps the publication metadata, and persists the fields
  that trigger the approval signal side effects.
- ``return_article_for_revision()`` moves an unapproved article back to the
  journalist, clears publication metadata, stores editor feedback, and
  persists the same editorial workflow fields consistently.

Keeping those state transitions here prevents the browser and API layers from
duplicating or drifting apart on the exact fields updated during editorial
review.
"""

from django.core.exceptions import ValidationError


# =============================================================================
# DERIVED REQUIREMENT - Centralise article-publication orchestration so the web
# UI and REST API cannot drift apart on approval side-effects or validation.
# =============================================================================
def publish_article(article, *, editor):
    # Developer's note: the bare ``*`` ends positional parameters, which makes
    # ``editor`` keyword-only. That keeps the call sites explicit about the
    # role of the approving user object. Cf.:
    # https://docs.python.org/3/glossary.html#term-parameter
    """Approve *article* and persist the editorial decision.

    Subscriber emails and the mock approval-announcement POST are dispatched by
    the article's post-save signal once the row transitions from
    ``approved=False`` to ``approved=True``. Keeping the save here centralised
    ensures the web UI and API use the same approval semantics without
    duplicating that state change.
    """
    if article.approved:
        raise ValidationError("This article has already been approved.")

    article.approve(editor=editor)
    article.save(
        update_fields=[
            "approved",
            "status",
            "publication_date",
            "approved_by",
            "editor_feedback",
        ]
    )
    return article


def return_article_for_revision(article, *, reason=""):
    # Developer's note: the bare ``*`` ends positional parameters, which makes
    # ``reason`` keyword-only. That keeps the call sites explicit about the
    # purpose of the editor feedback text. Cf.:
    # https://docs.python.org/3/glossary.html#term-parameter
    """Return *article* to its journalist and persist the editor feedback.

    Clearing the publication fields in one shared helper ensures the browser
    workflow and the API apply the same editorial-review semantics when an
    article is sent back for revision.
    """
    if article.approved:
        raise ValidationError(
            "Approved articles cannot be returned for revision.")

    article.return_to_journalist(reason=reason)
    article.save(
        update_fields=[
            "approved",
            "status",
            "publication_date",
            "approved_by",
            "editor_feedback",
        ]
    )
    return article
