"""
DRF (Django REST Framework) serializers for the Daily Indaba API.

Serializers convert between Python model instances and JSON for the REST API
and validate incoming data before it reaches the model layer.

Two base classes are used:
  serializers.Serializer      — manual field declaration; no model link.
  serializers.ModelSerializer — auto-generates fields from the model Meta,
                                similar to a Django ModelForm.
Cf.: https://www.django-rest-framework.org/api-guide/serializers/

Serializers defined in this module:
UserSerializer              — read-only public subset of a user (id, username,
                            role, public_name); used as a nested field in
                            Article and Newsletter serializers.
PublisherSerializer         — publisher metadata (name, description, fee);
                            used as a nested read field in ArticleSerializer.
NewsletterCategorySerializer— editorial category (name, slug, description);
                            nested read field in Article and Newsletter.
ArticleSerializer           — full article CRUD; nests the three serializers
                            above for reads, accepts FK integers on writes,
                            enforces paywall redaction and journalist/
                            publisher affiliation rules.
NewsletterSerializer        — newsletter CRUD; nests UserSerializer and
                            NewsletterCategorySerializer for reads, returns
                            article PKs (not full objects) to keep payloads
                            compact, and restricts linked articles to
                            approved ones only.
"""

from rest_framework import serializers

from .models import Article, Newsletter, NewsletterCategory, Publisher
from .views.helpers import _first_sentence, _user_has_full_access


# =============================================================================
# CORE REQUIREMENT - Serializers for User, Publisher, Article, and Newsletter
# so the required REST API can translate model data to/from JSON.
# =============================================================================
class UserSerializer(serializers.Serializer):
    """Compact public representation of a platform user."""

    # A plain Serializer is enough here because the API only exposes a tiny,
    # hand-picked public subset of user fields rather than the full model.
    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    public_name = serializers.CharField(read_only=True)


class PublisherSerializer(serializers.ModelSerializer):
    """Serializer for publisher metadata."""

    class Meta:
        model = Publisher
        fields = [
            "id",
            "name",
            "description",
            "created_at",
            "monthly_fee",
        ]
        read_only_fields = ["created_at"]


class NewsletterCategorySerializer(serializers.ModelSerializer):
    """Serializer for shared editorial categories."""

    class Meta:
        model = NewsletterCategory
        fields = ["id", "name", "slug", "description"]


class ArticleSerializer(serializers.ModelSerializer):
    """Serializer for article list, detail, create, and update flows."""

    author = UserSerializer(read_only=True)
    publisher = PublisherSerializer(read_only=True)
    category = NewsletterCategorySerializer(read_only=True)
    approved_by = UserSerializer(read_only=True)
    # Developer's note: two separate fields handle the publisher relationship:
    #   publisher    (read_only above) — nested serializer;
    #                                    returns full object on GET.
    #   publisher_id (write_only below) — PrimaryKeyRelatedField;
    #                                     accepts an integer
    #                PK on POST/PUT/PATCH so clients don't need to send a
    #                   full object.
    # source="publisher" maps the write field back to model's 'publisher' FK.
    # Cf.: https://www.django-rest-framework.org/api-guide/fields/#primarykeyrelatedfield
    publisher_id = serializers.PrimaryKeyRelatedField(
        source="publisher",
        # Allow only Publisher rows that exist in the database.
        queryset=Publisher.objects.all(),
        write_only=True,  # Hides publisher_id from GET responses.
        required=False,
        allow_null=True,
    )
    category_id = serializers.PrimaryKeyRelatedField(
        source="category",
        # Allow only NewsletterCategory rows that exist in the database.
        queryset=NewsletterCategory.objects.all(),
        write_only=True,  # Hides category_id from GET responses.
    )

    class Meta:
        model = Article
        fields = [
            "id",
            "title",
            "content",
            "author",
            "publisher",
            "publisher_id",
            "category",
            "category_id",
            "created_at",
            "publication_date",
            "approved",
            "approved_by",
            "importance",
            "image",
            "disclaimer",
        ]
        read_only_fields = [
            # The client must not be able to forge authorship timestamps or
            # self-approve content through the API payload.
            "author",
            "created_at",
            "publication_date",
            "approved",
        ]

    def _reader_has_full_access(self, instance):
        """Return True when the request-context reader may read the full body.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        # If there is no current user here, keep the full article text.
        if user is None:
            return True

        # ---------------------------------------------------
        # Access to full body text on account of user role:
        # ---------------------------------------------------
        # Journalists, editors, and publishers can always read the full
        # article.
        if user.role in {"journalist", "editor", "publisher"}:
            return True

        # ---------------------------------------------------
        # Reader access to full body text based on subscription plans:
        # ---------------------------------------------------
        reader_access = self.context.get("reader_access")
        # If the view did not pass in the reader's access info, use the usual
        # full-article check.
        if reader_access is None:
            return _user_has_full_access(user, instance)
        # Readers on the all-articles plan can read every article.
        if reader_access["all_articles_plan"]:
            return True
        # Readers subscribed to this writer can read the full article.
        if instance.author_id in reader_access["journalist_ids"]:
            return True
        # Readers subscribed to this publisher can also read the full article.
        if instance.publisher_id in reader_access["publisher_ids"]:
            return True
        # Everyone else only gets the teaser.
        return False

    def to_representation(self, instance):
        """Customize DRF's inherited serializer output for article access.

        ``ModelSerializer`` inherits ``to_representation()`` from DRF's base
        ``Serializer`` and would normally serialize the article fields as-is.
        In this project, the serializer also receives ``context`` from the
        view, and that context lets it distinguish between users who may see
        the full article content and users who should receive only teaser
        content.
        """
        data = super().to_representation(instance)
        teaser = _first_sentence(instance.content)
        full_access = self._reader_has_full_access(instance)
        data["teaser"] = teaser
        data["full_access"] = full_access
        if not full_access:
            data["content"] = teaser
        return data

    def validate(self, attrs):
        """Add project-specific object-level validation before saving.

        ``ModelSerializer`` inherits ``validate()`` from DRF's base
        ``Serializer``, where the default implementation simply returns
        ``attrs`` unchanged. That inherited behavior is too generic for this
        project because article submissions must enforce cross-field business
        rules that depend on the current user and the combination of submitted
        values.

        Overriding the inherited hook makes the serializer reject invalid
        newsroom states before the model is saved, specifically:
        journalist users may publish only under affiliated publishers, and
        Front Page articles must include an image. Those checks do not belong
        to any single serializer field in isolation, so they need the
        object-level ``validate()`` override rather than DRF's default
        pass-through behavior.
        """
        # self.context["request"] accesses the HTTP request passed when the
        # serializer was instantiated via context={"request": request}.
        # This is the standard DRF pattern for accessing request.user inside
        # a serializer's validate() method.
        # Cf.: https://www.django-rest-framework.org/api-guide/serializers/#passing-additional-context-to-the-serializer
        request = self.context["request"]
        user = request.user
        publisher = attrs.get("publisher")
        importance = attrs.get(
            "importance",
            getattr(self.instance, "importance", Article.STANDARD),
        )
        image = attrs.get("image", getattr(self.instance, "image", None))

        # -------------------------------------------------------------
        # Validation: Front Page articles must have an image
        # -------------------------------------------------------------
        if importance == Article.FRONT_PAGE and not image:
            raise serializers.ValidationError(
                {
                    "image": (
                        "Front Page articles require a rubric image."
                    )
                }
            )

        # -------------------------------------------------------------
        # Validation: Journalist can only publish under a publisher they
        #             are affiliated with.
        # -------------------------------------------------------------
        if publisher and user.role == "journalist":
            # Build a queryset with WHERE pk = publisher.pk on the journalist's
            # affiliated-publishers relation, then determine whether that
            # affiliation exists.
            if not user.journalist_publishers.filter(pk=publisher.pk).exists():
                raise serializers.ValidationError(
                    {
                        "publisher_id": (
                            "You can only publish under an affiliated "
                            "publisher."
                        )
                    }
                )
        return attrs


class NewsletterSerializer(serializers.ModelSerializer):
    """Serializer for newsletter list, detail, create, and update flows."""

    author = UserSerializer(read_only=True)
    category = NewsletterCategorySerializer(read_only=True)
    # Mirror the Article serializer pattern: return a nested category object on
    # GET, but accept a plain category primary key on POST/PUT/PATCH.
    category_id = serializers.PrimaryKeyRelatedField(
        source="category",
        # Allow only NewsletterCategory rows that exist in the database.
        queryset=NewsletterCategory.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    # Read responses keep newsletter payloads compact by returning only article
    # primary keys instead of nesting full article objects here.
    articles = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    # many=True on PrimaryKeyRelatedField handles a ManyToManyField — it
    # accepts a list of PKs on write and returns a list on read.
    # queryset= restricts valid values to approved articles (server-side
    # validation).
    article_ids = serializers.PrimaryKeyRelatedField(
        source="articles",
        many=True,
        # Allow only approved Article rows to be attached to a newsletter.
        queryset=Article.objects.filter(approved=True),
        write_only=True,
        required=False,
    )

    class Meta:
        model = Newsletter
        fields = [
            "id",
            "title",
            "description",
            "created_at",
            "author",
            "category",
            "category_id",
            "articles",
            "article_ids",
        ]
        read_only_fields = ["created_at", "author"]
