"""Permission policy layer for the Daily Indaba REST API.

This module gathers the custom Django REST framework permission classes used by
the article and newsletter endpoints. The aim is to keep the API's access
rules in one place so the views can declare *which* policy applies without
repeating the underlying role checks, Django model-permission checks, or
object-scope decisions in every action.

The implementation deliberately combines three layers of authorisation:

- authentication: anonymous requests are rejected before any role-specific
  behaviour is considered;
- Django permissions: ``user.has_perm(...)`` enforces the same permission model
  carried by the synchronised role groups;
- object scope: article and newsletter ownership, editorial oversight, and
  publisher-assignment rules are enforced against the concrete record being
  accessed.

This split mirrors DRF's own permission flow, where ``has_permission(...)``
runs before the view resolves the object and ``has_object_permission(...)``
runs after the object is available. Keeping that logic here makes the API
behaviour easier to review against the capstone requirements and keeps the
browser and API layers aligned on the same editorial boundaries.
"""

from rest_framework import permissions
# Cf. https://www.django-rest-framework.org/api-guide/permissions/#custom-permissions

from .views.helpers import _editor_can_manage_article


# =============================================================================
# DERIVED REQUIREMENT - Keep the REST API authorisation rules in one module so
# article and newsletter endpoints can share the same role, group-permission,
# and object-scope decisions without duplicating them in every view method.
# =============================================================================
APP_LABEL = "daily_indaba"


def _has_permission(user, codename):
    """Return True when *user* has the named model permission."""
    # user.has_perm(...) resolves both direct and group-derived permissions, so
    # this helper lets DRF enforce the same Django auth model configured in the
    # synchronised role groups.
    return user.is_authenticated and user.has_perm(f"{APP_LABEL}.{codename}")


# -----------------------------------------------------------------------------
# READ PERMISSIONS
# -----------------------------------------------------------------------------
class HasArticleViewPermission(permissions.BasePermission):
    """Require the standard article-view permission."""

    message = "You do not have permission to view articles."

    def has_permission(self, request, view):
        # Request-level read access is controlled entirely by the standard
        # Django model permission for articles.
        return _has_permission(request.user, "view_article")


class HasNewsletterViewPermission(permissions.BasePermission):
    """Require the standard newsletter-view permission."""

    message = "You do not have permission to view newsletters."

    def has_permission(self, request, view):
        # Readers, journalists, editors, and publishers all flow through the
        # same newsletter view permission rather than ad-hoc role checks.
        return _has_permission(request.user, "view_newsletter")


# -----------------------------------------------------------------------------
# ARTICLE WRITE / APPROVAL PERMISSIONS
# -----------------------------------------------------------------------------
class CanCreateArticle(permissions.BasePermission):
    """Allow article creation only for journalists with the right permission."""

    message = "Only journalists can create articles."

    def has_permission(self, request, view):
        user = request.user
        # Article creation is limited to journalist accounts that also carry
        # the expected Django model permission from the Journalists group.
        return (
            user.is_authenticated
            and user.role == "journalist"
            and _has_permission(user, "add_article")
        )


class CanAccessSubscribedArticles(permissions.BasePermission):
    """Allow the subscribed-feed endpoint only for readers."""

    message = "Only readers can access subscribed articles."

    def has_permission(self, request, view):
        user = request.user
        # The subscribed feed is a reader-specific view on top of the normal
        # article visibility rules, so the endpoint remains closed to other
        # authenticated roles.
        return (
            user.is_authenticated
            and user.role == "reader"
            and _has_permission(user, "view_article")
        )


class CanUpdateArticle(permissions.BasePermission):
    """Allow article updates only for permitted journalists or editors."""

    message = "You are not allowed to update articles."

    def has_permission(self, request, view):
        user = request.user
        # DRF evaluates has_permission(...) before looking up the target object.
        # That keeps obvious role rejections cheap and makes the error message
        # explicit before any article-specific scope logic runs.
        if not user.is_authenticated:
            return False
        if user.role == "reader":
            self.message = "Readers cannot update articles."
            return False
        if user.role in {"journalist", "editor"} and _has_permission(
            user,
            "change_article",
        ):
            return True
        return False

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == "journalist":
            # Journalists are limited to their own drafts; once an article is
            # approved, later editorial changes belong to the editor workflow.
            if obj.author != user:
                self.message = "You can only update your own articles."
                return False
            if obj.approved:
                self.message = "Approved articles cannot be edited by journalists."
                return False
            return True
        if user.role == "editor":
            # Editors may edit across authors, but only for articles that fall
            # inside the publisher scope they curate.
            if not _editor_can_manage_article(user, obj):
                self.message = "You are not assigned to curate this article."
                return False
            return True
        self.message = "Readers cannot update articles."
        return False


class CanDeleteArticle(permissions.BasePermission):
    """Allow article deletion only for permitted journalists or editors."""

    message = "You are not allowed to delete articles."

    def has_permission(self, request, view):
        user = request.user
        # Delete uses the same role split as update, but maps to Django's
        # delete permission so the group layer remains meaningful at runtime.
        if not user.is_authenticated:
            return False
        if user.role == "reader":
            self.message = "Readers cannot delete articles."
            return False
        if user.role in {"journalist", "editor"} and _has_permission(
            user,
            "delete_article",
        ):
            return True
        return False

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == "journalist":
            # Journalists may remove only their own unapproved work. Once an
            # article is approved, removal becomes an editorial action rather
            # than an author-draft action.
            if obj.author != user:
                self.message = "You can only delete your own articles."
                return False
            if obj.approved:
                self.message = (
                    "Approved articles cannot be deleted by journalists."
                )
                return False
            return True
        if user.role == "editor":
            # Editors may delete within the same curation scope used elsewhere.
            if not _editor_can_manage_article(user, obj):
                self.message = "You are not assigned to curate this article."
                return False
            return True
        self.message = "Readers cannot delete articles."
        return False


class CanApproveArticle(permissions.BasePermission):
    """Allow article approval only for editors."""

    message = "Only editors can approve articles."

    def has_permission(self, request, view):
        user = request.user
        # Approval is an editor-only action and deliberately reuses the normal
        # article change permission from the Editors group.
        return (
            user.is_authenticated
            and user.role == "editor"
            and _has_permission(user, "change_article")
        )

    def has_object_permission(self, request, view, obj):
        # Editors cannot approve articles outside the publishers they curate.
        if not _editor_can_manage_article(request.user, obj):
            self.message = "You are not assigned to curate this article."
            return False
        return True


# -----------------------------------------------------------------------------
# NEWSLETTER WRITE PERMISSIONS
# -----------------------------------------------------------------------------
class CanCreateNewsletter(permissions.BasePermission):
    """Allow newsletter creation only for journalists or editors."""

    message = "Only journalists and editors can create newsletters."

    def has_permission(self, request, view):
        user = request.user
        # Both journalists and editors may author newsletters, but the API
        # still checks the concrete Django permission rather than trusting the
        # role field alone.
        return (
            user.is_authenticated
            and user.role in {"journalist", "editor"}
            and _has_permission(user, "add_newsletter")
        )


class CanUpdateNewsletter(permissions.BasePermission):
    """Allow newsletter updates for editors or the owning journalist."""

    message = "You are not allowed to update newsletters."

    def has_permission(self, request, view):
        user = request.user
        # Request-level gate: only the two authoring roles may continue to
        # the object-level ownership or editor-oversight check.
        return (
            user.is_authenticated
            and user.role in {"journalist", "editor"}
            and _has_permission(user, "change_newsletter")
        )

    def has_object_permission(self, request, view, obj):
        user = request.user
        # Editors retain global newsletter oversight across authors.
        if user.role == "editor":
            return True
        # Journalists may only edit newsletters they authored themselves.
        if obj.author != user:
            self.message = "You can only update your own newsletters."
            return False
        return True


class CanDeleteNewsletter(permissions.BasePermission):
    """Allow newsletter deletion for editors or the owning journalist."""

    message = "You are not allowed to delete newsletters."

    def has_permission(self, request, view):
        user = request.user
        # Delete mirrors update so the role and permission story stays
        # consistent across newsletter write operations.
        return (
            user.is_authenticated
            and user.role in {"journalist", "editor"}
            and _has_permission(user, "delete_newsletter")
        )

    def has_object_permission(self, request, view, obj):
        user = request.user
        # Editors may delete any newsletter; journalists are limited to their
        # own authored editions.
        if user.role == "editor":
            return True
        if obj.author != user:
            self.message = "You can only delete your own newsletters."
            return False
        return True
