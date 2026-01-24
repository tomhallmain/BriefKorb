"""
ASGI config for BriefKorb OAuth callbacks.

It exposes the ASGI callable as a module-level variable named ``application``.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_app.settings')

application = get_asgi_application()
