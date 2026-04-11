"""Forms for the daily_indaba news app.

This module contains the forms used by the web layer for the main Daily Indaba
content and editorial workflows:

- ``ArticleForm``: create/edit form for articles.
- ``NewsletterForm``: create/edit form for newsletters.
- ``CommentForm``: reader comment/reply form on article detail pages.
- ``RejectionForm``: editor feedback form for returning an article.
- ``JournalistFeeForm``: journalist subscription-fee management form.
- ``PublisherFeeForm``: publisher subscription-fee management form.
- ``PublisherEditorAssignmentForm``: publisher-owned editor assignment form.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator

from accounts.models import (
    get_journalist_fee_bounds,
    get_publisher_fee_bounds,
)

from .models import Article, Newsletter

User = get_user_model()


# =============================================================================
# CORE REQUIREMENT - Article create/edit form used by journalists (and editors
# when correcting content) for the brief's article-management workflow.
# =============================================================================
class ArticleForm(forms.ModelForm):
    """Create / edit form for journalist-authored articles.

    The ``publisher`` queryset must be narrowed to the journalist's
    affiliated publishers in the view::

        form.fields['publisher'].queryset = (
            request.user.journalist_publishers.all()
        )
    """

    class Meta:
        model = Article
        fields = [
            'title',
            'content',
            'importance',
            'publisher',
            'category',
            'image',
            'disclaimer',
        ]
        widgets = {
            'content': forms.Textarea(attrs={'rows': 14}),
            'importance': forms.RadioSelect(),
            'disclaimer': forms.Textarea(
                attrs={
                    'rows': 3,
                    'placeholder': (
                        'Add a bias disclaimer if applicable.'
                    ),
                }
            ),
        }
        labels = {
            'disclaimer': 'Bias Disclaimer (optional)',
        }
        help_texts = {
            'publisher': (
                'Leave blank to publish as an independent journalist.'
            ),
            'category': (
                'Choose the editorial category that best matches this article.'
            ),
            'image': (
                'Required for Front Page articles; optional for '
                'Top Story and Standard articles.'
            ),
        }


# =============================================================================
# CORE REQUIREMENT - Newsletter create/edit form supporting the brief's
# newsletter-management workflow for journalist- and editor-authored editions.
# =============================================================================
class NewsletterForm(forms.ModelForm):
    """Create / edit form for journalist- and editor-authored newsletters.

    Only approved articles are offered in the ``articles`` checklist.  The
    newsletter-level category label is optional because one edition may span
    multiple article categories.
    """

    class Meta:
        model = Newsletter
        fields = ['title', 'description', 'category', 'articles']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'articles': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        """Limit article choices to approved articles only."""
        super().__init__(*args, **kwargs)
        # A newletter does not have to be limited to a single category:
        self.fields['category'].required = False
        self.fields['articles'].queryset = (
            # Build a QuerySet for the newsletter article checklist.
            Article.objects
            # Add WHERE approved = true so unpublished articles cannot be
            # attached to a newsletter.
            .filter(approved=True)
            .select_related('author', 'category')
            # Tell Django to join the author and category tables up front for
            # efficiency.
            .order_by('importance', '-publication_date')
            # Add ORDER BY importance ASC, publication_date DESC.
        )


class CommentForm(forms.Form):
    """Reader comment / reply form for article detail pages."""

    body = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'rows': 3,
                'placeholder': 'Write your comment here…',
            }
        ),
        label='Comment',
        max_length=2000,
    )
    # Render parent_id as a hidden HTML input so the reply target is
    # submitted with the form without being shown as a visible field.
    parent_id = forms.IntegerField(
        widget=forms.HiddenInput(),
        required=False,
    )


class RejectionForm(forms.Form):
    """Optional editor comment supplied when returning an article."""

    reason = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'rows': 3,
                'class': 'form-control',
                'placeholder': (
                    'Add revision comments for the journalist '
                    '(optional).'
                ),
            }
        ),
        label='Editor comments for the journalist',
        required=False,
    )


class JournalistFeeForm(forms.Form):
    """Allow a journalist to set their configured monthly subscription fee."""

    journalist_monthly_fee = forms.DecimalField(
        decimal_places=2,
        max_digits=5,
        label="Monthly fee (ZAR)",
        widget=forms.NumberInput(attrs={'step': '1', 'class': 'form-control'}),
    )

    def __init__(self, *args, **kwargs):
        """Apply the current seeded journalist fee range to the form."""
        super().__init__(*args, **kwargs)
        minimum, default, maximum = get_journalist_fee_bounds()
        field = self.fields["journalist_monthly_fee"]
        field.min_value = minimum
        field.max_value = maximum
        field.validators.extend([MinValueValidator(minimum), MaxValueValidator(maximum)])
        field.help_text = (
            "Set your reader subscription fee between "
            f"R{minimum:.2f} and R{maximum:.2f} per month."
        )
        if not self.is_bound:
            self.initial.setdefault("journalist_monthly_fee", default)


class PublisherFeeForm(forms.Form):
    """Allow a publisher owner or affiliated editor to set the monthly fee."""

    monthly_fee = forms.DecimalField(
        decimal_places=2,
        max_digits=6,
        label="Monthly fee (ZAR)",
        widget=forms.NumberInput(attrs={'step': '1', 'class': 'form-control'}),
    )

    def __init__(self, *args, **kwargs):
        """Apply the current seeded publisher fee range to the form."""
        super().__init__(*args, **kwargs)
        minimum, default, maximum = get_publisher_fee_bounds()
        field = self.fields["monthly_fee"]
        field.min_value = minimum
        field.max_value = maximum
        field.validators.extend([MinValueValidator(minimum), MaxValueValidator(maximum)])
        field.help_text = (
            "Set the publisher subscription fee between "
            f"R{minimum:.2f} and R{maximum:.2f} per month."
        )
        if not self.is_bound:
            self.initial.setdefault("monthly_fee", default)


class PublisherEditorAssignmentForm(forms.Form):
    """Allow a publisher account to assign or remove registered editors."""

    editors = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="Assigned editors",
        help_text=(
            "Select the registered editors who should curate this "
            "publisher's articles."
        ),
    )

    def __init__(self, *args, **kwargs):
        """Limit choices to users with the editor role."""
        super().__init__(*args, **kwargs)
        self.fields["editors"].queryset = (
            User.objects
            # Add WHERE role = 'editor'.
            .filter(role="editor")
            # Add ORDER BY first_name ASC, last_name ASC, username ASC.
            .order_by("first_name", "last_name", "username")
        )
