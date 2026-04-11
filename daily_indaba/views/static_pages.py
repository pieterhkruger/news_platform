"""
Static / informational page views for The Daily Indaba.

- about: Renders the About page.
- contact: Renders the Contact page.
- privacy_policy: Renders the Privacy Policy page.
"""

# render() returns the matching template as a normal HTTP response.
from django.shortcuts import render


def about(request):
    """About The Daily Indaba."""
    # Show the static About page content.
    return render(request, 'news/about.html')


def contact(request):
    """Contact page."""
    # Show the static Contact page content.
    return render(request, 'news/contact.html')


def privacy_policy(request):
    """Privacy policy page."""
    # Show the static Privacy Policy page content.
    return render(request, 'news/privacy_policy.html')
