"""In-principle mock implementation of an optional outbound announcement POST.

Background
----------
The original capstone brief (06-052 Capstone Project - News Application.pdf,
CF11) required a `requests.post()` call to X/Twitter whenever an article was
approved.  HyperionDev Support subsequently confirmed — in response to a query
about the inaccessibility of the X API — that the API section is optional and
the task can be completed without it.

This module is therefore an in-principle implementation: it demonstrates the
full approval-announcement wiring (signal → client → outbound POST) without
targeting a live social platform.  The POST goes to the project's own
MockAnnouncementAPIView endpoint (api_views.py), which validates the payload
and echoes it back so the complete side-effect path can be exercised in tests
and during local development without any external dependency or credentials.

In a real deployment the only change needed would be to point
ANNOUNCEMENT_ENDPOINT at the live social-media or notification service and
adjust the payload fields to match its API contract.

Call path
---------
    Article.post_save signal (signals.py)
        → post_article_approval_announcement(article)
            → build_article_announcement_payload(article)   [assembles JSON]
            → requests.post(ANNOUNCEMENT_ENDPOINT, json=payload)
                → MockAnnouncementAPIView (api_views.py)    [validates & acks]
"""

import logging
# Cf. Jester(p. 293): "Logging is your lowest level of introspection—recording
# events and messages that occur during pipeline execution. However, raw
# print() statements are not enough. Production-grade logging needs structure,
# timestamps, severity levels, and ideally JSON formatting to be
# machine-parsable."
# Cf. https://docs.python.org/3/library/logging.html
# https://www.geeksforgeeks.org/python/logging-in-python/

# The `requests` library is the standard Python HTTP client.
import requests as http_requests

from django.conf import settings  # Gives access to project settings at runtime
from django.urls import reverse   # Resolves a named URL pattern to its path


# Each module gets its own logger so log messages are easy to filter by source.
# __name__ evaluates to "daily_indaba.announcement_client".
logger = logging.getLogger(__name__)


def build_article_announcement_payload(article):
    """Return the JSON payload sent to the internal mock announcement API."""

    # Build the full public URL for the article so the announcement contains a
    # clickable link.  SITE_BASE_URL comes from settings
    # (default: localhost:8000);
    # rstrip('/') prevents a double-slash when reverse() returns a leading
    # slash.
    article_url = (
        getattr(settings, 'SITE_BASE_URL', 'http://localhost:8000').rstrip('/')
        # reverse() looks up the URL pattern named 'article_detail' inside the
        # 'news' app namespace and injects the article's primary key as <pk>.
        + reverse('news:article_detail', kwargs={'pk': article.pk})
    )

    # This dictionary will be serialised to JSON by requests (json= kwarg).
    # The field names mirror what MockAnnouncementAPIView expects to validate.
    return {
        # Discriminator so the receiver knows what kind of event this is:
        "announcement_type": "article_approval",
        # DB primary key; lets the receiver look the article up if needed:
        "article_id": article.pk,
        # Human-readable headline for display in the announcement:
        "title": article.title,
        # Full absolute URL constructed above:
        "article_url": article_url,
        # Display name (not username) of the journalist
        "author": article.author.public_name,
        # publisher is optional on an Article, so guard against None"
        "publisher": article.publisher.name if article.publisher else "",
        # approved_by is set by the editor who approved; guard against None
        "approved_by": (
            article.approved_by.public_name if article.approved_by else ""
        ),
        "kind": "link",  # Announcement format hint — this one carries a URL,
        #                  not plain text
    }


def post_article_approval_announcement(article):
    """POST the approval payload to the configured internal mock endpoint.

    Returns True if the POST succeeded (HTTP 2xx), False otherwise.
    Failures are logged as warnings rather than raised.
    """

    # Read the target URL from settings so it can be overridden via the
    # DJANGO_ANNOUNCEMENT_ENDPOINT environment variable without touching code.
    # .strip() removes accidental leading/trailing whitespace from env values.
    endpoint = getattr(settings, "ANNOUNCEMENT_ENDPOINT", "").strip()

    # If no endpoint is configured (e.g. the env var was explicitly cleared)
    # skip the POST silently.  This is expected behaviour, not an error.
    if not endpoint:
        logger.info(
            "Announcement POST skipped for article %s - no endpoint.",
            article.pk,
        )
        if getattr(settings, "DEBUG", False):
            print(f"[announcement] skipped article {article.pk}: no endpoint")
        return False

    # Assemble the JSON body before opening the network connection.
    payload = build_article_announcement_payload(article)

    try:
        # json=payload tells requests to serialise the dict to JSON and set
        # Content-Type: application/json automatically.
        # timeout prevents the approval request from hanging indefinitely if
        # the mock endpoint is slow or unreachable; default is 5 seconds.
        response = http_requests.post(
            endpoint,
            json=payload,
            timeout=getattr(settings, "ANNOUNCEMENT_TIMEOUT", 5),
        )
        # raise_for_status() converts any 4xx / 5xx HTTP response into a
        # requests.HTTPError (a subclass of RequestException), unifying error
        # handling in the except block below.
        response.raise_for_status()

    except http_requests.RequestException as exc:
        # RequestException is the base class for all requests errors, covering
        # connection failures, timeouts, and HTTP error status codes alike.
        # Log at WARNING so it surfaces in monitoring without crashing the view.
        logger.warning(
            "Mock announcement POST failed for article %s using endpoint=%s: %s",
            article.pk,
            endpoint,
            exc,
        )
        if getattr(settings, "DEBUG", False):
            print(f"[announcement] failed article {article.pk}: {exc}")
        return False  # Caller (signal handler) can decide whether to retry or ignore

    logger.info(
        "Mock announcement POST sent for article %s to %s.",
        article.pk,
        endpoint,
    )
    if getattr(settings, "DEBUG", False):
        print(f"[announcement] sent article {article.pk} -> {endpoint}")
    return True  # POST completed with a 2xx response
