"""Shared fixtures for the retained Daily Indaba tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from daily_indaba.models import (
    Article,
    Newsletter,
    NewsletterCategory,
    Publisher,
    Subscription,
)


class CoreNewsApiTestCase(APITestCase):
    """Shared API fixtures for retained article and newsletter tests."""

    def setUp(self):
        # Cf. Mele (2025:373). get_user_model() returns the custom user model
        # used here to distinguish between reader, journalist, and editor
        # accounts.
        self.User = get_user_model()

        # Create a reader account to exercise token-authenticated reader
        # endpoints and subscription-sensitive article access rules.
        self.reader = self.User.objects.create_user(
            username="reader_api",
            password="reader-pass-123",
            email="reader-api@example.com",
            role="reader",
        )
        # Create the primary journalist whose articles and newsletter are used
        # throughout the API tests.
        self.journalist = self.User.objects.create_user(
            username="journalist_api",
            password="journalist-pass-123",
            email="journalist-api@example.com",
            role="journalist",
        )
        # Create a second journalist so tests can distinguish subscribed
        # content from approved content written by someone else.
        self.other_journalist = self.User.objects.create_user(
            username="other_journalist_api",
            password="other-journalist-pass-123",
            email="other-journalist-api@example.com",
            role="journalist",
        )
        # Create an editor account for approval-only endpoints and to act as
        # the approving editor on already-published fixtures.
        self.editor = self.User.objects.create_user(
            username="editor_api",
            password="editor-pass-123",
            email="editor-api@example.com",
            role="editor",
            first_name="Chief",
            last_name="Editor",
        )

        # Create a publisher so the tests can cover publisher-scoped
        # subscriptions plus editor/journalist affiliation rules.
        self.publisher = Publisher.objects.create(
            name="The Gazette",
            description="General reporting",
        )
        # Attach the main journalist and editor to the publisher to mirror the
        # real newsroom relationships enforced in the app.
        self.publisher.journalists.add(self.journalist)
        self.publisher.editors.add(self.editor)

        # Create a shared category so every article and newsletter fixture can
        # satisfy the current model's category relationship.
        self.category = NewsletterCategory.objects.create(
            name="Politics Test",
            slug="politics-test",
        )

        # Create an approved article from the subscribed journalist so reader
        # API tests have one article the reader should be allowed to access.
        self.approved_article = Article.objects.create(
            title="Approved subscribed story",
            content=(
                "Subscriber lead sentence. "
                "Full approved article content continues in a second sentence."
            ),
            author=self.journalist,
            publisher=self.publisher,
            category=self.category,
            approved=True,
            approved_by=self.editor,
            importance=Article.TOP_STORY,
        )
        # Create another approved article from an unsubscribed journalist so
        # tests can prove access is not granted just because content is public.
        self.other_article = Article.objects.create(
            title="Approved non-subscribed story",
            content=(
                "Unsubscribed lead sentence. "
                "Another full approved article content continues here."
            ),
            author=self.other_journalist,
            category=self.category,
            approved=True,
            importance=Article.STANDARD,
        )
        # Create a pending article by the primary journalist so editor-only
        # approval flows and pending visibility can be tested.
        self.pending_article = Article.objects.create(
            title="Pending editor review",
            content="This article is awaiting approval.",
            author=self.journalist,
            publisher=self.publisher,
            category=self.category,
            approved=False,
            importance=Article.STANDARD,
        )
        # Create a journalist-authored newsletter to exercise author-scoped
        # newsletter API behavior.
        self.journalist_newsletter = Newsletter.objects.create(
            title="Journalist morning briefing",
            description="A journalist-authored newsletter for API tests.",
            author=self.journalist,
            category=self.category,
        )
        # Include the approved article so serializer and endpoint responses have
        # at least one published article to return inside the newsletter.
        self.journalist_newsletter.articles.add(self.approved_article)
        # Create an editor-authored newsletter so tests can compare editor
        # capabilities against journalist capabilities.
        self.editor_newsletter = Newsletter.objects.create(
            title="Editor oversight briefing",
            description="An editor-authored newsletter for API tests.",
            author=self.editor,
            category=self.category,
        )
        # Reuse the same approved article so the tests vary the authoring role
        # without needing a second newsletter article fixture.
        self.editor_newsletter.articles.add(self.approved_article)

        # Subscribe the reader directly to the primary journalist so the
        # subscribed-articles feed has an author-level subscription to match.
        Subscription.objects.create(
            reader=self.reader,
            journalist=self.journalist,
        )
        # Subscribe the reader to the publisher as well so tests can cover both
        # supported subscription target types.
        Subscription.objects.create(
            reader=self.reader,
            publisher=self.publisher,
        )

        # Create DRF auth tokens for the three API-facing roles used across the
        # retained article and newsletter endpoint tests.
        self.reader_token = Token.objects.create(user=self.reader)
        self.journalist_token = Token.objects.create(user=self.journalist)
        self.editor_token = Token.objects.create(user=self.editor)

        # Cache the main endpoint URLs centrally so individual tests do not
        # duplicate hard-coded API route strings.
        self.api_root_url = "/api/"
        self.article_list_url = "/api/articles/"
        self.article_subscribed_url = "/api/articles/subscribed/"
        self.newsletter_list_url = "/api/newsletters/"

    def authenticate(self, token):
        """Attach a DRF token to the API client."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")


class CoreNewsletterSerializerTestCase(TestCase):
    """Shared fixtures for retained newsletter-serializer tests."""

    def setUp(self):
        # Load the project's custom user model so these serializer fixtures use
        # the same role-aware accounts as the production code.
        self.User = get_user_model()

        # Create an editor author so serializer tests can check editor-created
        # newsletter data as a valid authoring case.
        self.editor = self.User.objects.create_user(
            username="newsletter_api_editor",
            password="newsletter-api-editor-pass-123",
            role="editor",
            first_name="API",
            last_name="Editor",
        )
        # Create a journalist author for the article fixtures that will be
        # attached to serializer input and output cases.
        self.journalist = self.User.objects.create_user(
            username="newsletter_api_journalist",
            password="newsletter-api-journalist-pass-123",
            role="journalist",
        )
        # Create a category label so serializer test articles can satisfy the
        # current article schema and category relationships.
        self.category = NewsletterCategory.objects.create(
            name="API Newsletter Category",
            slug="api-newsletter-category",
        )
        # Create an approved article that should be accepted by newsletter
        # serialization logic when attached to a newsletter payload.
        self.approved_article = Article.objects.create(
            title="Approved newsletter article",
            content="Approved article body for the newsletter serializer.",
            author=self.journalist,
            category=self.category,
            approved=True,
        )
        # Create a pending article so serializer tests can verify unpublished
        # articles are rejected or filtered out where appropriate.
        self.pending_article = Article.objects.create(
            title="Pending newsletter article",
            content="Pending article body that should not be accepted.",
            author=self.journalist,
            category=self.category,
            approved=False,
        )


class CoreNewsWebTestCase(TestCase):
    """Shared fixtures for retained web workflow tests."""

    def setUp(self):
        # Cf. Mele (2025:373). get_user_model() returns the custom user model
        # used here to distinguish between reader, journalist, and editor
        # accounts.
        self.User = get_user_model()

        # Create a reader account for comment-posting and subscriber-only web
        # page behavior tests.
        self.reader = self.User.objects.create_user(
            username="comment_reader",
            password="comment-pass-123",
            role="reader",
        )
        # Create a journalist whose article is used across the retained web
        # workflow tests.
        self.journalist = self.User.objects.create_user(
            username="newsletter_writer",
            password="newsletter-writer-pass-123",
            role="journalist",
        )
        # Create an editor account so web tests can cover editorial access and
        # approval actions in the browser-driven workflows.
        self.editor = self.User.objects.create_user(
            username="newsletter_editor",
            password="newsletter-editor-pass-123",
            role="editor",
            first_name="Newsletter",
            last_name="Editor",
        )
        # Create the independent-curation desk used to let the editor manage
        # content from journalists who are not attached to another publisher.
        self.independent_desk = Publisher.objects.create(
            name="The Daily Indaba",
            description="Independent-curation desk for web tests.",
            curates_independent_journalists=True,
        )
        # Affiliate the editor with the independent desk so editor-scoped web
        # views can treat this editor as responsible for independent content.
        self.independent_desk.editors.add(self.editor)

        # Create a category so the shared web-test article matches the current
        # Article model requirements.
        self.category = NewsletterCategory.objects.create(
            name="Culture Test",
            slug="culture-test",
        )
        # Create an approved article for the journalist so the retained web
        # tests can exercise article detail and subscriber-only display logic.
        self.article = Article.objects.create(
            title="Subscriber-only article",
            content="The first sentence. The rest is subscriber-only.",
            author=self.journalist,
            category=self.category,
            approved=True,
        )
