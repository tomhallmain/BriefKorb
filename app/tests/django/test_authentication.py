"""Tests for django_app/authentication.py: the external-facing (non-session)
bearer-token auth used by messages_api_view (see
docs/external-message-api-spec.md).

_load_external_api_config() resolves config.yaml via
EmailServerConfig.resolve_path(), which honors BRIEFKORB_CONFIG_PATH ahead
of the real app_dir-derived path -- same isolation as the other Django
view/config tests, no _get_app_dir-style patch needed here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import pytest
from django.http import JsonResponse
from django.test import RequestFactory

from django_app.authentication import authenticate_external_api_request, require_external_api_token
from email_server.config import EmailServerConfig, ExternalApiConfig, ExternalApiToken, ProviderConfig

rf = RequestFactory()


def _write_external_api_config(tokens: List[ExternalApiToken], enabled: bool = True) -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=False),
        gmail=ProviderConfig(enabled=False),
        external_api=ExternalApiConfig(enabled=enabled, tokens=tokens),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])


# --- authenticate_external_api_request ----------------------------------------

def test_returns_none_when_no_authorization_header() -> None:
    request = rf.get('/api/messages')
    assert authenticate_external_api_request(request) is None


def test_returns_none_when_header_is_not_bearer_scheme() -> None:
    request = rf.get('/api/messages', HTTP_AUTHORIZATION='Basic dXNlcjpwYXNz')
    assert authenticate_external_api_request(request) is None


def test_returns_none_when_bearer_token_is_blank() -> None:
    request = rf.get('/api/messages', HTTP_AUTHORIZATION='Bearer    ')
    assert authenticate_external_api_request(request) is None


def test_returns_none_when_config_file_does_not_exist() -> None:
    request = rf.get('/api/messages', HTTP_AUTHORIZATION='Bearer some-token')
    assert authenticate_external_api_request(request) is None


def test_returns_none_when_external_api_disabled() -> None:
    _write_external_api_config([ExternalApiToken(token='good-token', label='tagesform')], enabled=False)
    request = rf.get('/api/messages', HTTP_AUTHORIZATION='Bearer good-token')

    assert authenticate_external_api_request(request) is None


def test_returns_none_when_token_not_registered() -> None:
    _write_external_api_config([ExternalApiToken(token='good-token', label='tagesform')], enabled=True)
    request = rf.get('/api/messages', HTTP_AUTHORIZATION='Bearer wrong-token')

    assert authenticate_external_api_request(request) is None


def test_returns_label_for_matching_token() -> None:
    _write_external_api_config([
        ExternalApiToken(token='other-token', label='other-consumer'),
        ExternalApiToken(token='good-token', label='tagesform'),
    ], enabled=True)
    request = rf.get('/api/messages', HTTP_AUTHORIZATION='Bearer good-token')

    assert authenticate_external_api_request(request) == 'tagesform'


def test_returns_unlabeled_when_matching_token_has_no_label() -> None:
    _write_external_api_config([ExternalApiToken(token='good-token', label='')], enabled=True)
    request = rf.get('/api/messages', HTTP_AUTHORIZATION='Bearer good-token')

    assert authenticate_external_api_request(request) == 'unlabeled'


# --- require_external_api_token -----------------------------------------------

def test_decorator_returns_401_json_when_unauthorized() -> None:
    @require_external_api_token
    def view(request):
        raise AssertionError('view should not be called when unauthorized')

    request = rf.get('/api/messages')
    response = view(request)

    assert isinstance(response, JsonResponse)
    assert response.status_code == 401


def test_decorator_calls_view_and_sets_consumer_label_when_authorized() -> None:
    _write_external_api_config([ExternalApiToken(token='good-token', label='tagesform')], enabled=True)
    captured = {}

    @require_external_api_token
    def view(request):
        captured['consumer'] = request.external_api_consumer
        return JsonResponse({'ok': True})

    request = rf.get('/api/messages', HTTP_AUTHORIZATION='Bearer good-token')
    response = view(request)

    assert response.status_code == 200
    assert captured['consumer'] == 'tagesform'
