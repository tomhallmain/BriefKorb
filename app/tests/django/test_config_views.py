"""Tests for django_app/config/views.py's settings_view.

Unlike the oauth views, settings_view resolves config_path once via
EmailServerConfig.resolve_path() and reuses that same path for both reading
and (on POST) config.save() -- and resolve_path honors BRIEFKORB_CONFIG_PATH
ahead of the real app_dir-derived path. So the root conftest's autouse
isolated_app_state fixture is sufficient isolation here on its own; no
_get_app_dir-style patch is needed (unlike django_app/oauth/views.py, which
computes its status-file path from a fixed, non-overridable app_dir).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

import email_server.config as config_module
from email_server.auth import TokenManager
from email_server.config import EmailServerConfig, ExternalApiConfig, ExternalApiToken, ProviderConfig


def _write_config(tmp_path: Path, token_dir: Path) -> EmailServerConfig:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=True, client_id='cid', client_secret='csecret', tenant_id='tid', redirect_uri='http://x/ms', scopes=['ms-scope']),
        gmail=ProviderConfig(enabled=True, credentials_path='creds.json', redirect_uri='http://x/gmail', scopes=['gmail-scope']),
        token_storage_path=str(token_dir),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])
    return config


def _read_saved_config() -> EmailServerConfig:
    return EmailServerConfig.from_file(os.environ['BRIEFKORB_CONFIG_PATH'])


# --- GET --------------------------------------------------------------------

def test_settings_view_get_with_no_config_shows_defaults(client: Client) -> None:
    response = client.get(reverse('django_app.config:settings'))

    assert response.status_code == 200
    assert response.context['config'].microsoft.enabled is False
    assert response.context['config'].gmail.enabled is False
    assert response.context['config'].max_messages == 200
    assert response.context['ms_current_scopes'] == set()
    assert response.context['ms_auth_user'] is None
    assert response.context['gmail_auth_user'] is None
    assert isinstance(response.context['ms_available_scopes'], list)


def test_settings_view_get_with_existing_config_and_tokens(client: Client, tmp_path: Path) -> None:
    token_dir = tmp_path / 'tokens'
    _write_config(tmp_path, token_dir)
    token_manager = TokenManager(storage_path=str(token_dir))
    token_manager.store_token('ms-user@example.com', {'access_token': 'at'})
    token_manager.store_token('gmail-user@example.com', {'token': 'gt', 'token_uri': 'https://oauth2.googleapis.com/token'})

    response = client.get(reverse('django_app.config:settings'))

    assert response.context['config'].microsoft.enabled is True
    assert response.context['ms_current_scopes'] == {'ms-scope'}
    assert response.context['gmail_current_scopes'] == {'gmail-scope'}
    assert response.context['ms_auth_user'] == 'ms-user@example.com'
    assert response.context['gmail_auth_user'] == 'gmail-user@example.com'


# --- POST --------------------------------------------------------------------

def test_settings_view_post_saves_config_and_redirects(client: Client, tmp_path: Path) -> None:
    token_dir = tmp_path / 'tokens'

    response = client.post(reverse('django_app.config:settings'), {
        'ms_enabled': 'on',
        'ms_client_id': 'new-client-id',
        'ms_client_secret': 'new-secret',
        'ms_tenant_id': 'new-tenant',
        'ms_redirect_uri': 'http://x/ms-callback',
        'ms_scopes': ['ms-scope-a', 'ms-scope-b'],
        'gmail_credentials_path': 'gmail-creds.json',
        'gmail_redirect_uri': 'http://x/gmail-callback',
        'gmail_scopes': ['gmail-scope-a'],
        'log_level': 'DEBUG',
        'token_storage_path': str(token_dir),
    }, follow=True)

    assert response.status_code == 200
    assert response.redirect_chain == [(reverse('django_app.config:settings'), 302)]
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Settings saved successfully' in m for m in messages_shown)

    saved = _read_saved_config()
    assert saved.microsoft.enabled is True
    assert saved.microsoft.client_id == 'new-client-id'
    assert set(saved.microsoft.scopes) == {'ms-scope-a', 'ms-scope-b'}
    assert saved.gmail.enabled is False  # 'gmail_enabled' checkbox was not included
    assert saved.log_level == 'debug'


def test_settings_view_post_gmail_enabled_checkbox_persists(client: Client, tmp_path: Path) -> None:
    client.post(reverse('django_app.config:settings'), {
        'gmail_enabled': 'on',
        'gmail_credentials_path': 'gmail-creds.json',
        'gmail_redirect_uri': 'http://x/gmail-callback',
        'token_storage_path': str(tmp_path / 'tokens'),
    })

    saved = _read_saved_config()
    assert saved.gmail.enabled is True
    assert saved.gmail.credentials_path == 'gmail-creds.json'


def test_settings_view_post_invalid_log_level_falls_back_to_info(client: Client, tmp_path: Path) -> None:
    client.post(reverse('django_app.config:settings'), {
        'log_level': 'NOT_A_LEVEL',
        'token_storage_path': str(tmp_path / 'tokens'),
    })

    saved = _read_saved_config()
    assert saved.log_level == 'info'


def test_settings_view_post_saves_max_messages(client: Client, tmp_path: Path) -> None:
    client.post(reverse('django_app.config:settings'), {
        'max_messages': '350',
        'token_storage_path': str(tmp_path / 'tokens'),
    })

    saved = _read_saved_config()
    assert saved.max_messages == 350


def test_settings_view_post_invalid_max_messages_keeps_existing_value(client: Client, tmp_path: Path) -> None:
    client.post(reverse('django_app.config:settings'), {
        'max_messages': 'not-a-number',
        'token_storage_path': str(tmp_path / 'tokens'),
    })

    saved = _read_saved_config()
    assert saved.max_messages == 200  # no config.yaml existed yet, so this is the dataclass default


def test_settings_view_post_max_messages_clamped_to_minimum_of_one(client: Client, tmp_path: Path) -> None:
    client.post(reverse('django_app.config:settings'), {
        'max_messages': '-5',
        'token_storage_path': str(tmp_path / 'tokens'),
    })

    saved = _read_saved_config()
    assert saved.max_messages == 1


def test_settings_view_post_reports_error_when_save_fails(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_save(self: EmailServerConfig, config_path: str) -> None:
        raise RuntimeError('disk full')

    monkeypatch.setattr(config_module.EmailServerConfig, 'save', raise_save)

    response = client.post(reverse('django_app.config:settings'), {
        'token_storage_path': str(tmp_path / 'tokens'),
    }, follow=True)

    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Failed to save settings' in m and 'disk full' in m for m in messages_shown)


def test_settings_view_get_lists_api_tokens_masked_not_full(client: Client, tmp_path: Path) -> None:
    EmailServerConfig(
        microsoft=ProviderConfig(enabled=False),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(tmp_path / 'tokens'),
        external_api=ExternalApiConfig(
            enabled=True,
            tokens=[ExternalApiToken(token='abcdefghijklmnop', label='tagesform')],
        ),
    ).save(os.environ['BRIEFKORB_CONFIG_PATH'])

    response = client.get(reverse('django_app.config:settings'))

    assert response.status_code == 200
    assert response.context['config'].external_api.enabled is True
    assert response.context['api_tokens'] == [
        {'index': 0, 'label': 'tagesform', 'masked': 'abcdef...mnop'},
    ]
    assert b'abcdefghijklmnop' not in response.content
    assert b'abcdef...mnop' in response.content


def test_settings_view_post_generate_api_token_enables_api_and_shows_secret_once(
    client: Client, tmp_path: Path,
) -> None:
    response = client.post(reverse('django_app.config:settings'), {
        'generate_api_token': '1',
        'api_token_label': 'tagesform',
        'token_storage_path': str(tmp_path / 'tokens'),
    }, follow=True)

    assert response.status_code == 200
    saved = _read_saved_config()
    assert saved.external_api.enabled is True
    assert len(saved.external_api.tokens) == 1
    assert saved.external_api.tokens[0].label == 'tagesform'
    plaintext = saved.external_api.tokens[0].token
    assert response.context['newly_generated_api_token'] == plaintext
    assert plaintext.encode() in response.content
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Token generated' in m for m in messages_shown)

    second = client.get(reverse('django_app.config:settings'))
    assert second.context['newly_generated_api_token'] is None
    assert plaintext.encode() not in second.content


def test_settings_view_post_save_preserves_api_tokens_and_enabled_checkbox(
    client: Client, tmp_path: Path,
) -> None:
    EmailServerConfig(
        microsoft=ProviderConfig(enabled=False),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(tmp_path / 'tokens'),
        external_api=ExternalApiConfig(
            enabled=False,
            tokens=[ExternalApiToken(token='keep-this-token', label='existing')],
        ),
    ).save(os.environ['BRIEFKORB_CONFIG_PATH'])

    client.post(reverse('django_app.config:settings'), {
        'save_settings': '1',
        'external_api_enabled': 'on',
        'token_storage_path': str(tmp_path / 'tokens'),
        'max_messages': '200',
    })

    saved = _read_saved_config()
    assert saved.external_api.enabled is True
    assert [t.token for t in saved.external_api.tokens] == ['keep-this-token']


def test_settings_view_post_revoke_api_token(client: Client, tmp_path: Path) -> None:
    EmailServerConfig(
        microsoft=ProviderConfig(enabled=False),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(tmp_path / 'tokens'),
        external_api=ExternalApiConfig(
            enabled=True,
            tokens=[
                ExternalApiToken(token='token-one', label='one'),
                ExternalApiToken(token='token-two', label='two'),
            ],
        ),
    ).save(os.environ['BRIEFKORB_CONFIG_PATH'])

    response = client.post(reverse('django_app.config:settings'), {
        'revoke_api_token': '0',
        'external_api_enabled': 'on',
        'token_storage_path': str(tmp_path / 'tokens'),
    }, follow=True)

    saved = _read_saved_config()
    assert [t.label for t in saved.external_api.tokens] == ['two']
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Token revoked' in m for m in messages_shown)


def test_settings_view_post_revoke_invalid_index_does_not_save(
    client: Client, tmp_path: Path,
) -> None:
    EmailServerConfig(
        microsoft=ProviderConfig(enabled=False),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(tmp_path / 'tokens'),
        external_api=ExternalApiConfig(
            enabled=True,
            tokens=[ExternalApiToken(token='token-one', label='one')],
        ),
    ).save(os.environ['BRIEFKORB_CONFIG_PATH'])

    response = client.post(reverse('django_app.config:settings'), {
        'revoke_api_token': 'not-an-index',
        'token_storage_path': str(tmp_path / 'tokens'),
    }, follow=True)

    saved = _read_saved_config()
    assert [t.token for t in saved.external_api.tokens] == ['token-one']
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Could not revoke' in m for m in messages_shown)
