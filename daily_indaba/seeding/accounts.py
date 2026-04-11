"""
Account- and publisher-related seed steps for Daily Indaba demo data.

This module groups the seed stages that primarily create platform accounts,
publishers, subscriptions, and pricing records. Keeping them together makes
the user/publisher side of the data graph easier to reason about in isolation.
"""

from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model

from accounts.models import SubscriptionPricingPolicy
from daily_indaba.models import Publisher, Subscription

if TYPE_CHECKING:
    from .demo_news import SeedDependencies

# Use the project's active custom user model, not Django's default auth user.
User = get_user_model()


def _seed_pricing(data, update_existing: bool) -> bool:
    """
    Seed or update the single SubscriptionPricingPolicy row.

    There is only ever one pricing-policy row (identified by DEFAULT_SLUG), so
    get_or_create is used rather than a loop.

    Returns True if the row was newly created, False if it already existed.
    """
    # ------------------------------------------------------------------
    # Subscription pricing policy
    # ------------------------------------------------------------------

    # ================================
    # Load pricing values and model defaults
    # ================================
    # Pull any pricing overrides from the seed JSON. Missing keys are handled
    # below by falling back to the model's built-in defaults.
    pricing_data = data.get("subscription_pricing", {})
    # Build an unsaved model instance so the seed logic can reuse the field
    # defaults defined on SubscriptionPricingPolicy itself.
    policy_defaults = SubscriptionPricingPolicy()

    # ================================
    # Retrieve or create the pricing policy row
    # ================================
    # There is exactly one pricing-policy row, so DEFAULT_SLUG acts as the
    # natural key for idempotent seeding.
    policy, created_pricing = SubscriptionPricingPolicy.objects.get_or_create(
        slug=SubscriptionPricingPolicy.DEFAULT_SLUG,
        defaults={
            # Each field uses the seed value when provided, otherwise the model
            # default from the unsaved helper instance above.
            "journalist_min_fee": pricing_data.get(
                "journalist_min_fee",
                policy_defaults.journalist_min_fee,
            ),
            "journalist_default_fee": pricing_data.get(
                "journalist_default_fee",
                policy_defaults.journalist_default_fee,
            ),
            "journalist_max_fee": pricing_data.get(
                "journalist_max_fee",
                policy_defaults.journalist_max_fee,
            ),
            "publisher_min_fee": pricing_data.get(
                "publisher_min_fee",
                policy_defaults.publisher_min_fee,
            ),
            "publisher_default_fee": pricing_data.get(
                "publisher_default_fee",
                policy_defaults.publisher_default_fee,
            ),
            "publisher_max_fee": pricing_data.get(
                "publisher_max_fee",
                policy_defaults.publisher_max_fee,
            ),
            "all_articles_monthly_fee": pricing_data.get(
                "all_articles_monthly_fee",
                policy_defaults.all_articles_monthly_fee,
            ),
        },
    )

    # ================================
    # Update existing pricing fields when requested
    # ================================
    if update_existing:
        changed = False
        for field in (
            "journalist_min_fee",
            "journalist_default_fee",
            "journalist_max_fee",
            "publisher_min_fee",
            "publisher_default_fee",
            "publisher_max_fee",
            "all_articles_monthly_fee",
        ):
            # Skip fields omitted from the seed snapshot so partial pricing
            # payloads do not overwrite existing values unintentionally.
            value = pricing_data.get(field)
            if value is None:
                continue
            # Run the seed value through Django's field parser so comparisons
            # use the model's real Python type (for example Decimal).
            python_value = policy._meta.get_field(field).to_python(value)
            if getattr(policy, field) != python_value:
                setattr(policy, field, python_value)
                changed = True
        if changed:
            # Save once after the loop instead of issuing one UPDATE per field.
            policy.save()

    return created_pricing


def _seed_users(
    data,
    password: str,
    update_existing: bool,
    dependencies: "SeedDependencies",
):
    """
    Seed platform user accounts (readers, journalists, editors).

    All demo accounts share the supplied password. assign_to_role_group() is
    called for every user, created or updated, so Django auth groups stay in
    sync with the user's role field.
    """
    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    users_by_username = {}
    created_users = 0

    for user_data in data.get("users", []):
        # ================================
        # Retrieve or create the user row
        # ================================
        # Username is the stable natural key for demo accounts, so reruns reuse
        # the same database row instead of creating duplicates.
        user_obj, created = User.objects.get_or_create(
            username=user_data["username"],
            defaults={
                "email": user_data.get("email", ""),
                "first_name": user_data.get("first_name", ""),
                "last_name": user_data.get("last_name", ""),
                "role": user_data.get("role", "reader"),
                "display_name": user_data.get("display_name", ""),
                "bio": user_data.get("bio", ""),
            },
        )
        if created:
            # Hash the shared demo password before saving it to the database.
            user_obj.set_password(password)
            user_obj.save()
            created_users += 1

        # ================================
        # Update existing user fields when requested
        # ================================
        elif update_existing:
            changed = False
            for field in (
                "email",
                "first_name",
                "last_name",
                "role",
                "display_name",
                "bio",
            ):
                # If the seed omits a field, preserve the current database
                # value rather than blanking it out.
                value = user_data.get(field, getattr(user_obj, field))
                if getattr(user_obj, field) != value:
                    setattr(user_obj, field, value)
                    changed = True
            if changed:
                # Persist all changed profile fields in one save().
                user_obj.save()

        # ================================
        # Sync role membership and profile picture
        # ================================
        # Keep Django auth groups aligned with the custom role field even on
        # re-seeds of existing users.
        user_obj.assign_to_role_group()
        # Attach or clear the profile picture so media matches the current seed
        # snapshot.
        picture_file = user_data.get("profile_picture")
        dependencies.sync_profile_picture(
            user_obj,
            picture_file,
            update_existing,
        )
        # Cache the user by username so later seed stages can resolve foreign
        # keys without extra database queries.
        users_by_username[user_obj.username] = user_obj

    return users_by_username, created_users


def _seed_publishers(
    data,
    users_by_username,
    update_existing: bool,
):
    """
    Seed Publisher rows and their editor/journalist M2M relationships.

    Also derives the editor lookup structures that article seeding needs to
    assign a deterministic approving editor to each article.
    """
    # ------------------------------------------------------------------
    # Publishers
    # ------------------------------------------------------------------
    publishers_by_name = {}
    publisher_editors_by_name = {}
    independent_editors = []
    created_publishers = 0

    for publisher_data in data.get("publishers", []):
        # ================================
        # Resolve publisher foreign-key dependencies
        # ================================
        # Some publishers have a linked login account; others are just content
        # entities with no dedicated user row.
        account = (
            users_by_username.get(publisher_data["account"])
            if publisher_data.get("account")
            else None
        )
        # Independent-journalist curation affects which editors can later be
        # chosen to approve publisher-less articles.
        curates_independent = publisher_data.get(
            "curates_independent_journalists",
            False,
        )

        # ================================
        # Retrieve or create the publisher row
        # ================================
        # Publisher name is used as the natural key because the seed snapshot
        # refers to publishers by name.
        pub, created = Publisher.objects.get_or_create(
            name=publisher_data["name"],
            defaults={
                "description": publisher_data.get("description", ""),
                "account": account,
                "curates_independent_journalists": curates_independent,
            },
        )
        if created:
                created_publishers += 1

        # ================================
        # Update existing publisher fields when requested
        # ================================
        elif update_existing:
            changed = False
            # Exact-sync mode aligns the simple scalar fields with the current
            # JSON snapshot.
            description = publisher_data.get("description", "")
            if pub.description != description:
                pub.description = description
                changed = True
            if pub.account_id != (account.pk if account else None):
                pub.account = account
                changed = True
            if pub.curates_independent_journalists != curates_independent:
                pub.curates_independent_journalists = curates_independent
                changed = True
            if changed:
                pub.save()

        # ================================
        # Sync publisher editor and journalist relationships
        # ================================
        # Resolve the M2M users from the username cache built during user
        # seeding. Unknown usernames are ignored so a partial seed file does
        # not crash here.
        editors = [
            users_by_username[username]
            for username in publisher_data.get("editors", [])
            if username in users_by_username
        ]
        journalists = [
            users_by_username[username]
            for username in publisher_data.get("journalists", [])
            if username in users_by_username
        ]
        if created or update_existing or "editors" in publisher_data:
            # set(...) makes the M2M join table exactly match the seed list.
            pub.editors.set(editors)
        if created or update_existing or "journalists" in publisher_data:
            # Do the same exact-sync update for the publisher's journalist
            # roster when appropriate.
            pub.journalists.set(journalists)

        # Cache the publisher so later stages can resolve FKs and approval
        # editors without repeated queries.
        publishers_by_name[pub.name] = pub
        # Keep a publisher -> editors lookup for deterministic approval-editor
        # selection during article seeding.
        publisher_editors_by_name[pub.name] = editors
        if pub.curates_independent_journalists:
            # These editors are also eligible to approve independent articles.
            independent_editors.extend(editors)

    # ================================
    # Build approval-editor lookup collections
    # ================================
    # Remove duplicates while preserving first-seen order so deterministic
    # editor selection stays stable across re-runs.
    independent_editors = list(dict.fromkeys(independent_editors))
    # Keep a flat list of every editor user as the final fallback pool.
    editor_users = [
        user
        for user in users_by_username.values()
        if user.role == "editor"
    ]

    return (
        publishers_by_name,
        publisher_editors_by_name,
        independent_editors,
        editor_users,
        created_publishers,
    )


def _seed_subscriptions(
    data,
    users_by_username,
    publishers_by_name,
):
    """
    Seed Subscription rows linking readers to publishers or journalists.

    Subscriptions have no updatable fields beyond the FK pair, so there is no
    update_existing path. get_or_create is sufficient for idempotency.
    """
    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
    created_subscriptions = 0

    for subscription_data in data.get("subscriptions", []):
        # ================================
        # Resolve subscription foreign-key dependencies
        # ================================
        # Every subscription belongs to a reader account.
        reader = users_by_username.get(subscription_data["reader"])
        # Subscriptions may target either a publisher or a journalist.
        publisher = (
            publishers_by_name.get(subscription_data["publisher"])
            if subscription_data.get("publisher")
            else None
        )
        journalist = (
            users_by_username.get(subscription_data["journalist"])
            if subscription_data.get("journalist")
            else None
        )
        if reader and (publisher or journalist):
            # ================================
            # Retrieve or create the subscription row
            # ================================
            # get_or_create keeps reruns idempotent and prevents duplicate
            # subscriptions for the same reader/source pair.
            _, created = Subscription.objects.get_or_create(
                reader=reader,
                publisher=publisher,
                journalist=journalist,
            )
            if created:
                created_subscriptions += 1

    return created_subscriptions
