"""
Models for The Daily Indaba news platform.

This module defines all data models for the news-content layer of the
application.  User authentication and account management live in the
``accounts`` app; every user FK here references
``settings.AUTH_USER_MODEL``.

Models
------
Publisher          - news organisation housing editors and journalists
Article            - individual news article awaiting editorial approval
NewsletterCategory - shared editorial category for articles and newsletters
Newsletter         - curated article collection with an optional category label
Subscription       - reader's follow of a publisher or journalist
Comment            - reader comment or threaded reply on an article
ArticleNotification - per-user announcement that an article has been approved
"""

from django.conf import settings
# ValidationError is raised inside clean() methods when a model's data violates
# a business rule.  Django catches it during form validation and full_clean().
from django.core.exceptions import ValidationError
# Min/Max value validators allow the model layer to enforce the documented
# subscription-fee ranges even when values are set outside a Django form.
from django.db import models, transaction
from django.utils import timezone
# slugify() converts a human-readable string into a URL-safe slug, e.g.
# "Technology & Science" -> "technology-science".
# Cf.: https://docs.djangoproject.com/en/5.2/ref/utils/#django.utils.text.slugify
from django.utils.text import slugify

from .media_files import delete_field_file_if_unreferenced
from accounts.models import (
    get_default_publisher_monthly_fee,
    validate_publisher_fee,
)


# =============================================================================
# CORE REQUIREMENT - Publisher model representing organisations that affiliate
# editors and journalists and act as subscriber-visible content sources.
# =============================================================================
# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------

class Publisher(models.Model):
    """A news organisation that houses editors and journalists.

    Readers may subscribe to a publisher to receive email notifications
    whenever one of its journalists has an article approved.

    Publishers are created and managed by administrators via the
    Django admin interface.  Editors and journalists are affiliated
    through the :attr:`editors` and :attr:`journalists` M2M fields.

    Attributes
    ----------
    name:        Unique display name of the publication.
    description: Optional short description or editorial mission.
    created_at:  Timestamp recorded automatically on creation.
    account:     Optional publisher-role account that manages this publisher's
                 own profile and editor assignments.
    curates_independent_journalists: Whether editors affiliated with this
                 publisher may approve articles that are not attached to any
                 publisher.
    editors:     Users with role ``editor`` affiliated with this publisher.
    journalists: Users with role ``journalist`` affiliated with this
                 publisher.
    """

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        # related_name gives the reverse accessor on User, so code can follow
        # publisher.account -> User in the forward direction and
        # user.managed_publisher -> Publisher in the reverse direction.
        # If omitted, Django would use a default reverse name instead.
        # Cf. Melé, Django 5 By Example, p. 85, and Django's relationship docs:
        # https://docs.djangoproject.com/en/5.2/topics/db/examples/many_to_one/
        related_name="managed_publisher",
        # limit_choices_to narrows the selectable User rows for this field in
        # admin/model-form contexts to publisher-role accounts only; it is
        # about which related objects may be chosen:
        limit_choices_to={"role": "publisher"},
        help_text=(
            "Optional publisher-role account that manages this publisher via "
            "self-service."
        ),
    )
    curates_independent_journalists = models.BooleanField(
        default=False,
        help_text=(
            "When enabled, editors affiliated with this publisher may review "
            "and approve independent articles that do not belong to a "
            "publisher."
        ),
    )
    editors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        # related_name names the reverse accessor on User, so the reverse side
        # of publisher.editors becomes user.editor_publishers.
        # Cf. Melé, Django 5 By Example, pp. 196-197:
        related_name="editor_publishers",
        blank=True,
        # Restrict candidate related users to accounts whose role is "editor"
        # when this M2M relationship is edited through forms/admin.
        limit_choices_to={"role": "editor"},
    )
    journalists = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        # This reverse accessor is the journalist-specific counterpart to
        # editor_publishers above: publisher.journalists gives the forward
        # relation, while user.journalist_publishers gives the reverse set of
        # Publisher rows for a journalist user.
        related_name="journalist_publishers",
        blank=True,
        # Restrict candidate related users to accounts whose role is
        # "journalist" when editing the relationship via forms/admin.
        limit_choices_to={"role": "journalist"},
    )
    monthly_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=get_default_publisher_monthly_fee,
        help_text=(
            "Monthly subscription fee (ZAR) charged to readers who subscribe "
            "to this publisher. The valid range is defined by the "
            "subscription pricing policy seed data. Set by an affiliated "
            "editor."
        ),
    )

    class Meta:
        ordering = ["name"]

    def clean(self):
        # ``clean()`` is similar in spirit to SQL CHECK-style conditions, but
        # it runs in Django/Python rather than in the database itself.
        # It is used for whole-instance or cross-field business rules that
        # single field validators cannot express. Unlike a database constraint,
        # this is application-level validation & depends on Django calling it.
        """Validate cross-field / related-object business rules for Publisher.

        Django field validators only see one field at a time. This method can
        inspect the whole Publisher instance, which is why it is the right
        place to enforce rules such as "the linked account must have
        role='publisher'" and "monthly_fee must satisfy the current pricing
        policy".
        """
        super().clean()  # Cf. Guest, Chris; et al. (2026:461)
        if (
            self.account_id
            and getattr(self.account, "role", None) != "publisher"
        ):
            raise ValidationError(
                {"account":
                 "Only publisher-role accounts may own a publisher."}
            )
        validate_publisher_fee(self.monthly_fee)

    def save(self, *args, **kwargs):
        """Persist the publisher after validating its configurable fee."""
        # Re-check the configured fee here as a model-level safety net, even
        # when Publisher rows are saved programmatically rather than through a
        # validated form.
        validate_publisher_fee(self.monthly_fee)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Article
# ---------------------------------------------------------------------------

# =============================================================================
# CORE REQUIREMENT - Article model with author, publisher, timestamps, and
# approval state so journalists can submit work for editorial review.
# =============================================================================
class Article(models.Model):
    """A single news article authored by a journalist.

    Articles move through a simple editorial workflow recorded in
    :attr:`status`:

    * ``pending``  - submitted and awaiting editor review.
    * ``returned`` - sent back to the journalist for revision.
    * ``approved`` - published and publicly visible.

    :attr:`approved` remains the public-visibility flag used throughout the
    site, while :attr:`status` preserves the distinction between "still in the
    queue" and "back with the journalist". On approval (see :meth:`approve`):

    1. :attr:`approved` is set to ``True``.
    2. :attr:`status` becomes ``approved``.
    3. :attr:`publication_date` is stamped with the current timestamp.
    4. Email notifications are sent to all relevant subscribers.

    Importance levels
    -----------------
    The :attr:`importance` field controls display prominence and the
    order in which articles appear in listings:

    * ``FRONT_PAGE`` (1) - lead story; displayed with highest prominence.
    * ``TOP_STORY``  (2) - significant news, second tier.
    * ``STANDARD``   (3) - regular news item.

    Articles are ordered by importance ascending (1 first), then by
    :attr:`publication_date` descending within each tier.

    Attributes
    ----------
    title:            Headline of the article.
    content:          Full body text of the article.
    created_at:       Timestamp recorded automatically on creation.
    publication_date: Set when the article is approved; null otherwise.
    approved:         Whether the article has been approved by an editor.
    status:           Editorial workflow status (pending/returned/approved).
    approved_by:      The editor who approved the article, if published.
    editor_feedback:  Optional editor feedback shown to the journalist when an
                      article is returned for revision.
    importance:       Display prominence level (integer choice).
    image:            Optional hero image uploaded with the article.
    disclaimer:       Optional bias disclaimer (journalist or editor).
    author:           The journalist who wrote the article.
    publisher:        Optional publisher affiliation.
    category:         Editorial category assigned to the article.
    """

    FRONT_PAGE = 1
    TOP_STORY = 2
    STANDARD = 3
    STATUS_PENDING = "pending"
    STATUS_RETURNED = "returned"
    STATUS_APPROVED = "approved"

    IMPORTANCE_CHOICES = [
        (FRONT_PAGE, "Front Page"),
        (TOP_STORY, "Top Story"),
        (STANDARD, "Standard"),
    ]
    STATUS_CHOICES = [
        (STATUS_PENDING, "Awaiting approval"),
        (STATUS_RETURNED, "Returned to journalist"),
        (STATUS_APPROVED, "Published"),
    ]

    title = models.CharField(max_length=300)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    publication_date = models.DateTimeField(null=True, blank=True)
    approved = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        help_text=(
            "Editorial workflow status. Pending articles are in the approval "
            "queue, returned articles are back with the journalist for "
            "revision, and approved articles are published."
        ),
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # SET_NULL preserves the article's approval record even when the
        # approving editor's account is later deleted.  null=True is required
        # whenever SET_NULL is used so the column can store NULL.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_articles",
        help_text="The editor who approved this article for publication.",
    )
    editor_feedback = models.TextField(
        blank=True,
        help_text=(
            "Optional editor feedback shown to the journalist when this "
            "article is returned for revision."
        ),
    )
    importance = models.PositiveSmallIntegerField(
        choices=IMPORTANCE_CHOICES,
        default=STANDARD,
    )
    # Article images are user-uploaded files, not fixed static files like CSS
    # or logos. Django stores them under MEDIA_ROOT, and ImageField keeps the
    # file path and served URL for the model. See Guest et al., Web
    # Development with Django 6, Packt, pp. 509-510 and 516-523.
    image = models.ImageField(
        upload_to="articles/images/",
        null=True,
        blank=True,
        help_text=(
            "Required for Front Page articles; optional for Top Story "
            "and Standard articles."
        ),
    )
    disclaimer = models.TextField(
        blank=True,
        help_text=(
            "Bias disclaimer added by the journalist or editor when "
            "potential bias is recognised in the article."
        ),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # The brief says a journalist's independently published articles may be
        # represented by a ForeignKey or a reverse relation. This is that
        # reverse relation: Article.author stores the forward link, and
        # related_name="articles" gives the reverse accessor user.articles.
        on_delete=models.CASCADE,
        related_name="articles",
    )
    publisher = models.ForeignKey(
        Publisher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
        help_text="The publisher this article belongs to, if any.",
    )
    category = models.ForeignKey(
        "NewsletterCategory",
        # PROTECT keeps article categorisation stable and prevents deleting a
        # category that is still the authoritative classification for one or
        # more articles.
        on_delete=models.PROTECT,
        null=True,
        related_name="articles",
        help_text="Editorial category assigned to this article.",
    )

    class Meta:
        ordering = ["importance", "-publication_date", "-created_at"]

    def _validate_front_page_image_requirement(self):
        """
        Raise ValidationError when a Front Page article has no image.

        This image rule is a business decision for this capstone project's
        presentation standards. It is not a direct requirement from the
        assignment brief itself nor a neccessary requirement.
        """
        if self.importance == self.FRONT_PAGE and not self.image:
            raise ValidationError(
                {
                    "image": (
                        "Front Page articles require a rubric image."
                    )
                }
            )

    def approve(self, editor=None):
        """Mark the article as approved and stamp the publication date.

        Only updates the in-memory instance.  The caller must call
        :meth:`save` so the post-save notification hook can run.
        """
        self.approved = True
        self.status = self.STATUS_APPROVED
        self.publication_date = timezone.now()
        self.editor_feedback = ""
        if editor is not None:
            self.approved_by = editor

    def return_to_journalist(self, reason=""):
        """Move the article out of the approval queue and back to its author."""
        self.approved = False
        self.status = self.STATUS_RETURNED
        self.publication_date = None
        self.approved_by = None
        self.editor_feedback = reason.strip()

    def resubmit_for_approval(self):
        """Move a returned article back into the editor approval queue."""
        self.approved = False
        self.status = self.STATUS_PENDING
        self.publication_date = None
        self.approved_by = None

    def clean(self):
        """Enforce whole-article business rules that involve more than one
        field.

        A field-level validator can check that ``image`` is a valid upload, but
        it cannot express the rule "Front Page articles require an image"
        because that rule depends on the combination of ``importance`` and
        ``image`` together. ``clean()`` sees the whole model instance, so the
        cross-field check belongs here.
        """
        super().clean()
        self._validate_front_page_image_requirement()
        # The capstone workflow treats Article.author as the submitting
        # journalist. Views and serializers already enforce that at the HTTP
        # boundary; this model-level guard keeps admin edits, fixtures, and
        # shell scripts aligned with the same rule.
        if (
            self.author_id and
            getattr(self.author, "role", None) != "journalist"
        ):
            raise ValidationError(
                {"author": "Articles must be authored by journalist accounts."}
            )
        # Approval metadata should only ever point at an editor account.
        if (
            self.approved_by_id
            and getattr(self.approved_by, "role", None) != "editor"
        ):
            raise ValidationError(
                {
                    "approved_by": (
                        "Approved articles must record an editor as approver."
                    )
                }
            )

    # =========================================================================
    # DERIVED REQUIREMENT - Keep uploaded article media aligned with database
    # truth so replacing or deleting an article image does not leave orphaned
    # files behind in MEDIA_ROOT.
    # =========================================================================
    def save(self, *args, **kwargs):
        """
        Persist the article and retire the previous image once it is replaced.

        Django keeps uploaded media files on disk separately from the database
        row, so updating an ImageField does not automatically remove the old
        file.  Cleaning up after commit prevents orphaned media from
        accumulating in ``MEDIA_ROOT/articles/images``.
        """
        self._validate_front_page_image_requirement()

        previous_image_name = None
        if self.approved:
            self.status = self.STATUS_APPROVED
        elif self.status == self.STATUS_APPROVED:
            self.status = self.STATUS_PENDING
        # Run the model's cross-field and role validation here as a safety net
        # for code paths that save Article objects outside ModelForms or DRF
        # serializers, such as the Django admin, shell scripts, and tests.
        self.full_clean()
        # Developer's note: full_clean is Django’s higher-level validation
        # that calls clean() as part of its process

        # -----------------------------------------------------------------
        # Determine the stored name of the previous Article image (if any)
        # -----------------------------------------------------------------
        if self.pk:
            previous_image_name = (
                type(self).objects.filter(  # Build a queryset with WHERE
                    pk=self.pk  # pk = this article's primary key
                ).values_list(  # Return only the stored image-field value.
                    "image",
                    flat=True  # Return a flat sequence of values.
                ).first()  # Return the first matching value or None.
            ) or None

        # -----------------------------------------------------------------
        # Save the Article with its current image field value
        # -----------------------------------------------------------------
        # Call ``super().save()`` so Django performs the actual INSERT /
        # UPDATE and fires its normal model-save machinery before the later
        # on-commit file-cleanup callback runs.
        super().save(*args, **kwargs)

        # -----------------------------------------------------------------
        # After commit, delete the previous image file if now unreferenced
        # -----------------------------------------------------------------
        if previous_image_name and previous_image_name != self.image.name:

            article_model = type(self)

            def cleanup_replaced_image(
                old_name=previous_image_name,
                article_pk=self.pk,
                model=article_model,
            ):
                delete_field_file_if_unreferenced(
                    model,
                    "image",
                    old_name,
                    exclude_pk=article_pk,
                )

            # Defer file deletion until commit so a rolled-back save cannot
            # leave the row pointing at a file that has already been removed.
            transaction.on_commit(cleanup_replaced_image)
            # Cf. https://docs.djangoproject.com/en/5.2/topics/db/transactions/#performing-actions-after-commit

    def delete(self, *args, **kwargs):
        """Delete the row and then prune its image if no other row uses it."""
        # Capture the current file name before the row disappears from the DB.
        image_name = self.image.name or None
        # Remove the article row from the database:
        result = super().delete(*args, **kwargs)

        if image_name:
            # Keep the model class for the later cleanup callback.
            article_model = type(self)

            def cleanup_deleted_article_image(
                old_name=image_name,
                model=article_model,
            ):
                # Delete the file only if no remaining Article row still
                # points at it.
                delete_field_file_if_unreferenced(
                    model,
                    "image",
                    old_name,
                )
            # Wait until the DB delete commits so a rollback cannot orphan the
            # row.
            transaction.on_commit(cleanup_deleted_article_image)
            # Cf. https://docs.djangoproject.com/en/5.2/topics/db/transactions/#performing-actions-after-commit

        return result

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# NewsletterCategory
# ---------------------------------------------------------------------------

class NewsletterCategory(models.Model):
    """A shared editorial category used to classify articles and label newsletters.

    Default categories (Politics, Sport, Economics, Technology, Culture,
    Science, Business, Entertainment, International, International Relations)
    are seeded automatically by the demo-data bootstrap that runs after
    ``manage.py migrate`` on a fresh database.  Administrators may create
    additional categories at runtime via the Django admin interface.

    The :attr:`slug` is auto-generated from :attr:`name` on first save
    and is used in category-filtered article browsing URLs.

    Attributes
    ----------
    name:        Unique human-readable category label.
    slug:        URL-safe identifier, auto-derived from ``name``.
    description: Optional editorial description of the category.
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "editorial categories"

    def save(self, *args, **kwargs):
        """Auto-generate :attr:`slug` from :attr:`name` if not set."""
        # Overriding ``save()`` is the standard place to derive values such as
        # slugs immediately before persistence, so the stored row stays in sync
        # with the current name when no explicit slug was supplied.
        if not self.slug:
            self.slug = slugify(self.name)
        # Always call super().save() to run Django's internal save machinery
        # (pre/post_save signals, auto_now updates, etc.).  Omitting this would
        # silently prevent the row from being written to the database.
        # Cf.: https://docs.djangoproject.com/en/5.2/topics/db/models/#overriding-predefined-model-methods
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Newsletter
# ---------------------------------------------------------------------------

# =============================================================================
# CORE REQUIREMENT - Newsletter model for curated article collections authored
# by journalists or editors and viewable by authenticated users.
# =============================================================================
class Newsletter(models.Model):
    """A curated collection of articles with an optional category label.

    Newsletters are authored by journalists or editors and are visible to all
    authenticated users. Editors may create, edit, or delete any newsletter;
    journalists may create newsletters and may only edit or delete their own.

    The optional :attr:`category` acts as a newsletter-level label for a
    focused edition.  It is not the authoritative source of article
    categorisation; each article carries its own category directly.

    The many-to-many :attr:`articles` field allows an authorised author to
    associate any article with the newsletter, regardless of approval
    status — but only approved articles will typically be displayed.

    Attributes
    ----------
    title:       Headline title of the newsletter edition.
    description: Optional editorial summary or introduction.
    created_at:  Timestamp recorded automatically on creation.
    author:      The journalist or editor who created the newsletter.
    category:    Optional :class:`NewsletterCategory` label for the newsletter.
    articles:    Articles included in this newsletter (M2M).
    """

    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # The brief likewise suggests a journalist's newsletters can be
        # represented by a ForeignKey or a reverse relation. This field stores
        # the forward link, and related_name="newsletters" gives the reverse
        # accessor user.newsletters.
        on_delete=models.CASCADE,
        related_name="newsletters",
    )
    category = models.ForeignKey(
        NewsletterCategory,
        # Newsletter categories are descriptive labels only, so deleting a
        # category should clear the newsletter label rather than blocking
        # deletion or removing the newsletter itself.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="newsletters",
    )
    articles = models.ManyToManyField(
        Article,
        related_name="newsletters",
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        """Restrict newsletter authorship to journalists and editors."""
        super().clean()
        if self.author_id and getattr(self.author, "role", None) not in {
            "journalist",
            "editor",
        }:
            raise ValidationError(
                {
                    "author": (
                        "Newsletters must be authored by journalist or editor "
                        "accounts."
                    )
                }
            )

    def save(self, *args, **kwargs):
        """Persist the newsletter after applying role-aware validation."""
        # Model.save() is reached by admin edits and direct ORM usage as well
        # as normal form submissions, so the authorship rule is enforced here
        # in addition to the view and serializer layers.
        self.full_clean()
        # Developer's note: full_clean is Django’s higher-level validation
        # that calls clean() as part of its process
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------

# =============================================================================
# CORE REQUIREMENT - Subscription model connecting a reader to either one
# publisher or one journalist so approved articles can be delivered to the
# correct subscribers.
# =============================================================================
class Subscription(models.Model):
    """A reader's subscription to a publisher or an individual journalist.

    A subscription links a reader to exactly one content source: either
    a :attr:`publisher` or an individual :attr:`journalist`.  Exactly
    one of the two FK fields must be non-null; the :meth:`clean` method
    enforces this invariant.

    This model also serves as the explicit through table behind the
    brief-visible ``accounts.User.subscribed_publishers`` and
    ``accounts.User.subscribed_journalists`` many-to-many relationships.

    Uniqueness constraints prevent duplicate subscriptions:

    * A reader may only subscribe to a given publisher once.
    * A reader may only subscribe to a given journalist once.

    Attributes
    ----------
    reader:     The reader who holds this subscription.
    publisher:  The publisher being followed (mutually exclusive with
                ``journalist``).
    journalist: The journalist being followed (mutually exclusive with
                ``publisher``).
    created_at: Timestamp recorded automatically on creation.
    """

    reader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    publisher = models.ForeignKey(
        Publisher,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="subscriptions",
    )
    journalist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="journalist_subscriptions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # =====================================================================
        # DERIVED REQUIREMENT - Enforce the core subscription invariant at the
        # schema level so each row targets exactly one source and duplicates are
        # blocked even outside normal form handling.
        # =====================================================================
        constraints = [
            # This check uses Q objects to encode the XOR-style rule "publisher
            # set and journalist empty" OR "publisher empty and journalist set".
            # Guest et al. explain the same `Q(...) | Q(...)` / `Q(...) & Q(...)`
            # boolean-composition syntax in Web Development with Django 6,
            # Packt, pp. 186-188.
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(publisher__isnull=False)
                        & models.Q(journalist__isnull=True)
                    )
                    | (
                        models.Q(publisher__isnull=True)
                        & models.Q(journalist__isnull=False)
                    )
                ),
                name="subscription_exactly_one_target",
            ),
            # UniqueConstraint enforces that no two rows may share the same
            # tuple of field values. Here that tuple is (reader, publisher),
            # which prevents duplicate subscriptions to the same publisher.
            #
            # SQLite can support the more selective conditional/partial-unique
            # version of this rule, where uniqueness only applies when
            # ``publisher IS NOT NULL``. MariaDB cannot create that kind of
            # partial index, so this project uses the portable form instead and
            # relies on normal SQL NULL semantics: rows where ``publisher`` is
            # NULL do not conflict with one another in this constraint. That
            # means journalist-only subscriptions can still coexist while
            # publisher duplicates remain blocked.
            #
            # Cf.: https://docs.djangoproject.com/en/5.2/ref/models/constraints/#uniqueconstraint
            models.UniqueConstraint(
                fields=["reader", "publisher"],
                name="unique_reader_publisher_subscription",
            ),
            # Same idea for the (reader, journalist) pair: duplicate
            # subscriptions to the same journalist are blocked. Again, SQLite
            # could express this as a conditional unique constraint on
            # ``journalist IS NOT NULL``, but MariaDB cannot, so rows with
            # ``journalist`` = NULL are ignored by the portable uniqueness
            # check.
            models.UniqueConstraint(
                fields=["reader", "journalist"],
                name="unique_reader_journalist_subscription",
            ),
        ]

    def clean(self):
        """Enforce the subscription XOR rule across the two target fields.

        This is another whole-instance rule: a subscription is only valid when
        exactly one of ``publisher`` or ``journalist`` is set. A single-field
        validator cannot see both foreign keys together, so ``clean()`` is the
        correct place for this check.
        """
        errors = {}
        if self.publisher is None and self.journalist is None:
            errors["__all__"] = (
                "A subscription must target either a publisher "
                "or a journalist."
            )
        if (
            self.publisher is not None
            and self.journalist is not None
        ):
            errors["__all__"] = (
                "A subscription cannot target both a publisher "
                "and a journalist simultaneously."
            )
        # The explicit through model is part of the assessed schema, so the
        # role semantics belong here as well instead of living only in form or
        # view code.
        if self.reader_id and getattr(self.reader, "role", None) != "reader":
            errors["reader"] = (
                "Only reader accounts may own subscriptions."
            )
        if (
            self.journalist_id
            and getattr(self.journalist, "role", None) != "journalist"
        ):
            errors["journalist"] = (
                "Journalist subscriptions must target journalist accounts."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Persist the subscription after enforcing the role/XOR invariant."""
        # full_clean() runs the model's clean() method plus Django's normal
        # field and constraint validation, which keeps direct ORM writes
        # aligned with the same rules enforced in forms and views.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        target = self.publisher or self.journalist
        return f"{self.reader} \u2192 {target}"


# ---------------------------------------------------------------------------
# Comment
# ---------------------------------------------------------------------------

class Comment(models.Model):
    """A reader comment or threaded reply posted on an article.

    Comments are organised in a three-level hierarchy:

    * **Depth 1** - top-level comment posted directly on the article.
    * **Depth 2** - reply to a depth-1 comment.
    * **Depth 3** - reply to a depth-2 comment (maximum permitted depth).

    Business rules (enforced in :meth:`clean` and :meth:`save`):

    * A comment may not exceed depth :attr:`MAX_DEPTH` (3).
    * A reader may not post more than :attr:`MAX_COMMENTS_PER_ARTICLE`
      (5) comments or replies in total on a single article.
    * A reader's combined word count across all comments and replies on
      a single article may not exceed :attr:`MAX_WORDS_PER_ARTICLE`
      (100 words).

    The :attr:`depth` value is derived from the parent chain and stored
    to avoid expensive recursive lookups on each page render.

    Attributes
    ----------
    article:    The article this comment belongs to.
    author:     The reader who posted the comment.
    parent:     Parent comment for replies; null for top-level comments.
    depth:      Nesting depth (1â€“3), computed and stored on save.
    body:       The text content of the comment.
    created_at: Timestamp recorded automatically on creation.
    """

    MAX_DEPTH = 3
    MAX_COMMENTS_PER_ARTICLE = 5
    MAX_WORDS_PER_ARTICLE = 100

    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    parent = models.ForeignKey(
        # "self" creates a self-referential FK - a comment can reference another
        # comment in the same table, enabling threaded replies.  This is the
        # standard Django pattern for tree-structured data.
        # Cf.: https://docs.djangoproject.com/en/5.2/ref/models/fields/#self-referential-many-to-one-relationships
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
    )
    depth = models.PositiveSmallIntegerField(default=1)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def has_replies(self):
        """Return True if at least one reply exists on this comment."""
        return self.replies.exists()

    @staticmethod
    def _word_count(text):
        """Return the number of whitespace-separated words in *text*."""
        return len(text.split())

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def clean(self):
        """Enforce depth, comment-count, and word-count constraints.

        These checks belong in ``clean()`` because they depend on the whole
        Comment instance plus related database state: the parent comment, the
        target article, the author, and the author's existing comments on that
        article. A per-field validator cannot see that wider context.

        Raises
        ------
        ValidationError
            If any of the three business rules are violated.
        """
        if (
            self.parent is not None
            and self.parent.article_id != self.article_id
        ):
            raise ValidationError(
                {
                    "parent": (
                        "Replies must belong to the same article as "
                        "their parent comment."
                    )
                }
            )

        # Derive and validate depth from parent chain
        if self.parent is None:
            self.depth = 1
        else:
            self.depth = self.parent.depth + 1

        if self.depth > self.MAX_DEPTH:
            raise ValidationError(
                f"Comments may not be nested beyond "
                f"depth {self.MAX_DEPTH}."
            )

        # Guard: max comments per article per reader
        existing_qs = Comment.objects.filter(  # Build a queryset with WHERE
            article=self.article,
            author=self.author,
        )
        if self.pk:
            # Exclude the current comment row when validating an update to an
            # existing comment.
            existing_qs = existing_qs.exclude(pk=self.pk)

        # Count how many matching comments already exist for this reader/article
        # pair, then compare that total to the configured per-article limit.
        if existing_qs.count() >= self.MAX_COMMENTS_PER_ARTICLE:
            raise ValidationError(
                f"You may not post more than "
                f"{self.MAX_COMMENTS_PER_ARTICLE} comments on a "
                f"single article."
            )

        # Guard: max combined word count per article per reader
        used_words = sum(
            self._word_count(c.body) for c in existing_qs
        )
        new_words = self._word_count(self.body)
        if used_words + new_words > self.MAX_WORDS_PER_ARTICLE:
            remaining = self.MAX_WORDS_PER_ARTICLE - used_words
            raise ValidationError(
                f"Your combined comment word count on this article "
                f"would exceed {self.MAX_WORDS_PER_ARTICLE} words. "
                f"You have {remaining} word(s) remaining."
            )

    def save(self, *args, **kwargs):
        """Run full validation (including :meth:`clean`) before saving."""
        # Django does NOT call full_clean() automatically before save(), so we
        # call it explicitly here to ensure the depth / word-count / comment-count
        # constraints in clean() are enforced even when comments are created
        # programmatically (e.g. in tests or management commands, not via a form).
        # Cf.: https://docs.djangoproject.com/en/5.2/ref/models/instances/#django.db.models.Model.full_clean
        self.full_clean()
        # After validation, defer to Django's normal save implementation so the
        # row is actually written and any built-in save hooks still run.
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"Comment by {self.author} on "
            f"'{self.article}' (depth {self.depth})"
        )


# ---------------------------------------------------------------------------
# ArticleNotification
# ---------------------------------------------------------------------------

class ArticleNotification(models.Model):
    """A pending announcement delivered to a user when an article is approved.

    One row is created per recipient each time an article transitions from
    pending/returned to approved.  Recipients are:

    * The article's author (the journalist who wrote it).
    * Every reader who subscribes to the article's publisher.
    * Every reader who subscribes to the article's author directly.

    ``seen_at`` is null while the notification is unread.  It is stamped
    with the current timestamp the first time the recipient views the
    announcement page, via :meth:`mark_seen`.

    How the notification lifecycle works in this project:

    1. When an article is approved, the post-save approval receiver creates
       one ``ArticleNotification`` row per recipient.
    2. On login, the app checks for unread rows and can redirect the user to
       the announcement page before sending them to their normal destination.
       (Cf. DailyIndabaLoginView.get_success_url() in accounts/views.py)
    3. Viewing or dismissing the announcement marks the row as seen by
       stamping ``seen_at``.

    The login view checks for unseen rows and redirects the user to the
    announcement page before sending them to their normal post-login
    destination.

    Attributes
    ----------
    article:    The article that was approved.
    recipient:  The user who should see this notification.
    seen_at:    Timestamp when the notification was first viewed; null = unread.
    created_at: Timestamp recorded automatically on creation.
    """

    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="article_notifications",
    )
    # Null while unread; stamped by mark_seen() on first view.
    seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One notification row per (article, recipient) pair — prevents
            # duplicate entries if the signal fires more than once.
            models.UniqueConstraint(
                fields=["article", "recipient"],
                name="unique_article_notification_per_recipient",
            )
        ]

    def mark_seen(self):
        """Stamp seen_at with the current time if not already seen."""
        if self.seen_at is None:
            self.seen_at = timezone.now()
            self.save(update_fields=["seen_at"])

    @property
    def is_unread(self):
        """Return True when the notification has not yet been viewed."""
        return self.seen_at is None

    def __str__(self):
        state = "unread" if self.is_unread else "seen"
        return f"Notification({self.recipient}, article={self.article_id}, {state})"
