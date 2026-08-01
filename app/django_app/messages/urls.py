"""
URL patterns for messages app
"""

from django.urls import path
from . import views

app_name = 'django_app.messages'

urlpatterns = [
    path('messages', views.messages_view, name='messages'),
    path('messages/inbox', views.inbox_view, name='inbox'),
    path('messages/read/<str:provider>/<str:message_id>/', views.message_detail_view, name='message_detail'),
    path('messages/categorization', views.sender_categorization_view, name='sender_categorization'),
    path('api/messages', views.messages_api_view, name='messages_api'),
]
