"""
URL patterns for calendar app
"""

from django.urls import path
from . import views

app_name = 'django_app.calendar'

urlpatterns = [
    path('calendar', views.calendar_view, name='calendar'),
    path('calendar/new', views.new_event_view, name='new_event'),
]
