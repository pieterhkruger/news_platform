"""
REST API views for The Daily Indaba project.

Internal helpers (functions):
- _article_queryset: Base article queryset with select_related for all
  API endpoints.
- _article_serializer_context: Builds serializer context; injects the
  reader's subscription access sets for content-gating.
- _newsletter_queryset: Base newsletter queryset with select_related
  and prefetch_related for all API endpoints.
- _newsletter_serializer_context: Returns minimal serializer context
  for newsletter endpoints.
- _is_editor / _is_journalist / _is_reader: Role-check shortcuts used
  across queryset scoping and permission logic.

Auth endpoints:
- ApiRootView: GET a compact JSON index of the mounted API endpoints.
- TokenLoginView: POST credentials to obtain a DRF auth token; also
  returns user_id, username, and role in the response.
- TokenLogoutView: POST to delete the authenticated user's DRF token so
  it can no longer authenticate future API requests.

Article endpoints:
- ArticleListCreateAPIView: GET approved articles (editors also see
  pending); POST to create a new article (journalist only).
- SubscribedArticleListAPIView: GET approved articles from the
  reader's subscribed journalists and publishers only.
- ArticleDetailAPIView: GET/PUT/PATCH/DELETE a single article with
  role-appropriate read scoping and permission checks per method.
- ArticleApproveAPIView: POST to approve a pending article (editor
  only); runs the shared publish workflow.

Newsletter endpoints:
- NewsletterListCreateAPIView: GET all newsletters; POST to create a
  new one (journalist or editor only).
- NewsletterDetailAPIView: GET/PUT/PATCH/DELETE a single newsletter
  with role-appropriate permission checks per method.

Development helper:
- MockAnnouncementAPIView: POST mock article-announcement payloads for
  local dev/testing; no authentication required, no external calls.
"""

# Q objects let the ORM combine boolean predicates with `|` (OR) and `&`
# (AND) inside one query expression. Guest et al. present this exact pattern in
# Web Development with Django 6, Packt, pp. 186-188.
from django.db.models import Q
from django.shortcuts import get_object_or_404

# permissions provides AllowAny (for the login endpoint) and
# IsAuthenticated (project default).
from rest_framework import permissions, status
# Token is the DRF model storing one API token per user — stateless auth.
# Cf.: https://www.django-rest-framework.org/api-guide/
#      authentication/#tokenauthentication
from rest_framework.authtoken.models import Token
# ObtainAuthToken validates credentials and returns a token; subclassed here to
# add role info to the response payload.
from rest_framework.authtoken.views import ObtainAuthToken
# JSONParser restricts the login endpoint to JSON request bodies only.
from rest_framework.parsers import JSONParser
# Response serialises Python dicts to JSON via the configured renderer.
# Cf.: https://www.django-rest-framework.org/api-guide/responses/
from rest_framework.response import Response
from rest_framework.reverse import reverse
# APIView dispatches HTTP methods to same-named class methods and applies
# authentication/permission checks — the DRF equivalent of Django's View.
# Cf.: https://www.django-rest-framework.org/api-guide/views/
from rest_framework.views import APIView

from .api_permissions import (
    CanAccessSubscribedArticles,
    CanApproveArticle,
    CanCreateArticle,
    CanCreateNewsletter,
    CanDeleteArticle,
    CanDeleteNewsletter,
    CanUpdateArticle,
    CanUpdateNewsletter,
    HasArticleViewPermission,
    HasNewsletterViewPermission,
)
from .api_serializers import ArticleSerializer, NewsletterSerializer
from .models import Article, Newsletter, Subscription
from .editorial_workflows import publish_article
from .views.helpers import _filter_articles_for_editor


# =============================================================================
# Helper functions:
# =============================================================================

# =============================================================================
# DERIVED REQUIREMENT - Centralise queryset shaping and tiny role helpers so
# all REST endpoints apply consistent eager loading and permission decisions.
# =============================================================================
def _article_queryset():
    """Return the base queryset used by article API views."""
    # Build the base QuerySet for article API endpoints.
    # The idea is: Start with Article, and include those related foreign-key 
    #              objects efficiently in the same query.
    return Article.objects.select_related(
        # Tell Django to join these related tables up front for efficiency.
        "author",
        "publisher",
        "category",
        "approved_by",
    )


def _article_serializer_context(request):
    """Return context consumed by ``ArticleSerializer`` during output shaping.

    The returned dictionary is passed to ``ArticleSerializer(..., context=...)``
    so the serializer can access request-specific data through
    ``self.context`` in ``api_serializers.py``. In particular,
    ``ArticleSerializer.to_representation()`` uses this context to decide
    whether the current reader should receive the full article body or only
    the teaser/preview version of the content.

    For reader accounts, this helper precomputes the relevant subscription
    access sets once in the view layer and attaches them to the serializer
    context, instead of forcing the serializer to rebuild that information for
    each serialized article.
    """
    context = {"request": request}
    if _is_reader(request.user):
        context["reader_access"] = {
            # Is reader subscribed to "all articles plan"?
            "all_articles_plan": bool(request.user.all_articles_plan),
            # Set of journalists the reader is subscribed to
            "journalist_ids": set(
                Subscription.objects.filter(  # Build a queryset with WHERE
                    reader=request.user,
                    journalist__isnull=False,
                ).values_list(  # Return only journalist FK values.
                    "journalist_id",
                    flat=True  # Flat list of IDs, not 1-tuples.
                )
            ),
            # Set of publishers the reader is subscribed to
            "publisher_ids": set(
                Subscription.objects.filter(  # Build a queryset with WHERE
                    reader=request.user,
                    publisher__isnull=False,
                ).values_list(  # Return only publisher FK values.
                    "publisher_id",
                    flat=True  # Flat list of IDs, not 1-tuples.
                )
            ),
        }
    return context


def _newsletter_queryset():
    """Return the base queryset used by newsletter API views."""
    # Build the base QuerySet for newsletter API endpoints.
    return Newsletter.objects.select_related(
        "author", "category",
    ).prefetch_related(
        # Tell Django to join the author/category tables and prefetch the
        # many-to-many articles relation up front for efficiency.
        "articles"
    )


def _newsletter_serializer_context(request):
    """Return serializer context for newsletter API responses."""
    return {"request": request}


def _is_editor(user):
    return user.is_authenticated and user.role == "editor"


def _is_journalist(user):
    return user.is_authenticated and user.role == "journalist"


def _is_reader(user):
    return user.is_authenticated and user.role == "reader"


# =============================================================================
# CORE REQUIREMENT - Token-authenticated REST API endpoints for article list,
# subscribed feed, detail, create/update/delete, and approval as required by
# the capstone brief.
# =============================================================================
class ApiRootView(APIView):
    """Return a JSON index of the mounted Daily Indaba API endpoints.

    The project uses DRF's JSON renderer only, so this root endpoint acts as a
    lightweight landing page for `/api/`. It gives reviewers and developers a
    stable entry point that advertises the token, article, newsletter, and
    mock-announcement routes without requiring them to inspect the URLconf.
    """

    # Public index endpoint: skip token auth entirely:
    authentication_classes = []
    # Public discovery route: allow unauthenticated access:
    permission_classes = [permissions.AllowAny]
    def get(self, request):
        # Group related URLs so the route surface is easier to scan in a
        # browser or API client than a flat list of strings.
        return Response(
            {
                "project": "The Daily Indaba API",
                "authentication": {
                    "scheme": "Token",
                    "login": reverse(
                        "daily_indaba_api:token",
                        request=request,
                    ),
                    "logout": reverse(
                        "daily_indaba_api:token-logout",
                        request=request,
                    ),
                },
                "endpoints": {
                    "articles": reverse(
                        "daily_indaba_api:article-list",
                        request=request,
                    ),
                    "subscribed_articles": reverse(
                        "daily_indaba_api:article-subscribed",
                        request=request,
                    ),
                    "newsletters": reverse(
                        "daily_indaba_api:newsletter-list",
                        request=request,
                    ),
                    "mock_announcements": reverse(
                        "daily_indaba_api:mock-announcement",
                        request=request,
                    ),
                },
            }
        )


class TokenLoginView(ObtainAuthToken):
    """Issue or retrieve a DRF token for a valid username/password pair."""

    permission_classes = [permissions.AllowAny]  # Login must stay public so users can obtain their first token.
    parser_classes = [JSONParser]  # enforces JSON-only input

    def post(self, request, *args, **kwargs):
        # Validate the submitted credentials against DRF's auth serializer.
        serializer = self.serializer_class(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        # Pull out the authenticated user returned by the serializer.
        user = serializer.validated_data["user"]
        # get_or_create() fetches the existing token or creates a new one.
        # The _ discards the boolean 'created' flag; only the token is needed.
        # Cf.: https://docs.djangoproject.com/en/5.2/ref/models/
        #      querysets/#get-or-create
        token, _ = Token.objects.get_or_create(user=user)
        # Return the token plus a little user metadata for the client app.
        return Response(
            {
                "token": token.key,
                "user_id": user.pk,
                "username": user.username,
                "role": user.role,
            }
        )


class TokenLogoutView(APIView):
    """Delete the authenticated user's DRF token to fully invalidate it.

    With DRF's TokenAuthentication, tokens remain valid until explicitly
    deleted. The M06T07 submission's logout_user view called only Django's
    logout(request), which ends the browser session but leaves the API token
    live and reusable. As per feedback to HyperionDev Task 14 (Django -
    eCommerce Application Part 2), the token must be deleted via
    auth_token.delete() to fully log out of the API.

    Cf.: https://www.django-rest-framework.org/api-guide/authentication/
         #tokenauthentication
         https://dev.to/ebereplenty/how-to-log-users-out-in-django-rest-
         framework-drf-25ih
    """

    def post(self, request):
        # TokenLoginView uses get_or_create(), so a token is guaranteed to
        # exist for any request that reached this handler via
        # TokenAuthentication.
        request.user.auth_token.delete()
        # HTTP 204 No Content is the standard response for a successful
        # action that returns no body.
        # Cf.: https://www.django-rest-framework.org/api-guide/status-codes/
        return Response(status=status.HTTP_204_NO_CONTENT)


class ArticleListCreateAPIView(APIView):
    """List approved articles or create a new article as a journalist."""
    # Cf. https://www.django-rest-framework.org/topics/html-and-forms/#rendering-html
    # As per feedback to HyperionDev Task 14 (Django - eCommerce Application
    # Part 2)

    def get_permissions(self):
        # One endpoint serves two workflows: GET uses read access, while POST
        # switches to the stricter journalist-only article-creation rule.
        if self.request.method == "POST":
            return [
                permissions.IsAuthenticated(),
                CanCreateArticle(),
            ]
        return [
            permissions.IsAuthenticated(),
            HasArticleViewPermission(),
        ]

    def get(self, request):
        # Start from the shared article queryset used across article endpoints.
        articles = _article_queryset()
        if _is_editor(request.user):
            # Editors may see both approved and pending articles in their own
            # publisher scope only.
            articles = _filter_articles_for_editor(
                articles,
                request.user,
            ).order_by(
                # Add ORDER BY approved ASC, importance ASC, publication_date
                # DESC, created_at DESC.
                "approved",
                "importance",
                "-publication_date",
                "-created_at",
            )
        else:
            # Everyone else only sees approved articles in the public listing.
            articles = articles.filter(approved=True).order_by(
                # Add WHERE approved = true, then ORDER BY importance ASC,
                # publication_date DESC, created_at DESC.
                "importance",
                "-publication_date",
                "-created_at",
            )
        # Serialize the queryset to JSON-ready Python data.
        serializer = ArticleSerializer(
            articles,
            many=True,
            context=_article_serializer_context(request),
        )
        return Response(serializer.data)

    def post(self, request):
        # Bind the incoming JSON payload to the article serializer.
        serializer = ArticleSerializer(
            data=request.data,
            context={"request": request},
        )
        # raise_exception=True returns HTTP 400 with validation errors as
        # JSON when data is invalid, eliminating a manual check.
        # Cf.: https://www.django-rest-framework.org/api-guide/serializers/
        #      #raising-an-exception-on-invalid-data
        serializer.is_valid(raise_exception=True)
        # serializer.save(author=...) injects the authenticated user as the
        # article author without exposing 'author' as a writable API field.
        # Cf.: https://www.django-rest-framework.org/api-guide/serializers/
        #      #passing-additional-attributes-to-save
        article = serializer.save(author=request.user)
        # Return the created article representation with HTTP 201 Created.
        return Response(
            ArticleSerializer(
                article,
                context=_article_serializer_context(request),
            ).data,
            status=status.HTTP_201_CREATED,
        )


class SubscribedArticleListAPIView(APIView):
    """Return approved articles from the reader's subscribed sources only."""

    def get_permissions(self):
        # This endpoint is narrower than the general article list, so it uses
        # its own reader-only permission class.
        return [
            permissions.IsAuthenticated(),
            CanAccessSubscribedArticles(),
        ]

    def get(self, request):
        # .values_list(flat=True) returns flat integer IDs — more efficient
        # than loading full objects when only the FK is needed for the
        # subsequent Q(...) | Q(...) filter.
        # Cf.: https://docs.djangoproject.com/en/5.2/ref/models/
        #      querysets/#values-list
        # Collect publisher IDs the reader subscribes to:
        publisher_ids = Subscription.objects.filter(
            reader=request.user,
            publisher__isnull=False,
        ).values_list("publisher_id", flat=True)
        # Collect journalist IDs the reader subscribes to:
        journalist_ids = Subscription.objects.filter(
            reader=request.user,
            journalist__isnull=False,
        ).values_list("journalist_id", flat=True)

        # Return only approved articles from either subscribed source.
        articles = (
            _article_queryset()
            # Add WHERE approved = true.
            .filter(approved=True)
            # OR-combined Q-object pattern; see Guest et al., Web Development
            # with Django 6, pp. 186-188.
            .filter(
                Q(author_id__in=journalist_ids)
                | Q(publisher_id__in=publisher_ids)
            )
            # Remove duplicates when an article matches both sets.
            .distinct()
            # ORDER BY importance ASC, publication_date DESC, created_at DESC.
            .order_by("importance", "-publication_date", "-created_at")
        )
        # Serialize the matched article feed for the API response.
        serializer = ArticleSerializer(
            articles,
            many=True,
            context=_article_serializer_context(request),
        )
        return Response(serializer.data)


class ArticleDetailAPIView(APIView):
    """Retrieve, update, or delete a single article."""

    def get_permissions(self):
        # Method-specific routing keeps read, write, and delete rules
        # explicit without scattering manual checks through each handler.
        if self.request.method in {"PUT", "PATCH"}:
            return [
                permissions.IsAuthenticated(),
                CanUpdateArticle(),
            ]
        if self.request.method == "DELETE":
            return [
                permissions.IsAuthenticated(),
                CanDeleteArticle(),
            ]
        return [
            permissions.IsAuthenticated(),
            HasArticleViewPermission(),
        ]

    def get_object_for_read(self, request, pk):
        # Start with the shared article queryset used by this endpoint.
        queryset = _article_queryset()
        if _is_editor(request.user):
            # Editors may read approved and pending articles only inside the
            # publishers they curate, plus independent articles when their
            # publisher affiliation allows it.
            return get_object_or_404(
                _filter_articles_for_editor(queryset, request.user),
                pk=pk,
            )
        if _is_journalist(request.user):
            # Guest et al., Web Development with Django 6, pp. 186-188, use
            # Q objects to express OR logic; here the journalist may read an
            # article when it is approved OR when they authored it.
            return get_object_or_404(
                # Add WHERE approved = true OR author_id = request.user.pk.
                queryset.filter(Q(approved=True) | Q(author=request.user)),
                pk=pk,
            )
        # Readers and anonymous clients may only read approved articles.
        # Add WHERE approved = true.
        return get_object_or_404(queryset.filter(approved=True), pk=pk)

    def get_object_for_write(self, pk):
        # Write operations fetch the article without role filtering first.
        return get_object_or_404(_article_queryset(), pk=pk)

    def get(self, request, pk):
        # Load the readable article and return its serialized data.
        article = self.get_object_for_read(request, pk)
        return Response(
            ArticleSerializer(
                article,
                context=_article_serializer_context(request),
            ).data
        )

    def _update_article(self, request, pk, *, partial):
        # Load the target article before applying write-permission checks.
        article = self.get_object_for_write(pk)
        self.check_object_permissions(request, article)

        # partial=True lets PATCH update only the supplied fields, skipping
        # validation for absent ones — unlike PUT which requires all fields.
        # Cf.: https://www.django-rest-framework.org/api-guide/serializers/
        #      #partial-updates
        serializer = ArticleSerializer(
            article,
            data=request.data,
            partial=partial,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        # Preserve the original author; don't let the payload rewrite it.
        serializer.save(author=article.author)
        return Response(serializer.data)

    def put(self, request, pk):
        # PUT expects a full article representation, not a partial update.
        return self._update_article(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update_article(request, pk, partial=True)

    def delete(self, request, pk):
        # Load the target article before applying delete-permission checks.
        article = self.get_object_for_write(pk)
        self.check_object_permissions(request, article)

        # Delete the article row and return the standard empty 204 response.
        article.delete()
        # HTTP 204 No Content is the standard status for DELETE (no body).
        # HTTP 201 is used after POST; HTTP 403 signals permission denied.
        # Cf.: https://www.django-rest-framework.org/api-guide/status-codes/
        return Response(status=status.HTTP_204_NO_CONTENT)


class ArticleApproveAPIView(APIView):
    """Approve an article and trigger downstream notifications."""

    def get_permissions(self):
        # Approval has its own permission because it is editor-only and more
        # restrictive than ordinary article editing.
        return [
            permissions.IsAuthenticated(),
            CanApproveArticle(),
        ]

    def post(self, request, pk):
        # Load the target article and reject duplicate approvals.
        article = get_object_or_404(_article_queryset(), pk=pk)
        self.check_object_permissions(request, article)
        if article.approved:
            return Response(
                {"detail": "This article has already been approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Run the shared publish workflow to keep API and web UI consistent.
        publish_article(article, editor=request.user)
        return Response(
            ArticleSerializer(
                article,
                context=_article_serializer_context(request),
            ).data
        )


class NewsletterListCreateAPIView(APIView):
    """List newsletters or create a new newsletter."""

    def get_permissions(self):
        # DRF asks for a fresh permission set per request, so this method can
        # switch cleanly between read and write rules on the same endpoint.
        if self.request.method == "POST":
            return [
                permissions.IsAuthenticated(),
                CanCreateNewsletter(),
            ]
        return [
            permissions.IsAuthenticated(),
            HasNewsletterViewPermission(),
        ]

    def get(self, request):
        # Return the newest newsletters first so the API mirrors the public
        # reading order used elsewhere in the project.
        newsletters = _newsletter_queryset().order_by(
            # Add ORDER BY created_at DESC.
            "-created_at"
        )
        serializer = NewsletterSerializer(
            newsletters,
            many=True,
            context=_newsletter_serializer_context(request),
        )
        return Response(serializer.data)

    def post(self, request):
        # Bind the incoming payload, validate it, then stamp the authenticated
        # user in as the newsletter author server-side.
        serializer = NewsletterSerializer(
            data=request.data,
            context=_newsletter_serializer_context(request),
        )
        serializer.is_valid(raise_exception=True)
        newsletter = serializer.save(author=request.user)
        return Response(
            NewsletterSerializer(
                newsletter,
                context=_newsletter_serializer_context(request),
            ).data,
            status=status.HTTP_201_CREATED,
        )


class NewsletterDetailAPIView(APIView):
    """Retrieve, update, or delete a single newsletter."""

    def get_permissions(self):
        # Like the article detail endpoint, this one swaps permission classes
        # per HTTP method so reads, edits, and deletes stay explicit.
        if self.request.method in {"PUT", "PATCH"}:
            return [
                permissions.IsAuthenticated(),
                CanUpdateNewsletter(),
            ]
        if self.request.method == "DELETE":
            return [
                permissions.IsAuthenticated(),
                CanDeleteNewsletter(),
            ]
        return [
            permissions.IsAuthenticated(),
            HasNewsletterViewPermission(),
        ]

    def get_object(self, pk):
        # Centralise the lookup so read/update/delete all hit the same eager-
        # loaded queryset and the same 404 behaviour.
        return get_object_or_404(_newsletter_queryset(), pk=pk)

    def get(self, request, pk):
        newsletter = self.get_object(pk)
        return Response(
            NewsletterSerializer(
                newsletter,
                context=_newsletter_serializer_context(request),
            ).data
        )

    def _update_newsletter(self, request, pk, *, partial):
        # Resolve the target object before applying object-level permission
        # checks so the permission class can evaluate ownership and role scope.
        newsletter = self.get_object(pk)
        self.check_object_permissions(request, newsletter)
        serializer = NewsletterSerializer(
            newsletter,
            data=request.data,
            partial=partial,
            context=_newsletter_serializer_context(request),
        )
        serializer.is_valid(raise_exception=True)
        # Preserve the original author rather than allowing a client payload to
        # reassign ownership of the newsletter.
        serializer.save(author=newsletter.author)
        return Response(serializer.data)

    def put(self, request, pk):
        return self._update_newsletter(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update_newsletter(request, pk, partial=True)

    def delete(self, request, pk):
        newsletter = self.get_object(pk)
        self.check_object_permissions(request, newsletter)
        newsletter.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MockAnnouncementAPIView(APIView):
    """Receive mock announcement POSTs for local development and testing.

    In a real deployment, article-approval announcements would be POSTed to an
    external service (social media API, Slack webhook, etc.).  This endpoint
    stands in for that service locally: it validates the payload and echoes it
    back so the full approval side-effect can be exercised without any external
    dependency.

    No authentication is required — the endpoint is intentionally open so
    announcement_client.py can POST without credentials.

    How to test with Postman
    ------------------------
    1. Start the dev server:
           python manage.py runserver

    2. In Postman create a new request:
           Method : POST
           URL    : http://localhost:8000/api/mock-announcements/

    3. Under the Headers tab add:
           Content-Type : application/json

    4. Under the Body tab select "raw" and choose "JSON", then paste:
           {
               "announcement_type": "article_approval",
               "article_id": 1,
               "title": "Test Article",
               "article_url": "http://localhost:8000/news/articles/1/",
               "author": "Test Author",
               "kind": "link"
           }

       Note: "article_url" is treated as an opaque string by this endpoint;
       it does not need to resolve to a real article for the test to pass.

    5. Click Send.

    Expected responses
    ------------------
    HTTP 201 Created — all required fields present:
        {
            "ok": true,
            "mode": "mock",
            "message": "Mock announcement received. No external social
                        network was contacted.",
            "received": { ...the submitted payload... }
        }

    HTTP 400 Bad Request — one or more fields missing:
        {
            "ok": false,
            "error": "Missing required field(s): <field>, ..."
        }
    """

    # Mock webhook must be callable without DRF token credentials:
    authentication_classes = []
    # Dev/test helper endpoint: accept unauthenticated POSTs:
    permission_classes = [permissions.AllowAny]
    parser_classes = [JSONParser]  # Enforce JSON-only input

    def post(self, request):  # Override APIView method
        required_fields = [
            "announcement_type",
            "article_id",
            "title",
            "article_url",
            "author",
            "kind",
        ]
        missing = [
            field for field in required_fields
            if not request.data.get(field)
        ]
        if missing:
            return Response(
                {
                    "ok": False,
                    "error": (
                        "Missing required field(s): "
                        + ", ".join(missing)
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "ok": True,
                "mode": "mock",
                "message": (
                    "Mock announcement received. "
                    "No external social network was contacted."
                ),
                "received": request.data,
            },
            status=status.HTTP_201_CREATED,
        )
