"""Core article API tests for The Daily Indaba."""

from unittest.mock import patch

from django.conf import settings

from rest_framework import status

from daily_indaba.models import Article

from .base import CoreNewsApiTestCase


class ArticleApiTests(CoreNewsApiTestCase):
    """Retained core article API and approval tests."""

    def test_unauthenticated_article_list_requires_token(self):
        """Verify protected article endpoints reject requests without a token."""
        # -----------------------------------------------------------------
        # ARRANGE: Leave the API client unauthenticated
        # -----------------------------------------------------------------

        # -----------------------------------------------------------------
        # ACT: Call the protected article-list endpoint without a token
        # -----------------------------------------------------------------
        response = self.client.get(self.article_list_url)

        # -----------------------------------------------------------------
        # ASSERT: Verify DRF rejects the request at the authentication layer
        # -----------------------------------------------------------------
        # Checks: unauthenticated request returns HTTP 401 Unauthorized
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_endpoint_returns_token_for_valid_credentials(self):
        """Verify the API token login endpoint returns a token for valid credentials."""
        # -----------------------------------------------------------------
        # ARRANGE: Build a valid token-login payload for the reader account
        # -----------------------------------------------------------------
        credentials = {
            "username": "reader_api",
            "password": "reader-pass-123",
        }

        # -----------------------------------------------------------------
        # ACT: Submit the token login request
        # -----------------------------------------------------------------
        response = self.client.post("/api/token/", credentials, format="json")

        # -----------------------------------------------------------------
        # ASSERT: Verify the endpoint returns HTTP 200, the reader role, and
        #         a token string
        # -----------------------------------------------------------------
        # Checks: token endpoint returns HTTP 200 for valid credentials
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Checks: response contains the authenticated user's role
        self.assertEqual(response.data["role"], "reader")
        # Checks: response body includes a token field
        self.assertIn("token", response.data)

    def test_journalist_can_create_article_but_reader_cannot(self):
        """Verify article creation is journalist-only at the API layer."""
        # -----------------------------------------------------------------
        # ARRANGE: Build one valid article payload that both roles will
        #          submit
        # -----------------------------------------------------------------
        article_payload = {
            "title": "Fresh journalist article",
            "content": "Created through the API.",
            "importance": Article.STANDARD,
            "publisher_id": self.publisher.pk,
            "category_id": self.category.pk,
        }

        # -----------------------------------------------------------------
        # ACT: First submit as the journalist, then submit the same payload
        #      as the reader
        # -----------------------------------------------------------------
        self.authenticate(self.journalist_token)
        journalist_response = self.client.post(
            self.article_list_url,
            article_payload,
            format="json",
        )

        self.authenticate(self.reader_token)
        reader_response = self.client.post(
            self.article_list_url,
            article_payload,
            format="json",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the journalist gets HTTP 201, the reader gets HTTP
        #         403, and the article row belongs to the journalist
        # -----------------------------------------------------------------
        # Checks: journalist receives HTTP 201 Created on article submission
        self.assertEqual(
            journalist_response.status_code,
            status.HTTP_201_CREATED,
        )
        # Checks: reader receives HTTP 403 Forbidden on article submission
        self.assertEqual(
            reader_response.status_code,
            status.HTTP_403_FORBIDDEN,
        )
        # Checks: response includes the correct category slug
        self.assertEqual(
            journalist_response.data["category"]["slug"],
            self.category.slug,
        )
        # Checks: article row is saved with the journalist as author
        self.assertTrue(
            Article.objects.filter(
                title="Fresh journalist article",
                author=self.journalist,
            ).exists()
        )

    def test_journalist_can_update_own_unapproved_article_via_put(self):
        """Verify a journalist can replace their own unapproved article."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the journalist who authored the pending
        #          article and prepare a full replacement payload
        # -----------------------------------------------------------------
        self.authenticate(self.journalist_token)

        # -----------------------------------------------------------------
        # ACT: Submit the full update request
        # -----------------------------------------------------------------
        response = self.client.put(
            f"/api/articles/{self.pending_article.pk}/",
            {
                "title": "Pending editor review updated",
                "content": "Updated copy for the pending article.",
                "importance": Article.STANDARD,
                "publisher_id": self.publisher.pk,
                "category_id": self.category.pk,
                "disclaimer": "Updated via PUT",
            },
            format="json",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the update succeeds and the stored article fields
        #         are replaced with the submitted values
        # -----------------------------------------------------------------
        # Checks: journalist's PUT on their own article returns HTTP 200
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.pending_article.refresh_from_db()
        # Checks: article title is replaced with the submitted value
        self.assertEqual(
            self.pending_article.title,
            "Pending editor review updated",
        )
        # Checks: article disclaimer is replaced with the submitted value
        self.assertEqual(
            self.pending_article.disclaimer,
            "Updated via PUT",
        )

    def test_journalist_cannot_update_another_journalists_article_via_put(self):
        """Verify a journalist cannot update another author's article."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as one journalist and target an article
        #          owned by a different journalist
        # -----------------------------------------------------------------
        self.authenticate(self.journalist_token)

        # -----------------------------------------------------------------
        # ACT: Attempt the forbidden full update
        # -----------------------------------------------------------------
        response = self.client.put(
            f"/api/articles/{self.other_article.pk}/",
            {
                "title": "Attempted overwrite",
                "content": "This should be rejected.",
                "importance": Article.STANDARD,
                "category_id": self.category.pk,
                "disclaimer": "",
            },
            format="json",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the API returns HTTP 403 and leaves the stored
        #         article unchanged
        # -----------------------------------------------------------------
        # Checks: PUT on another journalist's article returns HTTP 403
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # Checks: error message states only own articles may be updated
        self.assertEqual(
            response.data["detail"],
            "You can only update your own articles.",
        )
        self.other_article.refresh_from_db()
        # Checks: the target article title is unchanged after the rejection
        self.assertEqual(
            self.other_article.title,
            "Approved non-subscribed story",
        )

    def test_reader_cannot_update_article_via_put(self):
        """Verify readers are blocked from article updates."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the reader role, which has view-only
        #          article access in the API
        # -----------------------------------------------------------------
        self.authenticate(self.reader_token)

        # -----------------------------------------------------------------
        # ACT: Attempt the forbidden full update
        # -----------------------------------------------------------------
        response = self.client.put(
            f"/api/articles/{self.approved_article.pk}/",
            {
                "title": "Reader overwrite attempt",
                "content": "Readers may not update articles.",
                "importance": Article.STANDARD,
                "category_id": self.category.pk,
                "disclaimer": "",
            },
            format="json",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the API returns the explicit role-based rejection
        # -----------------------------------------------------------------
        # Checks: reader's PUT on an article returns HTTP 403 Forbidden
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # Checks: error message states readers cannot update articles
        self.assertEqual(
            response.data["detail"],
            "Readers cannot update articles.",
        )

    def test_editor_can_patch_article_within_scope(self):
        """Verify an editor can partially update an article they curate."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the editor assigned to the article's
        #          publisher and prepare a minimal partial update payload
        # -----------------------------------------------------------------
        self.authenticate(self.editor_token)

        # -----------------------------------------------------------------
        # ACT: Patch the pending article
        # -----------------------------------------------------------------
        response = self.client.patch(
            f"/api/articles/{self.pending_article.pk}/",
            {
                "title": "Editor-retitled pending piece",
                "importance": Article.STANDARD,
            },
            format="json",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the editor can patch the article inside their
        #         curation scope
        # -----------------------------------------------------------------
        # Checks: editor's PATCH on an in-scope article returns HTTP 200
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.pending_article.refresh_from_db()
        # Checks: article title is updated to the editor's submitted value
        self.assertEqual(
            self.pending_article.title,
            "Editor-retitled pending piece",
        )

    def test_subscribed_feed_only_returns_followed_approved_articles(self):
        """Verify the subscribed feed excludes approved articles from unfollowed sources."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the subscribed reader created in setUp
        # -----------------------------------------------------------------
        self.authenticate(self.reader_token)

        # -----------------------------------------------------------------
        # ACT: Request the subscribed-articles endpoint
        # -----------------------------------------------------------------
        response = self.client.get(self.article_subscribed_url)

        # -----------------------------------------------------------------
        # ASSERT: Verify only the journalist or publisher followed by this
        #         reader appears in the returned feed
        # -----------------------------------------------------------------
        # Checks: subscribed feed endpoint returns HTTP 200 OK
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Checks: feed contains exactly one article (from followed sources)
        self.assertEqual(len(response.data), 1)
        # Checks: the single result is the subscribed journalist's article
        self.assertEqual(response.data[0]["id"], self.approved_article.pk)

    def test_editor_can_approve_article_and_trigger_signal_notifications_once(self):
        """Verify approval triggers email and mock POST side effects exactly once."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the editor and patch the shared
        #          notification helpers so the test can observe the side
        #          effects
        # -----------------------------------------------------------------
        self.authenticate(self.editor_token)

        # -----------------------------------------------------------------
        # ACT: Approve the pending article, then save it again after approval
        #      to confirm the side effects do not repeat on later edits
        # -----------------------------------------------------------------
        with patch(
            "daily_indaba.views.helpers._notify_subscribers"
        ) as notify_mock, patch(
            "daily_indaba.announcement_client.http_requests.post"
        ) as announcement_post_mock:
            announcement_post_mock.return_value.raise_for_status.return_value = (
                None
            )
            response = self.client.post(
                f"/api/articles/{self.pending_article.pk}/approve/",
                format="json",
            )
            self.pending_article.refresh_from_db()
            self.pending_article.title = "Pending editor review (updated)"
            self.pending_article.save(update_fields=["title"])

        # -----------------------------------------------------------------
        # ASSERT: Verify the article becomes approved, the approving editor
        #         is recorded, and the notification hook only fires for the
        #         real False -> True transition
        # -----------------------------------------------------------------
        self.pending_article.refresh_from_db()
        # Checks: approval endpoint returns HTTP 200 OK
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Checks: article approved flag is True after the approval action
        self.assertTrue(self.pending_article.approved)
        # Checks: approving editor is recorded on the article
        self.assertEqual(self.pending_article.approved_by, self.editor)
        # Checks: subscriber notification hook fires exactly once
        notify_mock.assert_called_once()
        # Checks: notification is called with the correct article instance
        self.assertEqual(
            notify_mock.call_args.args[0].pk,
            self.pending_article.pk,
        )
        # Checks: announcement POST request is made exactly once
        announcement_post_mock.assert_called_once()
        # Checks: announcement POST targets the configured endpoint URL
        self.assertEqual(
            announcement_post_mock.call_args.args[0],
            settings.ANNOUNCEMENT_ENDPOINT,
        )
        # Checks: announcement POST uses the configured timeout value
        self.assertEqual(
            announcement_post_mock.call_args.kwargs["timeout"],
            settings.ANNOUNCEMENT_TIMEOUT,
        )
        # Checks: announcement payload contains the article_approval type
        self.assertEqual(
            announcement_post_mock.call_args.kwargs["json"][
                "announcement_type"
            ],
            "article_approval",
        )
        # Checks: announcement payload contains the correct article ID
        self.assertEqual(
            announcement_post_mock.call_args.kwargs["json"]["article_id"],
            self.pending_article.pk,
        )

    def test_editor_cannot_approve_article_outside_assigned_publishers(self):
        """Verify an editor cannot approve an article that falls outside their publisher scope."""
        # -----------------------------------------------------------------
        # ARRANGE: Create an out-of-scope article and authenticate as the
        #          assigned publisher editor from setUp
        # -----------------------------------------------------------------
        unassigned_article = Article.objects.create(
            title="Independent article outside scope",
            content="Independent review copy.",
            author=self.other_journalist,
            category=self.category,
            approved=False,
            importance=Article.STANDARD,
        )
        self.authenticate(self.editor_token)

        # -----------------------------------------------------------------
        # ACT: Attempt to approve the out-of-scope article
        # -----------------------------------------------------------------
        response = self.client.post(
            f"/api/articles/{unassigned_article.pk}/approve/",
            format="json",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the API blocks the approval and leaves the article
        #         unapproved
        # -----------------------------------------------------------------
        # Checks: approval of an out-of-scope article returns HTTP 403
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # Checks: error states the editor is not assigned to the article
        self.assertEqual(
            response.data["detail"],
            "You are not assigned to curate this article.",
        )
        unassigned_article.refresh_from_db()
        # Checks: out-of-scope article remains unapproved
        self.assertFalse(unassigned_article.approved)

    def test_editor_can_delete_article_via_api(self):
        """Verify an editor can delete an article within their assigned publisher scope."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the editor who curates self.publisher,
        #          which owns self.pending_article
        # -----------------------------------------------------------------
        self.authenticate(self.editor_token)
        target_pk = self.pending_article.pk

        # -----------------------------------------------------------------
        # ACT: Issue the DELETE request against the article-detail endpoint
        # -----------------------------------------------------------------
        response = self.client.delete(f"/api/articles/{target_pk}/")

        # -----------------------------------------------------------------
        # ASSERT: Verify the endpoint returns HTTP 204 No Content and the
        #         row is removed from the database
        # -----------------------------------------------------------------
        # Checks: editor's DELETE returns HTTP 204 No Content
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        # Checks: deleted article row is removed from the database
        self.assertFalse(Article.objects.filter(pk=target_pk).exists())

    def test_reader_cannot_delete_article_via_api(self):
        """Verify readers are blocked from deleting articles via the API."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the reader who has no delete permission
        # -----------------------------------------------------------------
        self.authenticate(self.reader_token)

        # -----------------------------------------------------------------
        # ACT: Attempt to delete a published article the reader can view
        # -----------------------------------------------------------------
        response = self.client.delete(
            f"/api/articles/{self.approved_article.pk}/",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the endpoint returns HTTP 403 and the article row
        #         is still present in the database
        # -----------------------------------------------------------------
        # Checks: reader's DELETE attempt returns HTTP 403 Forbidden
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # Checks: article row remains in the database after the rejection
        self.assertTrue(
            Article.objects.filter(pk=self.approved_article.pk).exists()
        )
