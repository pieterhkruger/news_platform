"""Django admin configuration for the daily_indaba news app."""

# admin is Django's built-in site for CRUD-style staff management.
from django.contrib import admin

from .models import (
    Article,
    Comment,
    Newsletter,
    NewsletterCategory,
    Publisher,
    Subscription,
)


# =============================================================================
# CORE REQUIREMENT - This module contains the Django admin registrations that
# satisfy baseline administrator management of publishers, articles,
# newsletters, and subscriptions through the built-in admin interface.
# =============================================================================
@admin.register(Publisher)
class PublisherAdmin(admin.ModelAdmin):
    # list_display determines the columns shown on the publisher changelist.
    list_display = (
        'name',
        'account',
        'curates_independent_journalists',
        'monthly_fee',
        'created_at',
    )
    list_filter = ('curates_independent_journalists', 'created_at')
    search_fields = ('name', 'account__username', 'account__email')
    # filter_horizontal renders a dual-list widget for ManyToMany fields,
    # which is much easier to manage than the default multi-select box when
    # assigning editors and journalists.
    filter_horizontal = ('editors', 'journalists')


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    # readonly_fields show audit timestamps without letting admins edit them.
    list_display = (
        'title', 'author', 'publisher', 'category',
        'importance', 'status', 'approved', 'approved_by', 'publication_date',
    )
    list_filter = (
        'status',
        'approved',
        'importance',
        'publisher',
        'category',
        'approved_by',
    )
    search_fields = ('title', 'author__username', 'approved_by__username')
    readonly_fields = ('created_at', 'publication_date')


@admin.register(NewsletterCategory)
class NewsletterCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    # prepopulated_fields auto-fills the slug in the admin from the name field.
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'category', 'created_at')
    list_filter = ('category',)
    # Newsletters manage a ManyToMany article list, so the horizontal widget
    # is also appropriate here.
    filter_horizontal = ('articles',)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('reader', 'publisher', 'journalist', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('reader__username',)


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    # Depth is derived from the parent chain in the model and is not intended
    # for manual editing in the admin.
    list_display = (
        'author', 'article', 'depth', 'parent', 'created_at'
    )
    list_filter = ('depth', 'created_at')
    search_fields = ('author__username', 'body')
    readonly_fields = ('created_at', 'depth')
