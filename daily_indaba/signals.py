"""Signal receivers for Daily Indaba content workflows."""

# The capstone brief explicitly allowed a signal-based implementation for the
# approval side effects ("use post_save or a custom signal after article
# approval", page 7). HyperionDev later confirmed the API/social-posting
# section is optional, but this project retains the full signal path as an
# in-principle local implementation.
# "Django includes a “signal dispatcher” which helps decoupled applications
# get notified when actions occur elsewhere in the framework. In a nutshell,
# signals allow certain senders to notify a set of receivers that some action
# has taken place." https://docs.djangoproject.com/en/5.2/topics/signals/
#
# HOW IT WORKS:
# -------------
# Each function below is decorated with @receiver, which registers it with
# Django's signal dispatcher at import time. The functions are never called
# directly; Django calls them automatically whenever the matching signal fires
# (e.g. every time an Article is saved).
# Django passes two key arguments: sender (the Article class, identifying the
# type) and instance (the specific Article object being saved). The pre_save
# receiver attaches a temporary _previous_approved attribute directly onto
# instance before the database write; the post_save receiver reads that same
# attribute from the same object moments later to detect a state transition.
# The attribute disappears when the request ends and the object is discarded.


# pre_save / post_save fire around Django model writes.
# Cf. https://docs.djangoproject.com/en/5.2/ref/signals/#pre-save
#     https://docs.djangoproject.com/en/5.2/ref/signals/#post-save
from django.db.models.signals import post_save, pre_save
# @receiver is the decorator form of signal registration.
# Cf.: https://docs.djangoproject.com/en/5.2/topics/signals/
from django.dispatch import receiver

from .models import Article, ArticleNotification
# Posts the retained mock approval announcement when an article is approved.
from .announcement_client import post_article_approval_announcement
# Import the helpers module rather than binding the function directly so tests
# can patch daily_indaba.views.helpers._notify_subscribers and the receiver will
# still pick up the patched callable at runtime.
from .views import helpers as view_helpers


# =============================================================================
# DERIVED REQUIREMENT - Detect real approval transitions against persisted
# database state so approval notifications do not depend on transient
# attributes that only exist on one particular save path.
# =============================================================================
@receiver(
    pre_save,  # Run function before saving the row to the DB
    sender=Article,  # only fire for Article saves, not other models
    # sender = a Python variable holding the model class
    dispatch_uid="daily_indaba.article_previous_approval_state",
    # dispatch_uid prevents duplicate registration if this module is imported
    # more than once (e.g. in tests). Django ignores any subsequent
    # registration attempt that uses the same uid, so the receiver only fires
    # once per save. (The dispatch_uid is an arbitrary unique string.)
)
def capture_previous_article_approval_state(sender, instance, **kwargs):
    """Cache the database approval state before an article save runs.

    Attaches a ``_previous_approved`` attribute to *instance* so that the
    matching :func:`notify_subscribers_after_article_approval` post-save
    receiver can detect a real ``False → True`` approval transition.

    :param sender: The model class that sent the signal (``Article``).
    :type sender: type
    :param instance: The ``Article`` instance about to be saved.
    :type instance: Article
    :param kwargs: Additional keyword arguments forwarded by Django's signal
        dispatcher (e.g. ``raw``, ``using``).
    :rtype: None
    """
    # -----------------------------------------------------------------------
    # Handle brand-new Article rows that have no previous persisted state
    # -----------------------------------------------------------------------
    # Brand-new Article rows have no previous database state to compare
    # against, so the later post_save receiver can treat them separately.
    if not instance.pk:
        instance._previous_approved = None
        return

    # -----------------------------------------------------------------------
    # Capture the current persisted approval state before the save runs
    # -----------------------------------------------------------------------
    # Fetch only the approved column rather than loading the entire Article row
    # (the ORM term for loading all columns into an object is "hydrating");
    # all the signal needs is the previous boolean value to detect a
    # transition:
    instance._previous_approved = (
        # Create and narrow the queryset to the one Article row matching this
        # instance:
        sender.objects.filter(pk=instance.pk)
        # Retrieve only the 'approved' column as a flat list of plain values:
        .values_list("approved", flat=True)
        # Return the single result (a boolean) rather than a list:
        .first()
    )


@receiver(
    post_save,  # Run function after saving the row to the DB
    sender=Article,  # only fire for Article saves, not other models
    dispatch_uid="daily_indaba.article_approval_email_signal",
)
def notify_subscribers_after_article_approval(
    sender,
    instance,
    created,
    **kwargs,
):
    """Send approval side effects when an existing article becomes approved.

    Fires only on a confirmed ``False → True`` transition observed against the
    database state captured by the pre-save receiver.  Brand-new inserts,
    unchanged saves, and re-saves of already-approved articles are all skipped.

    Side effects (in order):

    1. Subscriber notification emails via
       :func:`~daily_indaba.views.helpers._notify_subscribers`.
    2. Mock external announcement POST via
       :func:`~daily_indaba.announcement_client.post_article_approval_announcement`.
    3. In-app :class:`~daily_indaba.models.ArticleNotification` rows via
       :func:`_create_article_notifications`.

    :param sender: The model class that sent the signal (``Article``).
    :type sender: type
    :param instance: The ``Article`` instance that was just saved.
    :type instance: Article
    :param created: ``True`` if this save created a new row; ``False`` for
        updates.
    :type created: bool
    :param kwargs: Additional keyword arguments forwarded by Django's signal
        dispatcher.
    :rtype: None
    """
    # -----------------------------------------------------------------------
    # Determine whether article has just been approved
    # -----------------------------------------------------------------------
    # Ignore brand-new inserts, unchanged approvals, and ordinary edits to an
    # already-approved article. The receiver should only run for a real
    # False -> True transition observed against the database state captured by
    # the matching pre_save receiver.
    if (
        created  # skip brand-new inserts; only updates can be a transition
        or not instance.approved  # skip if the article is still not approved
        # after the save
        or getattr(instance, "_previous_approved", None) is not False  # skip
        # if it was already approved (no transition)
    ):
        return

    # -----------------------------------------------------------------------
    # Notify subsribers of approved article & post approval announcement
    # -----------------------------------------------------------------------
    # Dispatch both approval side effects after the article has definitely been
    # saved as approved. Keeping this logic in the signal means both the web
    # approval flow and the API approval endpoint share the same side effects.
    # (1) send subscriber email :
    view_helpers._notify_subscribers(instance)
    # (2) posts the mock announcement:
    post_article_approval_announcement(instance)
    # (3) creates in-app ArticleNotification:
    _create_article_notifications(instance)
    # Update the transient cache in case the in-memory instance is saved again
    # later in the same request or test.
    instance._previous_approved = instance.approved


def _create_article_notifications(article):
    """Create ArticleNotification rows for the author and all subscribers.

    Called once per approval transition.  Uses ``bulk_create`` with
    ``ignore_conflicts=True`` so a duplicate signal fire (e.g. in tests) is
    harmless.

    Recipients:

    - The article's author (journalist).
    - Readers subscribed to the article's publisher.
    - Readers subscribed to the article's author directly.

    :param article: The newly approved article.
    :type article: Article
    :raises django.db.DatabaseError: Propagated if the bulk insert fails for a
        reason other than a uniqueness conflict.
    :rtype: None
    """
    recipient_ids = view_helpers._article_approval_recipient_ids(
        article,
        include_author=True,
    )
    # include_author=True keeps the journalist in the in-app announcement queue
    # while the shared helper still supplies the same subscriber audience used
    # by the email path.

    ArticleNotification.objects.bulk_create(
        [
            ArticleNotification(article=article, recipient_id=rid)
            for rid in recipient_ids
        ],
        ignore_conflicts=True,  # safe to call twice; duplicate rows are skipped
    )
