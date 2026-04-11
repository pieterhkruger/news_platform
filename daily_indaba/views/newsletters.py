"""
Newsletter views for The Daily Indaba.

- newsletter_list: All-newsletters listing page ordered newest first.
- newsletter_detail: Single newsletter showing its approved articles.
- newsletter_create: Journalist or editor creates a new newsletter.
- newsletter_edit: Journalist edits their own newsletter; editors may
  edit any newsletter on the platform.
- newsletter_delete: Journalist or editor deletes a newsletter;
  confirmation page on GET, deletion on POST.
- category_articles: Approved articles filtered by navigation
  category (uses Article.category, not newsletter category labels).
"""

# Flash messages show the outcome after create/edit/delete redirects.
from django.contrib import messages
# All newsletter management screens require an authenticated user.
from django.contrib.auth.decorators import login_required
# Shortcuts for fetching newsletter rows or rendering the matching template.
from django.shortcuts import get_object_or_404, redirect, render

# ModelForm used for newsletter create/edit validation.
from ..forms import NewsletterForm
# Article powers category feeds directly, while Newsletter and
# NewsletterCategory support the newsletter workflow and optional edition labels.
from ..models import Article, Newsletter, NewsletterCategory


# =============================================================================
# CORE REQUIREMENT - Newsletter list/detail/create/edit/delete views supporting
# the brief's curated-newsletter workflow for journalists and editors.
# =============================================================================
@login_required
def newsletter_list(request):
    """Render the all-newsletters listing page."""
    # Load newsletters with the related author/category rows needed by the list template.
    newsletters = (
        Newsletter.objects
        # Tell Django to join the author and category tables up front for
        # efficiency.
        .select_related('author', 'category')
        # Add ORDER BY created_at DESC.
        .order_by('-created_at')
    )
    return render(request, 'news/newsletter_list.html', {
        'newsletters': newsletters,
    })


@login_required
def newsletter_detail(request, pk):
    """Render a single newsletter with its approved articles."""
    # Load the newsletter together with its author and optional category label.
    newsletter = get_object_or_404(
        # Tell Django to join the author and category tables up front for
        # efficiency.
        Newsletter.objects.select_related('author', 'category'),
        pk=pk,
    )
    # Show only approved articles inside the newsletter detail page.
    articles = (
        # Build a QuerySet from the newsletter -> articles many-to-many relation.
        newsletter.articles
        # Add WHERE approved = true so only published articles are shown.
        .filter(approved=True)
        # Tell Django to join these related tables up front for efficiency.
        .select_related('author', 'publisher', 'category')
        # Add ORDER BY importance ASC, publication_date DESC.
        .order_by('importance', '-publication_date')
    )
    # Render the newsletter plus the approved articles selected for it.
    return render(request, 'news/newsletter_detail.html', {
        'newsletter': newsletter,
        'articles': articles,
    })


@login_required
def newsletter_create(request):
    """Allow a journalist or editor to create a new newsletter."""
    # Journalists and editors may author newsletters.
    if request.user.role not in {'journalist', 'editor'}:
        messages.error(request, "Access denied.")
        return redirect('news:home')

    if request.method == 'POST':
        # Bind the submitted form data to create a newsletter form instance.
        form = NewsletterForm(request.POST)
        if form.is_valid():
            # Delay the initial save so the view can stamp in the author.
            newsletter = form.save(commit=False)
            # Set the author to the logged-in user.
            newsletter.author = request.user
            # Save the newsletter row before attaching selected articles.
            newsletter.save()
            # Save the many-to-many relationships (selected articles).
            form.save_m2m()
            # Show a success message to the user.
            messages.success(request, "Newsletter created.")
            # Redirect to the detail page of the created newsletter.
            return redirect(
                'news:newsletter_detail', pk=newsletter.pk
            )
    else:
        # A GET request starts with a blank form ready for drafting a newsletter.
        form = NewsletterForm()

    return render(request, 'news/newsletter_form.html', {
        'form': form,
        'form_title': 'Create Newsletter',
    })


@login_required
def newsletter_edit(request, pk):
    """Allow a journalist (own newsletter) or editor to edit a newsletter."""
    # Load the newsletter that may be edited.
    newsletter = get_object_or_404(Newsletter, pk=pk)

    if request.user.role == 'journalist':
        # Journalists may only edit newsletters they authored themselves.
        if newsletter.author != request.user:
            messages.error(
                request, "You cannot edit this newsletter."
            )
            return redirect('news:journalist_dashboard')
    elif request.user.role != 'editor':
        # Readers and other roles cannot edit newsletters.
        messages.error(request, "Access denied.")
        return redirect('news:home')
    # Editors fall through here and may edit any newsletter on the platform.

    if request.method == 'POST':
        # Bind the submitted edits to the existing newsletter instance.
        form = NewsletterForm(request.POST, instance=newsletter)
        if form.is_valid():
            # Save the edited newsletter to the database.
            form.save()
            # Show a success message and redirect to the detail page.
            messages.success(request, "Newsletter updated.")
            return redirect(
                'news:newsletter_detail', pk=newsletter.pk
            )
    else:
        # For GET, pre-populate the form so the newsletter can be edited in place.
        form = NewsletterForm(instance=newsletter)

    return render(request, 'news/newsletter_form.html', {
        'form': form,
        'form_title': 'Edit Newsletter',
        'newsletter': newsletter,
    })


@login_required
def newsletter_delete(request, pk):
    """Allow a journalist (own) or editor to delete a newsletter."""
    # Load the newsletter selected for deletion.
    newsletter = get_object_or_404(Newsletter, pk=pk)

    if request.user.role == 'journalist':
        # Journalists may only delete newsletters they authored.
        if newsletter.author != request.user:
            messages.error(
                request, "You cannot delete this newsletter."
            )
            return redirect('news:journalist_dashboard')
    elif request.user.role != 'editor':
        # Readers and other non-editors cannot delete newsletters.
        messages.error(request, "Access denied.")
        return redirect('news:home')

    if request.method == 'POST':
        # Only delete after the confirmation form is submitted.
        newsletter.delete()
        messages.success(request, "Newsletter deleted.")
        # After deletion, return to the main newsletter listing.
        return redirect('news:newsletters')

    # On GET, render the confirmation page instead of deleting immediately.
    return render(request, 'news/newsletter_confirm_delete.html', {
        'newsletter': newsletter,
    })


@login_required
def category_articles(request, slug):
    """Show approved articles belonging to the selected navigation category."""
    # Resolve the category selected from the public navigation links.
    category = get_object_or_404(NewsletterCategory, slug=slug)
    # Article.category is the authoritative taxonomy field used by the
    # navigation routes. Newsletter categories remain optional edition labels.
    articles = (
        Article.objects
        # Add WHERE approved = true AND category_id = category.pk.
        .filter(
            approved=True,
            category=category,
        )
        # Tell Django to join these related tables up front for efficiency.
        .select_related('author', 'publisher', 'category')
        # Add ORDER BY importance ASC, publication_date DESC, created_at DESC.
        .order_by('importance', '-publication_date', '-created_at')
    )
    return render(request, 'news/category_article_list.html', {
        'category': category,
        'articles': articles,
    })
