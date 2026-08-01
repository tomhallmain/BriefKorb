"""Tests for email_server/auth/gmail.py's GmailOAuth.

Note: get_user_info() and the credential-building half of get_valid_token()
construct google.oauth2.credentials.Credentials directly at module scope in
gmail.py, so monkeypatching email_server.auth.gmail.Credentials (rather than
the original google.oauth2.credentials.Credentials) is enough there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from email_server.auth import TokenManager
from email_server.auth import gmail as gmail_module
from email_server.auth.gmail import GmailOAuth


def _oauth(tmp_path: Path) -> GmailOAuth:
    token_manager = TokenManager(storage_path=str(tmp_path))
    return GmailOAuth(credentials_path='creds.json', redirect_uri='http://x/callback', token_manager=token_manager)


# --- get_auth_url ------------------------------------------------------------

@dataclass
class FakeFlow:
    client_config: Dict[str, Any] = field(default_factory=dict)
    fetch_token_calls: List[Dict[str, Any]] = field(default_factory=list)
    fetch_token_error: Optional[Exception] = None
    credentials: Any = None

    def authorization_url(self, **kwargs):
        return ('http://fake-auth-url', 'state123')

    def fetch_token(self, **kwargs):
        self.fetch_token_calls.append(kwargs)
        if self.fetch_token_error:
            raise self.fetch_token_error


def test_get_auth_url_returns_url_and_stores_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    fake_flow = FakeFlow()
    captured = {}

    def fake_from_client_secrets_file(credentials_path, scopes=None, redirect_uri=None):
        captured['credentials_path'] = credentials_path
        captured['scopes'] = scopes
        captured['redirect_uri'] = redirect_uri
        return fake_flow

    monkeypatch.setattr(gmail_module.InstalledAppFlow, 'from_client_secrets_file', staticmethod(fake_from_client_secrets_file))

    url = oauth.get_auth_url()

    assert url == 'http://fake-auth-url'
    assert oauth.flow is fake_flow
    assert captured['credentials_path'] == 'creds.json'
    assert captured['redirect_uri'] == 'http://x/callback'
    assert captured['scopes'] == GmailOAuth.SCOPES


# --- get_token_from_code ------------------------------------------------------

def test_get_token_from_code_raises_without_prior_get_auth_url(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    with pytest.raises(RuntimeError):
        oauth.get_token_from_code('some-code')


def test_get_token_from_code_returns_token_dict_on_success(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)

    @dataclass
    class FakeCredentials:
        token: str = 'access-token'
        refresh_token: str = 'refresh-token'
        token_uri: str = 'https://oauth2.googleapis.com/token'
        client_id: str = 'client-id'
        client_secret: str = 'client-secret'
        scopes: List[str] = field(default_factory=lambda: ['scope1'])

    fake_flow = FakeFlow(credentials=FakeCredentials())
    oauth.flow = fake_flow

    result = oauth.get_token_from_code('auth-code')

    assert fake_flow.fetch_token_calls == [{'code': 'auth-code'}]
    assert result == {
        'token': 'access-token',
        'refresh_token': 'refresh-token',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'client-id',
        'client_secret': 'client-secret',
        'scopes': ['scope1'],
    }


def test_get_token_from_code_propagates_fetch_token_failure(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    fake_flow = FakeFlow(fetch_token_error=RuntimeError('network down'))
    oauth.flow = fake_flow

    with pytest.raises(RuntimeError, match='network down'):
        oauth.get_token_from_code('auth-code')


# --- refresh_token -------------------------------------------------------------

def test_refresh_token_raises_when_flow_never_initialized(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    assert oauth.flow is None
    with pytest.raises(AttributeError):
        oauth.refresh_token('some-refresh-token')


def test_refresh_token_returns_new_token_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    oauth.flow = FakeFlow(client_config={'installed': {'client_id': 'cid', 'client_secret': 'csecret'}})

    @dataclass
    class FakeCredentials:
        refresh_calls: List[Any] = field(default_factory=list)
        token: str = 'new-access-token'
        refresh_token: str = 'new-refresh-token'
        token_uri: str = 'https://oauth2.googleapis.com/token'
        client_id: str = 'cid'
        client_secret: str = 'csecret'
        scopes: List[str] = field(default_factory=lambda: GmailOAuth.SCOPES)

        def refresh(self, request):
            self.refresh_calls.append(request)

    fake_creds = FakeCredentials()
    monkeypatch.setattr(gmail_module, 'Credentials', lambda *a, **k: fake_creds)

    result = oauth.refresh_token('old-refresh-token')

    assert len(fake_creds.refresh_calls) == 1
    assert result['token'] == 'new-access-token'
    assert result['refresh_token'] == 'new-refresh-token'


# --- get_user_info -------------------------------------------------------------

@dataclass
class FakeGmailService:
    profile: Dict[str, Any] = field(default_factory=lambda: {'emailAddress': 'user@example.com'})

    def users(self):
        return self

    def getProfile(self, userId):
        assert userId == 'me'
        return self

    def execute(self):
        return self.profile


def test_get_user_info_from_string_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    monkeypatch.setattr(gmail_module, 'Credentials', lambda token: {'token': token})
    fake_service = FakeGmailService()
    monkeypatch.setattr(gmail_module, 'build', lambda *a, **k: fake_service)

    result = oauth.get_user_info('a-string-token')

    assert result == {'emailAddress': 'user@example.com'}


def test_get_user_info_from_dict_missing_token_raises_value_error(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    with pytest.raises(ValueError):
        oauth.get_user_info({'refresh_token': 'x'})


def test_get_user_info_from_dict_uses_token_or_access_token_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    captured = {}

    def fake_credentials(token=None, **kwargs):
        captured['token'] = token
        return object()

    monkeypatch.setattr(gmail_module, 'Credentials', fake_credentials)
    monkeypatch.setattr(gmail_module, 'build', lambda *a, **k: FakeGmailService())

    oauth.get_user_info({'access_token': 'from-access-token-key'})

    assert captured['token'] == 'from-access-token-key'


# --- get_valid_token -----------------------------------------------------------

def test_get_valid_token_returns_none_when_no_stored_token(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    assert oauth.get_valid_token('user1') is None


def test_get_valid_token_returns_none_for_wrong_provider_type(tmp_path: Path) -> None:
    oauth = _oauth(tmp_path)
    oauth.token_manager.store_token('user1', {'access_token': 'a-microsoft-shaped-token'})

    assert oauth.get_valid_token('user1') is None


def test_get_valid_token_returns_existing_token_when_still_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    stored = {
        'token': 'still-good', 'refresh_token': 'r', 'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'cid', 'client_secret': 'csecret', 'scopes': ['s'],
    }
    oauth.token_manager.store_token('user1', stored)

    @dataclass
    class FakeCredentials:
        valid: bool = True

    monkeypatch.setattr(gmail_module, 'Credentials', lambda *a, **k: FakeCredentials())

    result = oauth.get_valid_token('user1')

    assert result == stored


def test_get_valid_token_refreshes_and_stores_when_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    stored = {
        'token': 'stale', 'refresh_token': 'r', 'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'cid', 'client_secret': 'csecret', 'scopes': ['s'],
    }
    oauth.token_manager.store_token('user1', stored)

    @dataclass
    class FakeCredentials:
        valid: bool = False
        token: str = 'refreshed-token'
        refresh_token: str = 'r2'
        token_uri: str = 'https://oauth2.googleapis.com/token'
        client_id: str = 'cid'
        client_secret: str = 'csecret'
        scopes: List[str] = field(default_factory=lambda: ['s'])
        refresh_calls: List[Any] = field(default_factory=list)

        def refresh(self, request):
            self.refresh_calls.append(request)

    fake_creds = FakeCredentials()
    monkeypatch.setattr(gmail_module, 'Credentials', lambda *a, **k: fake_creds)

    result = oauth.get_valid_token('user1')

    assert len(fake_creds.refresh_calls) == 1
    assert result['token'] == 'refreshed-token'
    assert oauth.token_manager.get_token('user1')['token'] == 'refreshed-token'


def test_get_valid_token_returns_none_on_unexpected_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = _oauth(tmp_path)
    oauth.token_manager.store_token('user1', {
        'token': 't', 'refresh_token': 'r', 'token_uri': 'u', 'client_id': 'c', 'client_secret': 's', 'scopes': [],
    })

    def raise_error(*a, **k):
        raise RuntimeError("credentials construction blew up")

    monkeypatch.setattr(gmail_module, 'Credentials', raise_error)

    assert oauth.get_valid_token('user1') is None
