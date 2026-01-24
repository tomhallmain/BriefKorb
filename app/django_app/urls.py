"""
URL configuration for BriefKorb OAuth callbacks.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings

urlpatterns = [
    path('', include('django_app.home.urls')),
    path('', include('django_app.oauth.urls')),
    path('', include('django_app.calendar.urls')),
    path('', include('django_app.messages.urls')),
    path('admin/', admin.site.urls),
]

# Optionally include legacy tutorial UI routes (preserved for historical purposes)
if getattr(settings, 'LEGACY_UI_ENABLED', False):
    urlpatterns.insert(0, path('', include('tutorial.urls')))
