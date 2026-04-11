"""
Accounts models for The Daily Indaba news platform.

Models:
- SubscriptionPricingPolicy: Singleton config for all subscription fees
  (journalist, publisher, and all-articles plan). Single source of truth
  for monetary rules.
- User: Custom AUTH_USER_MODEL extending AbstractUser. Adds role
  (reader/journalist/editor/publisher), profile fields, pricing fields,
  and reader subscription links. Auto-syncs Django auth-group on save.
- TermsAcceptance: Audit record of a user's T&C acceptance at
  registration, capturing role and version accepted.
"""

# Standard-library logging is used in assign_to_role_group() to warn when a
# required auth group is absent rather than raising an uncaught exception.
import logging   # Cf. https://docs.python.org/3/library/logging.html
# Decimal is used for subscription fees to avoid floating-point rounding errors.
from decimal import Decimal

# settings.AUTH_USER_MODEL is used in FK declarations instead of importing the
# User class directly, to prevent circular imports and to stay compatible when
# the AUTH_USER_MODEL setting is swapped.
# Cf.: https://docs.djangoproject.com/en/5.2/topics/auth/customizing/#substituting-a-custom-user-model
from django.conf import settings
# ValidationError is raised when pricing data violates a business rule.
# MinValueValidator is used on the pricing-policy model itself so stored
# monetary values cannot be negative.
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
# AbstractUser provides the standard Django username/password/email fields plus
# group and permission hooks. Extending it preserves all built-in auth
# machinery while allowing custom fields and role logic to be added.
# Group is Django's built-in permission group model used in assign_to_role_group().
# Cf.: https://docs.djangoproject.com/en/5.2/topics/auth/customizing/#extending-the-existing-user-model
from django.contrib.auth.models import AbstractUser, Group
# Base class for all Django database models.
from django.db import OperationalError, ProgrammingError, models


# =============================================================================
# DERIVED REQUIREMENT - Keep all user-facing subscription pricing in one seeded
# configuration model so monetary rules are not duplicated across User,
# Publisher, forms, views, and templates.
# =============================================================================
class SubscriptionPricingPolicy(models.Model):
    """
    Seeded singleton-style pricing configuration for subscription products.

    The project supports three pricing concepts:

    - the journalist fee range (minimum / default / maximum)
    - the publisher fee range (minimum / default / maximum)
    - the flat all-articles monthly plan

    Keeping those values in one row makes the fee rules editable via seed data
    without scattering the same constants across multiple models and views.
    """

    DEFAULT_SLUG = "default"

    slug = models.SlugField(
        max_length=50,
        unique=True,
        default=DEFAULT_SLUG,
        editable=False,
    )
    journalist_min_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("30.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    journalist_default_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("30.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    journalist_max_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("50.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    publisher_min_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("80.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    publisher_default_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("80.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    publisher_max_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("120.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    all_articles_monthly_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("200.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    class Meta:
        verbose_name = "subscription pricing policy"
        verbose_name_plural = "subscription pricing policy"

    def clean(self):
        """Enforce sensible min/default/max ordering for each fee family."""
        super().clean()
        errors = {}

        if self.journalist_min_fee > self.journalist_default_fee:
            errors["journalist_default_fee"] = (
                "Journalist default fee must be at least the minimum fee."
            )
        if self.journalist_default_fee > self.journalist_max_fee:
            errors["journalist_default_fee"] = (
                "Journalist default fee must not exceed the maximum fee."
            )
        if self.journalist_min_fee > self.journalist_max_fee:
            errors["journalist_max_fee"] = (
                "Journalist maximum fee must be at least the minimum fee."
            )

        if self.publisher_min_fee > self.publisher_default_fee:
            errors["publisher_default_fee"] = (
                "Publisher default fee must be at least the minimum fee."
            )
        if self.publisher_default_fee > self.publisher_max_fee:
            errors["publisher_default_fee"] = (
                "Publisher default fee must not exceed the maximum fee."
            )
        if self.publisher_min_fee > self.publisher_max_fee:
            errors["publisher_max_fee"] = (
                "Publisher maximum fee must be at least the minimum fee."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Persist the singleton row with validation."""
        if not self.slug:
            self.slug = self.DEFAULT_SLUG
        self.full_clean()
        # Developer's note: full_clean is Django’s higher-level validation
        # that calls clean() as part of its process
        super().save(*args, **kwargs)

    def __str__(self):
        """Return a human-readable label for the pricing policy.

        :rtype: str
        """
        return "Daily Indaba subscription pricing"


def get_subscription_pricing_policy():
    """
    Return the active pricing-policy row or an unsaved default instance.

    The fallback instance keeps tests and fresh databases usable before seed
    data has been loaded. Database-read failures are tolerated during early
    migration phases when the table may not exist yet.
    """
    try:
        # Build a queryset with WHERE slug = DEFAULT_SLUG, then return the
        # first matching pricing-policy row or None.
        policy = SubscriptionPricingPolicy.objects.filter(
            slug=SubscriptionPricingPolicy.DEFAULT_SLUG
        ).first()
    except (OperationalError, ProgrammingError):
        return SubscriptionPricingPolicy()
    return policy or SubscriptionPricingPolicy()


def get_journalist_fee_bounds():
    """Return the configured journalist min/default/max fees."""
    policy = get_subscription_pricing_policy()
    return (
        policy.journalist_min_fee,
        policy.journalist_default_fee,
        policy.journalist_max_fee,
    )


def get_publisher_fee_bounds():
    """Return the configured publisher min/default/max fees."""
    policy = get_subscription_pricing_policy()
    return (
        policy.publisher_min_fee,
        policy.publisher_default_fee,
        policy.publisher_max_fee,
    )


def get_default_journalist_monthly_fee():
    """Return the configured default journalist monthly fee."""
    return get_journalist_fee_bounds()[1]


def get_default_publisher_monthly_fee():
    """Return the configured default publisher monthly fee."""
    return get_publisher_fee_bounds()[1]


def get_all_articles_monthly_fee():
    """Return the configured flat monthly fee for the all-articles plan."""
    return get_subscription_pricing_policy().all_articles_monthly_fee


def validate_journalist_fee(value):
    """Raise ValidationError when a journalist fee falls outside policy."""
    minimum, _, maximum = get_journalist_fee_bounds()
    if value < minimum or value > maximum:
        raise ValidationError(
            (
                "Journalist monthly fee must be between "
                f"R{minimum:.2f} and R{maximum:.2f}."
            )
        )


def validate_publisher_fee(value):
    """Raise ValidationError when a publisher fee falls outside policy."""
    minimum, _, maximum = get_publisher_fee_bounds()
    if value < minimum or value > maximum:
        raise ValidationError(
            (
                "Publisher monthly fee must be between "
                f"R{minimum:.2f} and R{maximum:.2f}."
            )
        )


# =============================================================================
# CORE REQUIREMENT - Custom AUTH_USER_MODEL with explicit Reader / Journalist /
# Editor / Publisher roles so the capstone's role-based access model is
# represented in one authoritative user table.
# =============================================================================
# Start with Django's built-in user fields (through AbstractUser), then add
# the extra Daily Indaba fields this project needs.
class User(AbstractUser):
    """
    Custom user model for The Daily Indaba news platform.

    Differentiates between 'reader', 'journalist', 'editor', and 'publisher'
    roles.

    Extends Django's ``AbstractUser``, so all standard fields (``username``,
    ``email``, ``password``, ``first_name``, ``last_name``, ``is_staff``,
    ``is_active``, ``date_joined``, etc.) are inherited and not repeated here.
    The additional fields below capture the user's role and optional public
    profile information.

    Cf. https://docs.djangoproject.com/en/5.2/topics/auth/customizing/
    https://www.geeksforgeeks.org/python/custom-user-models-in-django/:
    "Every Django project should implement a custom user model from the start.
    This approach avoids future issues and allows flexibility in
    authentication, user fields, and business logic."
    https://learndjango.com/tutorials/django-custom-user-model

    Fields (custom additions beyond ``AbstractUser``):
        role (str): Designates whether the account belongs to a ``"reader"``,
            ``"journalist"``, ``"editor"``, or ``"publisher"``. Defaults to
            ``"reader"``.
        display_name (str): Public display identity. Readers and journalists
            may use it as an optional pseudonym. Publisher accounts store the
            organisation name here. Editors may not use it. Blank by default.
        bio (str): Optional short biography displayed on the user's public
            profile. Most relevant for journalists and editors.
        profile_picture (ImageField | None): Avatar, headshot, or official
            organisation logo uploaded by the user, depending on role.
            Stored under ``profile_pictures/``. Optional for all roles.
            Requires Pillow (``pip install Pillow``).
        journalist_monthly_fee (Decimal): Monthly subscription fee in ZAR
            charged to readers who subscribe directly to a journalist.
            Applicable for journalist accounts only. The default and valid
            range come from :class:`SubscriptionPricingPolicy`.
        all_articles_plan (bool): Reader-plan flag indicating whether the user
            pays the configured all-articles flat rate instead of relying only
            on individual subscriptions. Defaults to ``False``.
        subscribed_publishers: Reader subscriptions to publisher content
            sources, exposed directly on the custom user model as required by
            the capstone brief and backed by the
            ``daily_indaba.Subscription`` through table.
        subscribed_journalists: Reader subscriptions to individual
            journalist sources, likewise exposed as a first-class
            ``ManyToManyField`` on the custom user model and backed by the
            same explicit through model.

    Properties:
        profile_picture_url (str | None): The URL of the uploaded picture, or
            ``None`` when no picture has been uploaded.
        is_reader (bool): ``True`` when ``role == "reader"``.
        is_journalist (bool): ``True`` when ``role == "journalist"``.
        is_editor (bool): ``True`` when ``role == "editor"``.
        is_publisher (bool): ``True`` when ``role == "publisher"``.
        public_name (str): Role-aware display identity used publicly in
            by-lines, comments, and profile headings. Editors prefer their real
            full names; publisher accounts prefer ``display_name`` and fall
            back to the linked publisher name when needed; non-editors
            otherwise prefer ``display_name`` when present and fall back to
            ``username``.
    """

    ROLE_CHOICES = [
        ("reader", "Reader"),
        ("journalist", "Journalist"),
        ("editor", "Editor"),
        ("publisher", "Publisher"),
    ]

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="reader",
    )
    display_name = models.CharField(
        max_length=150,
        blank=True,
        help_text=(
            "Optional public pseudonym for readers and journalists, "
            "or the organisation name for publisher accounts. "
            "Not available to editors."
        ),
    )
    bio = models.TextField(blank=True)
    profile_picture = models.ImageField(
        upload_to="profile_pictures/",
        blank=True,
        null=True,
    )
    journalist_monthly_fee = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=get_default_journalist_monthly_fee,
        help_text=(
            "Monthly subscription fee (ZAR) charged to readers who subscribe "
            "to this journalist directly. Applicable for role='journalist' "
            "only. The valid range is defined by the subscription pricing "
            "policy seed data."
        ),
    )
    all_articles_plan = models.BooleanField(
        default=False,
        help_text=(
            "When True, the reader pays the configured all-articles monthly "
            "rate and has full access to all articles regardless of "
            "individual subscriptions. Applicable for role='reader' only."
        ),
    )
    # The capstone brief explicitly calls for ManyToManyField relationships on
    # the custom user model for reader subscriptions. That sits in some
    # tension with the brief's separate normalisation requirement, so the live
    # code tilts toward the brief's explicit wording here while preserving the
    # normalised join-table design underneath via the Subscription through
    # model.
    #
    # Django normalises ManyToManyField relationships either way by storing the
    # links in an intermediate table. When no custom intermediary is supplied,
    # Django creates that model/table automatically; `through=` and
    # `through_fields=` are used here to select the existing Subscription
    # intermediary explicitly instead of letting Django generate an anonymous
    # one.
    #
    # Official docs:
    # https://docs.djangoproject.com/en/5.2/ref/models/fields/#django.db.models.ManyToManyField.through
    # https://docs.djangoproject.com/en/5.2/ref/models/fields/#django.db.models.ManyToManyField.through_fields
    # Background explanation:
    # https://medium.com/@siklab/demystifying-many-to-many-relationships-with-through-fields-in-django-a-beginners-guide-78a30a04e3f5
    subscribed_publishers = models.ManyToManyField(
        "daily_indaba.Publisher",
        through="daily_indaba.Subscription",
        through_fields=("reader", "publisher"),
        related_name="subscribed_readers",
        blank=True,
        help_text=(
            "Publishers this reader subscribes to. Exposed directly on the "
            "custom user model to satisfy the capstone brief and backed by "
            "the explicit Subscription through model."
        ),
    )
    subscribed_journalists = models.ManyToManyField(
        "self",
        through="daily_indaba.Subscription",
        through_fields=("reader", "journalist"),
        symmetrical=False,
        related_name="subscriber_readers",
        blank=True,
        limit_choices_to={"role": "journalist"},
        help_text=(
            "Journalists this reader subscribes to. Exposed directly on the "
            "custom user model to satisfy the capstone brief and backed by "
            "the explicit Subscription through model."
        ),
    )

    class Meta(AbstractUser.Meta):
        pass

    @property
    def profile_picture_url(self):
        """Return the uploaded profile picture URL when one exists.

        :return: The URL string of the profile picture, or ``None`` when
            no picture has been uploaded.
        :rtype: str or None
        """
        if self.profile_picture:
            return self.profile_picture.url
        return None

    @property
    def is_reader(self):
        """Return ``True`` when this account has the reader role.

        :rtype: bool
        """
        return self.role == "reader"

    @property
    def is_journalist(self):
        """Return ``True`` when this account has the journalist role.

        :rtype: bool
        """
        return self.role == "journalist"

    @property
    def is_editor(self):
        """Return ``True`` when this account has the editor role.

        :rtype: bool
        """
        return self.role == "editor"

    @property
    def is_publisher(self):
        """Return ``True`` when this account has the publisher role.

        :rtype: bool
        """
        return self.role == "publisher"

    @property
    def public_name(self):
        """Return the role-appropriate public identity string.

        Use this property everywhere a user's name is displayed publicly
        (by-lines, comment authors, profile headings). Editors show their
        full real name when available because pseudonyms are not permitted
        for that role.

        :return: The user's display name, organisation name, or username,
            depending on their role and what profile fields are populated.
        :rtype: str
        """
        if self.role == "editor":
            full_name = self.get_full_name().strip()
            if full_name:
                return full_name
        if self.role == "publisher":
            if self.display_name:
                return self.display_name
            publisher = getattr(self, "managed_publisher", None)
            if publisher is not None:
                return publisher.name
            full_name = self.get_full_name().strip()
            if full_name:
                return full_name
        if self.role != "editor" and self.display_name:
            return self.display_name
        return self.username

    def clean(self):
        """Validate that the journalist fee falls within the seeded policy range.

        :raises ValidationError: If ``journalist_monthly_fee`` is outside the
            configured minimum / maximum bounds.
        :rtype: None
        """
        super().clean()
        validate_journalist_fee(self.journalist_monthly_fee)

    # =========================================================================
    # CORE REQUIREMENT - Assign every account to the Django auth group that
    # matches its role so the brief's Reader / Journalist / Editor permission
    # matrix is reflected in Django's built-in authorisation system.
    # =========================================================================
    def assign_to_role_group(self):
        """
        Synchronise this user with the Django auth group for their role.

        Readers -> ``Readers``, journalists -> ``Journalists``, editors ->
        ``Editors``, and publishers -> ``Publishers``. All other role groups
        are removed so the user's auth-group state stays aligned with ``role``.

        If the target group is not present yet (e.g. before migrations have
        run), the assignment is skipped and a warning is logged rather than
        raising an exception.
        """
        group_name_by_role = {
            "reader": "Readers",
            "journalist": "Journalists",
            "editor": "Editors",
            "publisher": "Publishers",
        }
        target_group_name = group_name_by_role.get(self.role)
        if not target_group_name:
            return

        logger = logging.getLogger(__name__)
        # .filter().first() is preferred over .get() so that a missing group
        # returns None instead of raising Group.DoesNotExist. This makes the
        # method safe to call before migrations have fully completed.
        try:
            target_group = Group.objects.filter(name=target_group_name).first()
        except (OperationalError, ProgrammingError):
            logger.info(
                "Skipping %s group assignment because auth tables are not "
                "ready yet.",
                target_group_name,
            )
            return
        if not target_group:
            logger.info(
                "Skipping %s group assignment because the group is absent.",
                target_group_name,
            )
            return

        # Remove the user from all OTHER role groups before adding the new one.
        # Set subtraction (full set - {target}) gives the names of groups the
        # user should no longer belong to.
        # self.groups is a Django ManyToManyField; .remove(*iterable) and
        # .add(obj) issue the corresponding DELETE and INSERT rows in the
        # join table.
        # Cf.: https://docs.djangoproject.com/en/5.2/topics/db/examples/many_to_many/
        other_groups = Group.objects.filter(  # Build a queryset with WHERE
            name__in=set(group_name_by_role.values()) - {target_group_name}
        )
        # Determine whether at least one non-target role group is currently
        # attached to this user.
        if other_groups.exists():
            self.groups.remove(*other_groups)
        self.groups.add(target_group)

    # =========================================================================
    # DERIVED REQUIREMENT - Keep the persisted role field and Django group
    # membership synchronised automatically after every save so admin edits,
    # fixtures, and shell-created users cannot drift out of alignment.
    # =========================================================================
    def save(self, *args, **kwargs):
        """
        Persist the user and keep Django auth groups aligned with ``role``.

        Registration was already calling :meth:`assign_to_role_group`, but
        admin edits, shell scripts, fixtures created with ``create_user()``,
        and any future self-service role-management flow all save ``User``
        instances too. Keeping the group sync here turns it into a model-level
        safety net so the role/group invariant does not depend on every caller
        remembering to invoke an extra method afterwards.
        """
        update_fields = kwargs.get("update_fields")
        if (
            self._state.adding
            or update_fields is None
            or "journalist_monthly_fee" in update_fields
        ):
            validate_journalist_fee(self.journalist_monthly_fee)
        super().save(*args, **kwargs)
        self.assign_to_role_group()


TERMS_VERSIONS = {
    "reader": "reader-v1",
    "journalist": "journalist-v1",
    "editor": "editor-v1",
    "publisher": "publisher-v1",
}


# =============================================================================
# DERIVED REQUIREMENT - Persist role-specific electronic terms acceptance as an
# auditable database record rather than leaving consent as a transient form
# checkbox during registration only.
# =============================================================================
class TermsAcceptance(models.Model):
    """
    Records that a user electronically accepted the role-specific terms and
    conditions at the time of registration.

    Fields:
        user (User): The account that accepted the terms. One-to-one because
            a single acceptance record per user is expected at registration.
        role (str): The user's role at the time of acceptance (immutable
            snapshot in case the role later changes).
        terms_version (str): The identifier of the version accepted, e.g.
            ``"reader-v1"``. Allows future T&C re-acceptance flows when a
            new version is published.
        accepted_at (datetime): Timestamp of acceptance set automatically.
    """

    # OneToOneField is a ForeignKey with a unique=True constraint enforced at
    # the database level. It ensures exactly one TermsAcceptance row per user.
    # Cf.: https://docs.djangoproject.com/en/5.2/topics/db/examples/one_to_one/
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="terms_acceptance",
    )
    role = models.CharField(max_length=20)
    terms_version = models.CharField(max_length=20)
    accepted_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def record_for(cls, user):
        """Create a T&C acceptance record for *user* at registration.

        Derives the terms version from ``TERMS_VERSIONS`` based on the
        user's current role, falling back to ``"<role>-v1"`` for
        unrecognised roles.

        :param user: The newly registered user.
        :type user: User
        :return: The saved acceptance record.
        :rtype: TermsAcceptance
        """
        version = TERMS_VERSIONS.get(user.role, f"{user.role}-v1")
        return cls.objects.create(
            user=user,
            role=user.role,
            terms_version=version,
        )

    def __str__(self):
        """Return a human-readable summary of the acceptance record.

        :rtype: str
        """
        return (
            f"{self.user} accepted {self.terms_version}"
            f" on {self.accepted_at:%Y-%m-%d}"
        )
