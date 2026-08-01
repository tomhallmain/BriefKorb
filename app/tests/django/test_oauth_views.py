"""Tests for django_app/oauth/views.py.

Two isolation hazards specific to this module, beyond the usual
config/cache/token env-var overrides already handled by the root
conftest.py's autouse isolated_app_state fixture:

1. microsoft_callback/gmail_callback compute `app_dir = _get_app_dir()` --
   a *fixed* `Path(__file__).parent.parent.parent`, not overridable via env
   var -- and write a real `.microsoft_auth_status.json` /
   `.gmail_auth_status.json` file under `<app_dir>/email_server/` on every
   code path that gets past the initial config-lookup checks, including the
   generic exception handler. Every test that reaches that point patches
   `_get_app_dir` to a tmp_path so this never touches the real repo.
2. Both callbacks construct real MSAL/Google OAuth objects directly (not
   through a mockable wrapper), and gmail_callback does so via *local*
   imports inside the function body -- `from google_auth_oauthlib.flow
   import InstalledAppFlow`, `from googleapiclient.discovery import build`,
   `from google.oauth2.credentials import Credentials` -- so those get
   patched on the real underlying modules, not on this view module's
   namespace (patching the latter wouldn't reach a local import).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from django.test import Client
from django.urls import reverse

import google.oauth2.credentials as google_credentials_module
import google_auth_oauthlib.flow as google_flow_module
from googleapiclient import discovery as googleapiclient_discovery_module

from django_app.oauth import views as oauth_views_module
from email_server.config import EmailServerConfig, ProviderConfig


# --- shared helpers ----------------------------------------------------------

def _patch_app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    app_dir = tmp_path / 'app_dir'
    (app_dir / 'email_server').mkdir(parents=True)
    monkeypatch.setattr(oauth_views_module, '_get_app_dir', lambda: app_dir)
    return app_dir


def _write_config(microsoft: ProviderConfig, gmail: ProviderConfig, token_dir: Path) -> EmailServerConfig:
    config = EmailServerConfig(microsoft=microsoft, gmail=gmail, token_storage_path=str(token_dir))
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])
    return config


def _ms_config(tmp_path: Path, **overrides: Any) -> EmailServerConfig:
    defaults: Dict[str, Any] = dict(
        enabled=True, client_id='cid', client_secret='csecret', tenant_id='tenant-id',
        redirect_uri='http://testserver/auth/microsoft/callback',
        scopes=['https://graph.microsoft.com/Mail.ReadWrite'],
    )
    defaults.update(overrides)
    return _write_config(ProviderConfig(**defaults), ProviderConfig(enabled=False), tmp_path / 'tokens')


def _gmail_config(tmp_path: Path, credentials_path: Path, **overrides: Any) -> EmailServerConfig:
    defaults: Dict[str, Any] = dict(
        enabled=True, credentials_path=str(credentials_path),
        redirect_uri='http://testserver/auth/gmail/callback',
        scopes=['https://www.googleapis.com/auth/gmail.readonly'],
    )
    defaults.update(overrides)
    return _write_config(ProviderConfig(enabled=False), ProviderConfig(**defaults), tmp_path / 'tokens')


@dataclass
class FakeMSALApp:
    init_flow_result: Dict[str, Any] = field(default_factory=lambda: {'auth_uri': 'http://fake-auth-url'})
    token_result: Dict[str, Any] = field(default_factory=dict)
    initiate_calls: List[Dict[str, Any]] = field(default_factory=list)
    by_code_calls: List[Dict[str, Any]] = field(default_factory=list)
    by_flow_calls: List[Any] = field(default_factory=list)

    def initiate_auth_code_flow(self, scopes=None, redirect_uri=None):
        self.initiate_calls.append({'scopes': scopes, 'redirect_uri': redirect_uri})
        return self.init_flow_result

    def acquire_token_by_authorization_code(self, code, scopes=None, redirect_uri=None):
        self.by_code_calls.append({'code': code, 'scopes': scopes, 'redirect_uri': redirect_uri})
        return self.token_result

    def acquire_token_by_auth_code_flow(self, flow, params):
        self.by_flow_calls.append((flow, params))
        return self.token_result


@dataclass
class FakeMSALCache:
    has_state_changed: bool = False

    def serialize(self) -> str:
        return 'serialized-cache'


def _patch_msal(monkeypatch: pytest.MonkeyPatch, app: Optional[FakeMSALApp] = None) -> FakeMSALApp:
    app = app or FakeMSALApp()
    monkeypatch.setattr(oauth_views_module.msal, 'SerializableTokenCache', lambda: FakeMSALCache())
    monkeypatch.setattr(oauth_views_module.msal, 'ConfidentialClientApplication', lambda *a, **k: app)
    return app


@dataclass
class FakeGoogleCredentials:
    token: str = 'access-token'
    refresh_token: str = 'refresh-token'
    token_uri: str = 'https://oauth2.googleapis.com/token'
    client_id: str = 'client-id'
    client_secret: str = 'client-secret'
    scopes: List[str] = field(default_factory=lambda: ['scope1'])


@dataclass
class FakeGmailFlow:
    credentials: Any = field(default_factory=FakeGoogleCredentials)
    fetch_token_calls: List[Dict[str, Any]] = field(default_factory=list)

    def fetch_token(self, **kwargs: Any) -> None:
        self.fetch_token_calls.append(kwargs)

    def authorization_url(self, **kwargs: Any):
        return ('http://fake-gmail-auth-url', 'state123')


@dataclass
class FakeGmailProfileService:
    profile: Dict[str, Any] = field(default_factory=lambda: {'emailAddress': 'user@example.com'})

    def users(self):
        return self

    def getProfile(self, userId: str):
        assert userId == 'me'
        return self

    def execute(self):
        return self.profile


def _patch_gmail_success(monkeypatch: pytest.MonkeyPatch, flow: Optional[FakeGmailFlow] = None, service: Optional[FakeGmailProfileService] = None) -> FakeGmailFlow:
    flow = flow or FakeGmailFlow()
    service = service or FakeGmailProfileService()
    monkeypatch.setattr(google_flow_module.InstalledAppFlow, 'from_client_secrets_file', staticmethod(lambda *a, **k: flow))
    monkeypatch.setattr(google_credentials_module, 'Credentials', lambda **kwargs: object())
    monkeypatch.setattr(googleapiclient_discovery_module, 'build', lambda *a, **k: service)
    return flow


# --- microsoft_callback --------------------------------------------------------

def test_microsoft_callback_returns_error_when_no_code_or_error_param(client: Client) -> None:
    response = client.get(reverse('django_app.oauth:microsoft_callback'))

    assert response.status_code == 200
    assert b'Unknown error' in response.content


def test_microsoft_callback_surfaces_oauth_error_param(client: Client) -> None:
    response = client.get(reverse('django_app.oauth:microsoft_callback'), {'error': 'access_denied'})

    assert b'access_denied' in response.content


def test_microsoft_callback_returns_error_when_config_missing(client: Client) -> None:
    response = client.get(reverse('django_app.oauth:microsoft_callback'), {'code': 'auth-code'})

    assert b'Configuration file not found' in response.content


def test_microsoft_callback_returns_error_when_microsoft_disabled(client: Client, tmp_path: Path) -> None:
    _write_config(ProviderConfig(enabled=False), ProviderConfig(enabled=False), tmp_path / 'tokens')

    response = client.get(reverse('django_app.oauth:microsoft_callback'), {'code': 'auth-code'})

    assert b'Microsoft Graph is not configured' in response.content


def test_microsoft_callback_succeeds_on_desktop_path_and_stores_token(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_app_dir(monkeypatch, tmp_path)
    _ms_config(tmp_path)
    app = _patch_msal(monkeypatch, FakeMSALApp(token_result={
        'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600,
        'id_token_claims': {'preferred_username': 'user@example.com', 'name': 'Test User'},
    }))

    response = client.get(reverse('django_app.oauth:microsoft_callback'), {'code': 'auth-code'})

    assert response.status_code == 200
    assert b'Authentication Successful' in response.content
    assert app.by_code_calls[0]['code'] == 'auth-code'

    from email_server.auth import TokenManager
    token_manager = TokenManager(storage_path=str(tmp_path / 'tokens'))
    assert token_manager.get_token('user@example.com')['access_token'] == 'at'
    assert client.session['user']['email'] == 'user@example.com'


def test_microsoft_callback_web_signin_redirects_to_home(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_app_dir(monkeypatch, tmp_path)
    _ms_config(tmp_path)
    session = client.session
    session['microsoft_auth_flow'] = {'auth_uri': 'stashed'}
    session.save()
    app = _patch_msal(monkeypatch, FakeMSALApp(token_result={
        'access_token': 'at', 'id_token_claims': {'preferred_username': 'user@example.com'},
    }))

    response = client.get(reverse('django_app.oauth:microsoft_callback'), {'code': 'auth-code'})

    assert response.status_code == 302
    assert response.url == reverse('django_app.home:home')
    assert len(app.by_flow_calls) == 1


def test_microsoft_callback_returns_error_when_msal_result_has_error(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ms_config(tmp_path)
    _patch_msal(monkeypatch, FakeMSALApp(token_result={'error': 'invalid_grant', 'error_description': 'code expired'}))

    response = client.get(reverse('django_app.oauth:microsoft_callback'), {'code': 'auth-code'})

    assert b'code expired' in response.content


def test_microsoft_callback_exception_path_writes_status_file_under_patched_app_dir(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = _patch_app_dir(monkeypatch, tmp_path)
    _ms_config(tmp_path)

    def raise_app(*a: Any, **k: Any) -> Any:
        raise RuntimeError('msal blew up')

    monkeypatch.setattr(oauth_views_module.msal, 'ConfidentialClientApplication', raise_app)

    response = client.get(reverse('django_app.oauth:microsoft_callback'), {'code': 'auth-code'})

    assert b'msal blew up' in response.content
    assert (app_dir / 'email_server' / '.microsoft_auth_status.json').exists()


# --- gmail_callback --------------------------------------------------------------

def test_gmail_callback_returns_error_when_no_code_or_error_param(client: Client) -> None:
    response = client.get(reverse('django_app.oauth:gmail_callback'))

    assert b'Unknown error' in response.content


def test_gmail_callback_returns_error_when_config_missing(client: Client) -> None:
    response = client.get(reverse('django_app.oauth:gmail_callback'), {'code': 'auth-code'})

    assert b'Configuration file not found' in response.content


def test_gmail_callback_returns_error_when_gmail_disabled(client: Client, tmp_path: Path) -> None:
    _write_config(ProviderConfig(enabled=False), ProviderConfig(enabled=False), tmp_path / 'tokens')

    response = client.get(reverse('django_app.oauth:gmail_callback'), {'code': 'auth-code'})

    assert b'Gmail is not configured' in response.content


def test_gmail_callback_returns_error_when_credentials_file_missing(client: Client, tmp_path: Path) -> None:
    _gmail_config(tmp_path, credentials_path=tmp_path / 'does-not-exist.json')

    response = client.get(reverse('django_app.oauth:gmail_callback'), {'code': 'auth-code'})

    assert b'credentials file not found' in response.content.lower()


def test_gmail_callback_succeeds_and_stores_token(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_app_dir(monkeypatch, tmp_path)
    creds_file = tmp_path / 'creds.json'
    creds_file.write_text('{}')
    _gmail_config(tmp_path, credentials_path=creds_file)
    flow = _patch_gmail_success(monkeypatch)

    response = client.get(reverse('django_app.oauth:gmail_callback'), {'code': 'auth-code'})

    assert response.status_code == 200
    assert b'Authentication Successful' in response.content
    assert flow.fetch_token_calls == [{'code': 'auth-code'}]

    from email_server.auth import TokenManager
    token_manager = TokenManager(storage_path=str(tmp_path / 'tokens'))
    assert token_manager.get_token('user@example.com')['token'] == 'access-token'
    assert client.session['user']['email'] == 'user@example.com'


def test_gmail_callback_web_signin_redirects_to_home(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_app_dir(monkeypatch, tmp_path)
    creds_file = tmp_path / 'creds.json'
    creds_file.write_text('{}')
    _gmail_config(tmp_path, credentials_path=creds_file)
    _patch_gmail_success(monkeypatch)
    session = client.session
    session['gmail_web_signin'] = True
    session.save()

    response = client.get(reverse('django_app.oauth:gmail_callback'), {'code': 'auth-code'})

    assert response.status_code == 302
    assert response.url == reverse('django_app.home:home')


def test_gmail_callback_exception_path_writes_status_file_under_patched_app_dir(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = _patch_app_dir(monkeypatch, tmp_path)
    creds_file = tmp_path / 'creds.json'
    creds_file.write_text('{}')
    _gmail_config(tmp_path, credentials_path=creds_file)

    def raise_flow(*a: Any, **k: Any) -> Any:
        raise RuntimeError('flow init blew up')

    monkeypatch.setattr(google_flow_module.InstalledAppFlow, 'from_client_secrets_file', staticmethod(raise_flow))

    response = client.get(reverse('django_app.oauth:gmail_callback'), {'code': 'auth-code'})

    assert b'flow init blew up' in response.content
    assert (app_dir / 'email_server' / '.gmail_auth_status.json').exists()


# --- sign_in_microsoft --------------------------------------------------------

def test_sign_in_microsoft_returns_error_when_config_missing(client: Client) -> None:
    response = client.get(reverse('django_app.oauth:sign_in_microsoft'))

    assert b'Configuration file not found' in response.content


def test_sign_in_microsoft_returns_error_when_disabled(client: Client, tmp_path: Path) -> None:
    _write_config(ProviderConfig(enabled=False), ProviderConfig(enabled=False), tmp_path / 'tokens')

    response = client.get(reverse('django_app.oauth:sign_in_microsoft'))

    assert b'Microsoft Graph is not configured' in response.content


def test_sign_in_microsoft_redirects_to_auth_url_and_stores_flow_in_session(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ms_config(tmp_path)
    app = _patch_msal(monkeypatch, FakeMSALApp(init_flow_result={'auth_uri': 'http://fake-auth-url'}))

    response = client.get(reverse('django_app.oauth:sign_in_microsoft'))

    assert response.status_code == 302
    assert response.url == 'http://fake-auth-url'
    assert len(app.initiate_calls) == 1
    assert client.session['microsoft_auth_flow'] == {'auth_uri': 'http://fake-auth-url'}


# --- sign_in_gmail --------------------------------------------------------------

def test_sign_in_gmail_returns_error_when_config_missing(client: Client) -> None:
    response = client.get(reverse('django_app.oauth:sign_in_gmail'))

    assert b'Configuration file not found' in response.content


def test_sign_in_gmail_returns_error_when_disabled(client: Client, tmp_path: Path) -> None:
    _write_config(ProviderConfig(enabled=False), ProviderConfig(enabled=False), tmp_path / 'tokens')

    response = client.get(reverse('django_app.oauth:sign_in_gmail'))

    assert b'Gmail is not configured' in response.content


def test_sign_in_gmail_returns_error_when_credentials_file_missing(client: Client, tmp_path: Path) -> None:
    _gmail_config(tmp_path, credentials_path=tmp_path / 'does-not-exist.json')

    response = client.get(reverse('django_app.oauth:sign_in_gmail'))

    assert b'credentials file not found' in response.content.lower()


def test_sign_in_gmail_redirects_to_auth_url_and_flags_web_signin(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    creds_file = tmp_path / 'creds.json'
    creds_file.write_text('{}')
    _gmail_config(tmp_path, credentials_path=creds_file)
    monkeypatch.setattr(google_flow_module.InstalledAppFlow, 'from_client_secrets_file', staticmethod(lambda *a, **k: FakeGmailFlow()))

    response = client.get(reverse('django_app.oauth:sign_in_gmail'))

    assert response.status_code == 302
    assert response.url == 'http://fake-gmail-auth-url'
    assert client.session['gmail_web_signin'] is True


# --- sign_out --------------------------------------------------------------

def test_sign_out_clears_session_and_redirects_home(client: Client) -> None:
    session = client.session
    session['user'] = {'is_authenticated': True, 'email': 'user@example.com'}
    session.save()

    response = client.get(reverse('django_app.oauth:sign_out'))

    assert response.status_code == 302
    assert response.url == reverse('django_app.home:home')
    assert 'user' not in client.session
