"""
URL patterns for home app
"""

from django.urls import path
from . import views

app_name = 'django_app.home'

urlpatterns = [
    path('', views.home_view, name='home'),
]
