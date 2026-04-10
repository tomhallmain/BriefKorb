from django.urls import path
from . import views

app_name = 'django_app.config'

urlpatterns = [
    path('settings/', views.settings_view, name='settings'),
]
