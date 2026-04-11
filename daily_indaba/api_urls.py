"""API URL routes for the Daily Indaba project."""

from django.urls import path

from .api_views import (
    ApiRootView,
    ArticleApproveAPIView,
    ArticleDetailAPIView,
    ArticleListCreateAPIView,
    MockAnnouncementAPIView,
    NewsletterDetailAPIView,
    NewsletterListCreateAPIView,
    SubscribedArticleListAPIView,
    TokenLoginView,
    TokenLogoutView,
)

app_name = "daily_indaba_api"

# =============================================================================
# CORE REQUIREMENT - URL surface for the required token-authenticated REST API
# endpoints covering article operations, subscribed feeds, approval, and
# token issuance. Newsletter endpoints are exposed as an extension so the
# newsletter serializer is reachable through the live API as well.
# =============================================================================
# `.as_view()` turns each APIView subclass into the callable Django view object
# that `path(...)` can route.
urlpatterns = [
    # JSON index of the mounted API endpoints for browser-based discovery.
    path("", ApiRootView.as_view(), name="api-root"),
    # Issue or retrieve a DRF auth token for username/password credentials.
    path("token/", TokenLoginView.as_view(), name="token"),
    # Delete the authenticated user's DRF token to fully invalidate it.
    # As per feedback to HyperionDev Task 14 (Django - eCommerce Application
    # Part 2): logout(request) ends the session but leaves the token active;
    # auth_token.delete() must be called to revoke API access.
    # Cf.: https://www.django-rest-framework.org/api-guide/authentication/
    #      #tokenauthentication
    #      https://dev.to/ebereplenty/how-to-log-users-out-in-django-rest-
    #      framework-drf-25ih
    path("token/logout/", TokenLogoutView.as_view(), name="token-logout"),
    path(
        "mock-announcements/",
        MockAnnouncementAPIView.as_view(),
        name="mock-announcement",
    ),
    path("articles/", ArticleListCreateAPIView.as_view(), name="article-list"),
    path(
        "articles/subscribed/",
        SubscribedArticleListAPIView.as_view(),
        name="article-subscribed",
    ),
    path(
        "articles/<int:pk>/",
        ArticleDetailAPIView.as_view(),
        name="article-detail",
    ),
    path(
        "articles/<int:pk>/approve/",
        ArticleApproveAPIView.as_view(),
        name="article-approve",
    ),
    path(
        "newsletters/",
        NewsletterListCreateAPIView.as_view(),
        name="newsletter-list",
    ),
    path(
        "newsletters/<int:pk>/",
        NewsletterDetailAPIView.as_view(),
        name="newsletter-detail",
    ),
]
