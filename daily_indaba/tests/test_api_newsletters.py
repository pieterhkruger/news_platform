"""Core newsletter API tests for The Daily Indaba."""

from rest_framework import status

from daily_indaba.models import Newsletter

from .base import CoreNewsApiTestCase


class NewsletterApiTests(CoreNewsApiTestCase):
    """Retained core newsletter API permission and CRUD tests."""

    def test_reader_can_list_newsletters_via_api(self):
        """Verify authenticated readers can list newsletters through the API."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the reader who has newsletter view access
        # -----------------------------------------------------------------
        self.authenticate(self.reader_token)

        # -----------------------------------------------------------------
        # ACT: Request the newsletter-list endpoint
        # -----------------------------------------------------------------
        response = self.client.get(self.newsletter_list_url)

        # -----------------------------------------------------------------
        # ASSERT: Verify the response includes both newsletters created in
        #         setUp
        # -----------------------------------------------------------------
        # Checks: newsletter list endpoint returns HTTP 200 OK
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_titles = {item["title"] for item in response.data}
        # Checks: journalist's newsletter appears in the listed results
        self.assertIn("Journalist morning briefing", returned_titles)
        # Checks: editor's newsletter appears in the listed results
        self.assertIn("Editor oversight briefing", returned_titles)

    def test_editor_can_create_newsletter_via_api(self):
        """Verify an editor can create a newsletter through the API."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the editor and build a valid newsletter
        #          payload referencing an approved article
        # -----------------------------------------------------------------
        self.authenticate(self.editor_token)

        # -----------------------------------------------------------------
        # ACT: Submit the create request
        # -----------------------------------------------------------------
        response = self.client.post(
            self.newsletter_list_url,
            {
                "title": "Editor API edition",
                "description": "Created through the newsletter endpoint.",
                "category_id": self.category.pk,
                "article_ids": [self.approved_article.pk],
            },
            format="json",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the API creates the newsletter under the editor's
        #         account and links the selected article
        # -----------------------------------------------------------------
        # Checks: newsletter creation returns HTTP 201 Created for editors
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        newsletter = Newsletter.objects.get(title="Editor API edition")
        # Checks: created newsletter is attributed to the submitting editor
        self.assertEqual(newsletter.author, self.editor)
        # Checks: created newsletter is linked to the submitted category
        self.assertEqual(newsletter.category, self.category)
        # Checks: created newsletter includes the submitted approved article
        self.assertEqual(
            list(newsletter.articles.values_list("pk", flat=True)),
            [self.approved_article.pk],
        )

    def test_reader_cannot_create_newsletter_via_api(self):
        """Verify readers cannot create newsletters through the API."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the reader role, which is limited to
        #          newsletter viewing rather than authoring
        # -----------------------------------------------------------------
        self.authenticate(self.reader_token)

        # -----------------------------------------------------------------
        # ACT: Attempt the forbidden create request
        # -----------------------------------------------------------------
        response = self.client.post(
            self.newsletter_list_url,
            {
                "title": "Reader API edition",
                "description": "Should be rejected.",
                "category_id": self.category.pk,
                "article_ids": [self.approved_article.pk],
            },
            format="json",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the request is rejected and no newsletter row is
        #         created
        # -----------------------------------------------------------------
        # Checks: reader's create attempt returns HTTP 403 Forbidden
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # Checks: no newsletter row is created after the rejected request
        self.assertFalse(
            Newsletter.objects.filter(title="Reader API edition").exists()
        )

    def test_journalist_can_update_own_newsletter_via_patch(self):
        """Verify a journalist can update their own newsletter through the API."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the journalist who authored the target
        #          newsletter
        # -----------------------------------------------------------------
        self.authenticate(self.journalist_token)

        # -----------------------------------------------------------------
        # ACT: Patch the journalist-owned newsletter
        # -----------------------------------------------------------------
        response = self.client.patch(
            f"/api/newsletters/{self.journalist_newsletter.pk}/",
            {"title": "Journalist API briefing updated"},
            format="json",
        )

        # -----------------------------------------------------------------
        # ASSERT: Verify the update succeeds and the stored title changes
        # -----------------------------------------------------------------
        # Checks: journalist's PATCH on their own newsletter returns HTTP 200
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.journalist_newsletter.refresh_from_db()
        # Checks: newsletter title is updated to the submitted value
        self.assertEqual(
            self.journalist_newsletter.title,
            "Journalist API briefing updated",
        )

    def test_editor_can_delete_newsletter_via_api(self):
        """Verify an editor can delete a newsletter through the API."""
        # -----------------------------------------------------------------
        # ARRANGE: Authenticate as the editor and target a newsletter
        #          authored by another role
        # -----------------------------------------------------------------
        self.authenticate(self.editor_token)
        target_pk = self.journalist_newsletter.pk

        # -----------------------------------------------------------------
        # ACT: Issue the DELETE request against the newsletter-detail
        #      endpoint
        # -----------------------------------------------------------------
        response = self.client.delete(f"/api/newsletters/{target_pk}/")

        # -----------------------------------------------------------------
        # ASSERT: Verify the row is removed and the API returns HTTP 204
        # -----------------------------------------------------------------
        # Checks: editor's DELETE returns HTTP 204 No Content
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        # Checks: deleted newsletter row is removed from the database
        self.assertFalse(Newsletter.objects.filter(pk=target_pk).exists())
