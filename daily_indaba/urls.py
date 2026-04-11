"""URL configuration for the daily_indaba news app.

URL structure by implementation classification
as it relates to the HyperionDev project specs
----------------------------------------------

All paths below are relative to ``/daily-indaba/``.

Support core requirements of Capstone project
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
/                              public home / approved-article discovery
/articles/                     authenticated article listing
/articles/new/                 create article (journalist)
/articles/<int:pk>/            article detail
/articles/<int:pk>/edit/       edit article (journalist own | editor scoped)
/articles/<int:pk>/delete/     delete article (journalist own draft | editor scoped)
/articles/<int:pk>/approve/    approve article (editor scoped)
/articles/<int:pk>/reject/     reject article with feedback (editor scoped)
/newsletters/                  newsletter listing
/newsletters/new/              create newsletter (journalist | editor)
/newsletters/<int:pk>/         newsletter detail
/newsletters/<int:pk>/edit/    edit newsletter (journalist own | editor)
/newsletters/<int:pk>/delete/  delete newsletter (journalist own | editor)
/journalist/                   journalist work-queue dashboard
/editor/                       editor overview dashboard
/editor/queue/                 full editorial approval queue
/publisher/                    publisher account dashboard

Good-to-have additions
~~~~~~~~~~~~~~~~~~~~~~
/category/<slug:slug>/         approved articles by article category
/publishers/<int:pk>/          public publisher profile
/journalists/<int:pk>/         public journalist profile

Useful extensions beyond core requirements
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
/articles/<int:pk>/comment/    post reader comment or reply (POST only)
/articles/<int:article_pk>/comment/<int:comment_pk>/delete/
                               delete reader comment thread (POST only)
/publishers/<int:pk>/editors/  publisher editor assignment
/subscribe/                    toggle reader subscription (POST only)
/subscribe/all-articles/       toggle all-articles plan (POST only)
/journalist/set-fee/           set journalist subscription fee (POST only)
/publishers/<int:pk>/set-fee/  set publisher subscription fee (POST only)

Extra features added
~~~~~~~~~~~~~~~~~~~~
/about/                        about page
/privacy-policy/               privacy policy page
/contact/                      contact page
/journalists/                  public journalist directory
/publishers/                   public publisher directory
/announcements/<int:pk>/       article-approval announcement (recipient only)
/announcements/<int:pk>/dismiss/ dismiss announcement without reading (POST)

"""

from django.urls import path

from . import views

app_name = 'news'

urlpatterns = [
    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    path('', views.home, name='home'),

    # ------------------------------------------------------------------
    # Articles
    # ------------------------------------------------------------------
    path('articles/', views.article_list, name='articles'),
    path('articles/new/', views.article_create, name='article_create'),
    path(
        'articles/<int:pk>/',
        views.article_detail,
        name='article_detail',
    ),
    path(
        'articles/<int:pk>/edit/',
        views.article_edit,
        name='article_edit',
    ),
    path(
        'articles/<int:pk>/delete/',
        views.article_delete,
        name='article_delete',
    ),
    path(
        'articles/<int:pk>/approve/',
        views.article_approve,
        name='article_approve',
    ),
    path(
        'articles/<int:pk>/reject/',
        views.article_reject,
        name='article_reject',
    ),
    path(
        'articles/<int:pk>/comment/',
        views.post_comment,
        name='post_comment',
    ),

    # ------------------------------------------------------------------
    # Newsletters
    # ------------------------------------------------------------------
    path('newsletters/', views.newsletter_list, name='newsletters'),
    path(
        'newsletters/new/',
        views.newsletter_create,
        name='newsletter_create',
    ),
    path(
        'newsletters/<int:pk>/',
        views.newsletter_detail,
        name='newsletter_detail',
    ),
    path(
        'newsletters/<int:pk>/edit/',
        views.newsletter_edit,
        name='newsletter_edit',
    ),
    path(
        'newsletters/<int:pk>/delete/',
        views.newsletter_delete,
        name='newsletter_delete',
    ),

    # ------------------------------------------------------------------
    # Category (used by navigation links in base.html)
    # ------------------------------------------------------------------
    path(
        'category/<slug:slug>/',
        views.category_articles,
        name='category',
    ),

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------
    path(
        'publishers/<int:pk>/',
        views.publisher_profile,
        name='publisher_profile',
    ),
    path('publisher/', views.publisher_dashboard, name='publisher_dashboard'),
    path(
        'publishers/<int:pk>/editors/',
        views.publisher_manage_editors,
        name='publisher_manage_editors',
    ),
    path(
        'journalists/<int:pk>/',
        views.journalist_profile,
        name='journalist_profile',
    ),

    # ------------------------------------------------------------------
    # Dashboards
    # ------------------------------------------------------------------
    path('journalist/', views.journalist_dashboard,
         name='journalist_dashboard'),
    path('editor/', views.editor_dashboard, name='editor_dashboard'),
    path('editor/queue/', views.approval_queue, name='approval_queue'),

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
    path(
        'subscribe/',
        views.toggle_subscription,
        name='toggle_subscription',
    ),

    # ------------------------------------------------------------------
    # Comment management
    # ------------------------------------------------------------------
    path(
        'articles/<int:article_pk>/comment/<int:comment_pk>/delete/confirm/',
        views.comment_confirm_delete,
        name='comment_confirm_delete',
    ),
    path(
        'articles/<int:article_pk>/comment/<int:comment_pk>/delete/',
        views.comment_delete,
        name='comment_delete',
    ),

    # ------------------------------------------------------------------
    # About / static pages
    # ------------------------------------------------------------------
    path('about/', views.about, name='about'),
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('journalists/', views.journalist_list, name='journalist_list'),
    path('publishers/', views.publisher_list, name='publisher_list'),
    path('contact/', views.contact, name='contact'),

    # ------------------------------------------------------------------
    # Subscriptions — all-articles flat-rate plan
    # ------------------------------------------------------------------
    path(
        'subscribe/all-articles/',
        views.subscribe_all_articles,
        name='subscribe_all_articles',
    ),

    # ------------------------------------------------------------------
    # Fee management
    # ------------------------------------------------------------------
    path(
        'journalist/set-fee/',
        views.set_journalist_fee,
        name='set_journalist_fee',
    ),
    path(
        'publishers/<int:pk>/set-fee/',
        views.publisher_set_fee,
        name='publisher_set_fee',
    ),

    # ------------------------------------------------------------------
    # Announcements — article-approval notifications
    # ------------------------------------------------------------------
    path(
        'announcements/<int:pk>/',
        views.announcement_detail,
        name='announcement_detail',
    ),
    path(
        'announcements/<int:pk>/dismiss/',
        views.dismiss_notification,
        name='dismiss_notification',
    ),
]
