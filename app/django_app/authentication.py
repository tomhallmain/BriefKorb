"""
Authentication for BriefKorb's external-facing (non-session) API.

Any view meant to be called by an external consumer -- not a browser
session -- should use `require_external_api_token` rather than Django's
normal session/cookie auth. Not specific to the messages endpoint: this
lives at the django_app level so any future external-facing view can
reuse it.
"""

import hmac
import sys
from functools import wraps
from pathlib import Path
from typing import Optional

from django.http import JsonResponse

sys.path.insert(0, str(Path(__file__).parent.parent))

from email_server.config import EmailServerConfig, ExternalApiConfig


def _get_app_dir() -> Path:
    return Path(__file__).parent.parent


def _load_external_api_config() -> ExternalApiConfig:
    """Load the `external_api` section of email_server/config.yaml.

    Returns a disabled/empty config (rather than raising) when config.yaml
    doesn't exist yet, so an unconfigured BriefKorb instance simply
    authorizes no one instead of erroring on every request.
    """
    config_path = EmailServerConfig.resolve_path(_get_app_dir())
    if not config_path.exists():
        return ExternalApiConfig()
    return EmailServerConfig.from_file(str(config_path)).external_api


def authenticate_external_api_request(request) -> Optional[str]:
    """Check the request's `Authorization: Bearer <token>` header against
    the registered token list.

    Returns the matching token's label (a non-empty string; "unlabeled" if
    the registered entry has no label set) on success, or None if the
    request is missing/unauthorized.
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return None
    presented_token = auth_header[len('Bearer '):].strip()
    if not presented_token:
        return None

    external_api = _load_external_api_config()
    if not external_api.enabled:
        return None

    for registered in external_api.tokens:
        # Constant-time comparison -- these are bearer secrets, not IDs.
        if hmac.compare_digest(registered.token, presented_token):
            return registered.label or "unlabeled"

    return None


def require_external_api_token(view_func):
    """Decorator: reject the request with 401 unless it carries a token
    registered in `external_api.tokens`. On success, attaches the token's
    label to `request.external_api_consumer` for logging."""
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        consumer_label = authenticate_external_api_request(request)
        if consumer_label is None:
            return JsonResponse({'error': 'Unauthorized'}, status=401)
        request.external_api_consumer = consumer_label
        return view_func(request, *args, **kwargs)
    return wrapped
