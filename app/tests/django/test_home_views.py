"""Tests for django_app/home/views.py's home_view.

Relies on the root conftest.py's autouse isolated_app_state fixture, which
repoints BRIEFKORB_CONFIG_PATH at a fresh, nonexistent-by-default tmp path
per test -- so the "no config file" branch is exercised for free without
touching this repo's real email_server/config.yaml. Tests exercising the
TokenManager-fallback branch write a real config.yaml to that same
(tmp-isolated) path.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.test import Client
from django.urls import reverse

from email_server.auth import TokenManager
from email_server.config import EmailServerConfig, ProviderConfig


def _write_config(tmp_path: Path, token_storage_path: Path) -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=True),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(token_storage_path),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])


def test_home_view_unauthenticated_when_no_session_and_no_config(client: Client) -> None:
    response = client.get(reverse('django_app.home:home'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is False
    assert response.context['user'] is None


def test_home_view_uses_session_user_when_present(client: Client) -> None:
    session = client.session
    session['user'] = {'is_authenticated': True, 'name': 'Session User', 'email': 'session@example.com'}
    session.save()

    response = client.get(reverse('django_app.home:home'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is True
    assert response.context['user']['email'] == 'session@example.com'


def test_home_view_ignores_session_user_missing_is_authenticated_flag(client: Client) -> None:
    session = client.session
    session['user'] = {'email': 'session@example.com'}  # no is_authenticated key
    session.save()

    response = client.get(reverse('django_app.home:home'))

    assert response.context['is_authenticated'] is False


def test_home_view_falls_back_to_token_manager_when_config_and_tokens_exist(client: Client, tmp_path: Path) -> None:
    token_dir = tmp_path / 'tokens'
    _write_config(tmp_path, token_dir)
    token_manager = TokenManager(storage_path=str(token_dir))
    token_manager.store_token('user@example.com', {'access_token': 'at'})
    token_manager.store_user_info('user@example.com', {'displayName': 'Desktop User', 'email': 'user@example.com'})

    response = client.get(reverse('django_app.home:home'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is True
    assert response.context['user']['name'] == 'Desktop User'
    assert response.context['user']['email'] == 'user@example.com'


def test_home_view_unauthenticated_when_config_exists_but_no_tokens_stored(client: Client, tmp_path: Path) -> None:
    token_dir = tmp_path / 'tokens'
    _write_config(tmp_path, token_dir)

    response = client.get(reverse('django_app.home:home'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is False
    assert response.context['user'] is None


def test_home_view_unauthenticated_when_stored_user_has_no_user_info(client: Client, tmp_path: Path) -> None:
    token_dir = tmp_path / 'tokens'
    _write_config(tmp_path, token_dir)
    token_manager = TokenManager(storage_path=str(token_dir))
    token_manager.store_token('user@example.com', {'access_token': 'at'})  # no store_user_info call

    response = client.get(reverse('django_app.home:home'))

    assert response.context['is_authenticated'] is False
