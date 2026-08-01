"""Tests for email_server/auth/microsoft.py's MicrosoftOAuth.

microsoft.py does ``import msal`` / ``import requests`` (module imports, not
``from ... import Name``), so monkeypatching attributes directly on
``email_server.auth.microsoft.msal`` / ``...requests`` is enough -- no need
to reach for the underlying third-party modules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from email_server.auth import TokenManager
from email_server.auth import microsoft as microsoft_module
from email_server.auth.microsoft import MicrosoftOAuth


def _oauth(tmp_path: Path, **kwargs: Any) -> MicrosoftOAuth:
    token_manager = kwargs.pop('token_manager', None) or TokenManager(storage_path=str(tmp_path))
    defaults: Dict[str, Any] = dict(
        client_id='cid', client_secret='csecret', tenant_id='tenant-id',
        redirect_uri='http://x/callback', token_manager=token_manager,
    )
    defaults.update(kwargs)
    return MicrosoftOAuth(**defaults)


@dataclass
class FakeMSALCache:
    has_state_changed: bool = False
    serialized: str = 'serialized-cache-data'
    deserialize_calls: List[str] = field(default_factory=list)

    def serialize(self) -> str:
        return self.serialized

    def deserialize(self, data: str) -> None:
        self.deserialize_calls.append(data)


@dataclass
class FakeMSALApp:
    init_flow_result: Dict[str, Any] = field(default_factory=lambda: {'auth_uri': 'http://fake-msal-auth-url'})
    acquire_token_result: Dict[str, Any] = field(default_factory=dict)
    accounts: List[Any] = field(default_factory=list)
    acquire_silent_result: Optional[Dict[str, Any]] = None
    initiate_calls: List[Dict[str, Any]] = field(default_factory=list)
    acquire_by_code_calls: List[Any] = field(default_factory=list)
    acquire_silent_calls: List[Dict[str, Any]] = field(default_factory=list)

    def initiate_auth_code_flow(self, scopes=None, redirect_uri=None):
        self.initiate_calls.append({'scopes': scopes, 'redirect_uri': redirect_uri})
        return self.init_flow_result

    def acquire_token_by_auth_code_flow(self, flow, request_dict):
        self.acquire_by_code_calls.append((flow, request_dict))
        return self.acquire_token_result

    def get_accounts(self):
        return self.accounts

    def acquire_token_silent(self, scopes=None, account=None):
        self.acquire_silent_calls.append({'scopes': scopes, 'account': account})
        return self.acquire_silent_result


def _patch_msal(monkeypatch: pytest.MonkeyPatch, app: Optional[FakeMSALApp] = None, cache: Optional[FakeMSALCache] = None) -> FakeMSALApp:
    app = app or FakeMSALApp()
    cache = cache or FakeMSALCache()
    monkeypatch.setattr(microsoft_module.msal, 'SerializableTokenCache', lambda: cache)
    monkeypatch.setattr(microsoft_module.msal, 'ConfidentialClientApplication', lambda *a, **k: app)
    return app


# --- __init__ ------------------------------------------------------------

def test_init_uses_default_scopes_when_not_provided(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    assert oauth.scopes == [
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/Mail.Send",
    ]


def test_init_uses_provided_scopes(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path, scopes=['custom.scope'])
    assert oauth.scopes == ['custom.scope']


# --- get_auth_url ----------------------------------------------------------

def test_get_auth_url_returns_url_and_stores_current_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    fake_app = _patch_msal(monkeypatch)

    url = oauth.get_auth_url()

    assert url == 'http://fake-msal-auth-url'
    assert oauth._current_flow == fake_app.init_flow_result
    assert fake_app.initiate_calls == [{'scopes': oauth.scopes, 'redirect_uri': 'http://x/callback'}]


def test_get_auth_url_with_user_id_caches_flow_and_loads_token_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    oauth.token_manager.store_token('user1', {'access_token': 'tok', 'msal_cache': 'existing-serialized'})
    fake_cache = FakeMSALCache()
    fake_app = _patch_msal(monkeypatch, cache=fake_cache)

    url = oauth.get_auth_url(user_id='user1')

    assert url == 'http://fake-msal-auth-url'
    assert fake_cache.deserialize_calls == ['existing-serialized']
    assert oauth._auth_flow_cache['user1'] == fake_app.init_flow_result


def test_get_auth_url_raises_value_error_when_no_auth_uri(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    _patch_msal(monkeypatch, app=FakeMSALApp(init_flow_result={}))

    with pytest.raises(ValueError):
        oauth.get_auth_url()


def test_get_auth_url_propagates_exception_from_msal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)

    def raise_app(*a: Any, **k: Any) -> Any:
        raise RuntimeError('msal init failed')

    monkeypatch.setattr(microsoft_module.msal, 'ConfidentialClientApplication', raise_app)

    with pytest.raises(RuntimeError, match='msal init failed'):
        oauth.get_auth_url()


# --- get_token_from_code ----------------------------------------------------

def test_get_token_from_code_raises_when_no_flow_available(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    with pytest.raises(ValueError):
        oauth.get_token_from_code('some-code')


def test_get_token_from_code_success_with_explicit_flow_and_user_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    flow = {'auth_uri': 'x'}
    fake_app = _patch_msal(monkeypatch, app=FakeMSALApp(acquire_token_result={
        'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600,
        'token_type': 'Bearer', 'scope': 's', 'id_token': 'idt',
    }))

    result = oauth.get_token_from_code('auth-code', user_id='user1', flow=flow)

    assert result['access_token'] == 'at'
    assert fake_app.acquire_by_code_calls == [(flow, {'code': 'auth-code'})]
    assert oauth.token_manager.get_token('user1')['access_token'] == 'at'
    assert oauth._current_flow is None


def test_get_token_from_code_uses_current_flow_and_extracts_user_id_from_claims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    oauth._current_flow = {'auth_uri': 'x'}
    _patch_msal(monkeypatch, app=FakeMSALApp(acquire_token_result={
        'access_token': 'at', 'id_token_claims': {'preferred_username': 'user@example.com'},
    }))

    oauth.get_token_from_code('auth-code')

    assert oauth.token_manager.get_token('user@example.com')['access_token'] == 'at'


def test_get_token_from_code_finds_flow_from_auth_flow_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    flow = {'auth_uri': 'x'}
    oauth._auth_flow_cache['user1'] = flow
    fake_app = _patch_msal(monkeypatch, app=FakeMSALApp(acquire_token_result={'access_token': 'at'}))

    oauth.get_token_from_code('auth-code')

    assert fake_app.acquire_by_code_calls == [(flow, {'code': 'auth-code'})]
    assert oauth.token_manager.get_token('user1')['access_token'] == 'at'


def test_get_token_from_code_raises_runtime_error_on_msal_error_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    flow = {'auth_uri': 'x'}
    _patch_msal(monkeypatch, app=FakeMSALApp(acquire_token_result={
        'error': 'invalid_grant', 'error_description': 'code expired',
    }))

    with pytest.raises(RuntimeError, match='code expired'):
        oauth.get_token_from_code('bad-code', user_id='user1', flow=flow)


def test_get_token_from_code_preserves_existing_msal_cache_when_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    oauth.token_manager.store_token('user1', {'access_token': 'old', 'msal_cache': 'existing-serialized'})
    flow = {'auth_uri': 'x'}
    _patch_msal(monkeypatch, cache=FakeMSALCache(has_state_changed=False), app=FakeMSALApp(acquire_token_result={'access_token': 'new-at'}))

    oauth.get_token_from_code('auth-code', user_id='user1', flow=flow)

    assert oauth.token_manager.get_token('user1')['msal_cache'] == 'existing-serialized'


# --- refresh_token -----------------------------------------------------------

def test_refresh_token_posts_expected_request_and_returns_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    # refresh_token() constructs a real MSAL ConfidentialClientApplication
    # (via _get_msal_app) before falling through to the manual POST below --
    # the result is unused, but skipping this patch makes MSAL perform a live
    # authority/tenant discovery network call.
    _patch_msal(monkeypatch)
    captured: Dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> Dict[str, Any]:
            return {'access_token': 'new-at', 'refresh_token': 'new-rt'}

    def fake_post(url: str, data: Optional[Dict[str, Any]] = None) -> FakeResponse:
        captured['url'] = url
        captured['data'] = data
        return FakeResponse()

    monkeypatch.setattr(microsoft_module.requests, 'post', fake_post)

    result = oauth.refresh_token('old-refresh-token')

    assert result == {'access_token': 'new-at', 'refresh_token': 'new-rt'}
    assert captured['url'] == f"{oauth.authority}/oauth2/v2.0/token"
    assert captured['data']['refresh_token'] == 'old-refresh-token'
    assert captured['data']['grant_type'] == 'refresh_token'


def test_refresh_token_propagates_http_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    _patch_msal(monkeypatch)

    class FakeResponse:
        def raise_for_status(self) -> None:
            raise RuntimeError('400 client error')

    monkeypatch.setattr(microsoft_module.requests, 'post', lambda url, data=None: FakeResponse())

    with pytest.raises(RuntimeError, match='400 client error'):
        oauth.refresh_token('old-refresh-token')


# --- get_user_info -------------------------------------------------------------

def test_get_user_info_returns_json_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    captured: Dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> Dict[str, Any]:
            return {'mail': 'user@example.com'}

    def fake_get(url: str, headers: Optional[Dict[str, str]] = None) -> FakeResponse:
        captured['url'] = url
        captured['headers'] = headers
        return FakeResponse()

    monkeypatch.setattr(microsoft_module.requests, 'get', fake_get)

    result = oauth.get_user_info('a-token')

    assert result == {'mail': 'user@example.com'}
    assert captured['url'] == f"{oauth.graph_url}/me"
    assert captured['headers'] == {'Authorization': 'Bearer a-token'}


def test_get_user_info_propagates_request_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)

    def fake_get(url: str, headers: Optional[Dict[str, str]] = None) -> Any:
        raise microsoft_module.requests.exceptions.RequestException('network down')

    monkeypatch.setattr(microsoft_module.requests, 'get', fake_get)

    with pytest.raises(microsoft_module.requests.exceptions.RequestException):
        oauth.get_user_info('a-token')


# --- get_valid_token -----------------------------------------------------------

def test_get_valid_token_returns_none_when_no_stored_token(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    assert oauth.get_valid_token('user1') is None


def test_get_valid_token_returns_none_for_wrong_provider_type(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    oauth.token_manager.store_token('user1', {'token': 'a-gmail-shaped-token', 'token_uri': 'https://oauth2.googleapis.com/token'})

    assert oauth.get_valid_token('user1') is None


def test_get_valid_token_returns_fresh_stored_token_without_touching_msal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    stored = {'access_token': 'fresh-at', 'acquired_at': time.time(), 'expires_in': 3600}
    oauth.token_manager.store_token('user1', stored)

    def fail_if_called() -> Any:
        raise AssertionError('should not touch msal when the stored token is still fresh')

    monkeypatch.setattr(microsoft_module.msal, 'SerializableTokenCache', fail_if_called)

    assert oauth.get_valid_token('user1') == stored


def test_get_valid_token_falls_back_to_stored_token_when_no_accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    stored = {'access_token': 'stale-at'}
    oauth.token_manager.store_token('user1', stored)
    _patch_msal(monkeypatch, app=FakeMSALApp(accounts=[]))

    assert oauth.get_valid_token('user1') == stored


def test_get_valid_token_refreshes_silently_when_accounts_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    oauth.token_manager.store_token('user1', {'access_token': 'stale-at'})
    fake_app = _patch_msal(
        monkeypatch,
        cache=FakeMSALCache(has_state_changed=True, serialized='new-cache-blob'),
        app=FakeMSALApp(accounts=['account1'], acquire_silent_result={
            'access_token': 'silent-at', 'refresh_token': 'silent-rt', 'expires_in': 3600,
        }),
    )

    result = oauth.get_valid_token('user1')

    assert result is not None
    assert result['access_token'] == 'silent-at'
    assert result['msal_cache'] == 'new-cache-blob'
    assert oauth.token_manager.get_token('user1')['access_token'] == 'silent-at'
    assert fake_app.acquire_silent_calls[0]['account'] == 'account1'


def test_get_valid_token_falls_back_to_stored_token_on_silent_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    stored = {'access_token': 'stale-at'}
    oauth.token_manager.store_token('user1', stored)
    _patch_msal(monkeypatch, app=FakeMSALApp(accounts=['account1'], acquire_silent_result={'error': 'invalid_grant'}))

    assert oauth.get_valid_token('user1') == stored


def test_get_valid_token_returns_none_on_unexpected_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    oauth.token_manager.store_token('user1', {'access_token': 'at'})

    def raise_error() -> Any:
        raise RuntimeError('cache blew up')

    monkeypatch.setattr(microsoft_module.msal, 'SerializableTokenCache', raise_error)

    assert oauth.get_valid_token('user1') is None
