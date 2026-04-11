"""
URL configuration for news_platform project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.templatetags.static import static as static_url
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("daily_indaba.api_urls")),
    path("daily-indaba/", include("daily_indaba.urls")),
    path("accounts/", include("accounts.urls")),
    path(
        "favicon.ico",
        RedirectView.as_view(
            url=static_url("daily_indaba/favicon.ico"),
            permanent=False,
        ),
    ),
    path("", RedirectView.as_view(url="/daily-indaba/", permanent=False)),
]

# Guest et al. show the development-media URL mapping guarded by DEBUG so the
# Django development server can serve MEDIA_ROOT locally while production
# remains the responsibility of the front-end web server.  This explicit guard
# mirrors both the book's teaching example and Django's own documentation.
# See Web Development with Django 6, Packt, pp. 511-516.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)
