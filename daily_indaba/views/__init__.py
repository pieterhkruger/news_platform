"""
Views package for The Daily Indaba news app.

Importing from the sub-modules here means that ``urls.py`` continues to
work without any change: it does ``from . import views`` and then accesses
``views.home``, ``views.article_list``, etc. exactly as before.
"""

from .articles import (
    article_approve,
    article_create,
    article_delete,
    article_detail,
    article_edit,
    article_list,
    article_reject,
    home,
)
from .comments import (
    comment_confirm_delete,
    comment_delete,
    post_comment,
)
from .dashboards import (
    approval_queue,
    editor_dashboard,
    journalist_dashboard,
    publisher_set_fee,
    set_journalist_fee,
    subscribe_all_articles,
    toggle_subscription,
)
from .newsletters import (
    category_articles,
    newsletter_create,
    newsletter_delete,
    newsletter_detail,
    newsletter_edit,
    newsletter_list,
)
from .profiles import (
    journalist_list,
    journalist_profile,
    publisher_dashboard,
    publisher_list,
    publisher_manage_editors,
    publisher_profile,
)
from .notifications import (
    announcement_detail,
    dismiss_notification,
)
from .static_pages import (
    about,
    contact,
    privacy_policy,
)

__all__ = [
    # articles
    "home",
    "article_list",
    "article_detail",
    "article_create",
    "article_edit",
    "article_delete",
    "article_approve",
    "article_reject",
    # comments
    "post_comment",
    "comment_confirm_delete",
    "comment_delete",
    # newsletters
    "newsletter_list",
    "newsletter_detail",
    "newsletter_create",
    "newsletter_edit",
    "newsletter_delete",
    "category_articles",
    # profiles
    "publisher_profile",
    "publisher_dashboard",
    "publisher_manage_editors",
    "journalist_profile",
    "journalist_list",
    "publisher_list",
    # dashboards
    "journalist_dashboard",
    "editor_dashboard",
    "approval_queue",
    "toggle_subscription",
    "subscribe_all_articles",
    "set_journalist_fee",
    "publisher_set_fee",
    # static pages
    "about",
    "contact",
    "privacy_policy",
    # notifications
    "announcement_detail",
    "dismiss_notification",
]
