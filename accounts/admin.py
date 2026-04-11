# admin is Django's built-in administration site.
from django.contrib import admin
# BaseUserAdmin provides Django's stock auth-user admin screens; extending it
# is the standard way to add custom user-model fields without rebuilding the
# whole admin from scratch.
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import SubscriptionPricingPolicy, TermsAcceptance, User


# =============================================================================
# CORE REQUIREMENT - Administrators must be able to manage user accounts
# through Django admin, including role and profile fields.
# =============================================================================
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # Extend the stock auth admin layout with the extra profile fields defined
    # on the project's custom User model.
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Daily Indaba Profile",
            {"fields": (
                "role", "bio", "profile_picture"
            )},
        ),
    )
    # add_fieldsets controls the admin's "create user" screen, which is
    # separate from the normal "change user" fieldset layout above.
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Daily Indaba Profile", {"fields": ("role",)}),
    )
    # list_display chooses the columns shown on the user changelist page.
    list_display = (
        "username",
        "email",
        "role",
        "is_staff",
        "is_superuser",
        "date_joined",
    )
    # list_filter adds right-sidebar drill-down filters in the admin UI.
    list_filter = (
        "role", "is_staff", "is_superuser", "is_active"
    )
    # search_fields enables the admin search box to query these columns.
    search_fields = ("username", "email", "first_name", "last_name")


# =============================================================================
# DERIVED REQUIREMENT - Expose persisted terms-acceptance records in admin so
# consent state can be reviewed without inspecting the database manually.
# =============================================================================
@admin.register(TermsAcceptance)
class TermsAcceptanceAdmin(admin.ModelAdmin):
    # readonly_fields prevents staff from editing the audit trail manually.
    list_display = ("user", "role", "terms_version", "accepted_at")
    list_filter = ("role", "terms_version")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("user", "role", "terms_version", "accepted_at")


@admin.register(SubscriptionPricingPolicy)
class SubscriptionPricingPolicyAdmin(admin.ModelAdmin):
    # The slug identifies the singleton-style pricing row and should remain
    # stable once created.
    list_display = (
        "slug",
        "journalist_min_fee",
        "journalist_default_fee",
        "journalist_max_fee",
        "publisher_min_fee",
        "publisher_default_fee",
        "publisher_max_fee",
        "all_articles_monthly_fee",
    )
    readonly_fields = ("slug",)
