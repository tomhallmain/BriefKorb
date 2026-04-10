"""
URL patterns for OAuth callbacks and authentication
"""

from django.urls import path
from . import views

app_name = 'django_app.oauth'

urlpatterns = [
    # OAuth callbacks
    path('auth/microsoft/callback', views.microsoft_callback, name='microsoft_callback'),
    path('auth/gmail/callback', views.gmail_callback, name='gmail_callback'),
    
    # Web-based sign-in/sign-out
    path('auth/microsoft/signin', views.sign_in_microsoft, name='sign_in_microsoft'),
    path('auth/gmail/signin', views.sign_in_gmail, name='sign_in_gmail'),
    path('auth/signout', views.sign_out, name='sign_out'),
]
