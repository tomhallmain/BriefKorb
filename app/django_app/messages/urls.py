"""
URL patterns for messages app
"""

from django.urls import path
from . import views

app_name = 'django_app.messages'

urlpatterns = [
    path('messages', views.messages_view, name='messages'),
]
