"""Tests for django_app/messages/views.py's blocked_senders_view -- lists
block-event history (via UnifiedEmailServer.get_blocked_sender_summary)
and handles unblocking a sender's local suppression. Multi-provider/
aggregation correctness itself is UnifiedEmailServer's own tested concern
(test_unified_email_server.py); these tests patch it wholesale via the
shared FakeUnifiedEmailServer and focus on the view's own job: request
handling, selection, and unblock dispatch.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from email_server.config import EmailServerConfig, ProviderConfig

from _fake_unified_email_server import FakeUnifiedEmailServer, patch_server as _patch_server


def _write_config(tmp_path: Path, microsoft_enabled: bool = True, gmail_enabled: bool = False) -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=microsoft_enabled),
        gmail=ProviderConfig(enabled=gmail_enabled),
        token_storage_path=str(tmp_path / 'tokens'),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])


def _summary(sender: str, is_locally_blocked: bool, **overrides) -> dict:
    base = {
        'sender': sender,
        'sender_kind': 'email',
        'sender_display_name': None,
        'event_count': 1,
        'latest_event': {'timestamp_utc': '2024-01-01T00:00:00+00:00', 'source': 'desktop_email_client', 'provider': None, 'message_count': None, 'message_subjects': None},
        'events': [{'timestamp_utc': '2024-01-01T00:00:00+00:00', 'source': 'desktop_email_client', 'provider': None, 'message_count': None, 'message_subjects': None}],
        'is_locally_blocked': is_locally_blocked,
    }
    base.update(overrides)
    return base


# --- error states ------------------------------------------------------------

def test_blocked_senders_view_shows_error_when_config_missing(client: Client) -> None:
    response = client.get(reverse('django_app.messages:blocked_senders'))

    assert response.status_code == 200
    assert response.context['summaries'] == []
    assert 'error' in response.context


def test_blocked_senders_view_shows_error_when_no_provider_enabled(client: Client, tmp_path: Path) -> None:
    _write_config(tmp_path, microsoft_enabled=False, gmail_enabled=False)

    response = client.get(reverse('django_app.messages:blocked_senders'))

    assert response.context['summaries'] == []
    assert 'No email provider' in response.context['error']


# --- GET listing ---------------------------------------------------------------

def test_blocked_senders_view_get_lists_summaries(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(blocked_sender_summary=[_summary('spam@example.com', True)])
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:blocked_senders'))

    assert response.status_code == 200
    assert [s['sender'] for s in response.context['summaries']] == ['spam@example.com']
    assert response.context['selected_summary'] is None


def test_blocked_senders_view_get_with_sender_param_selects_detail(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(blocked_sender_summary=[
        _summary('spam@example.com', True),
        _summary('other@example.com', False),
    ])
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:blocked_senders'), {'sender': 'spam@example.com'})

    assert response.context['selected_summary']['sender'] == 'spam@example.com'
    assert response.context['selected_sender'] == 'spam@example.com'


def test_blocked_senders_view_get_with_unknown_sender_has_no_selected_summary(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(blocked_sender_summary=[_summary('spam@example.com', True)])
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:blocked_senders'), {'sender': 'nobody@example.com'})

    assert response.context['selected_summary'] is None
    assert response.context['selected_sender'] == 'nobody@example.com'


def test_blocked_senders_view_hides_unblock_form_for_history_only_sender(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(blocked_sender_summary=[_summary('rulesonly@example.com', False)])
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:blocked_senders'), {'sender': 'rulesonly@example.com'})

    assert b'name="unblock"' not in response.content


def test_blocked_senders_view_shows_unblock_form_for_locally_blocked_sender(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(blocked_sender_summary=[_summary('spam@example.com', True)])
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:blocked_senders'), {'sender': 'spam@example.com'})

    assert b'name="unblock"' in response.content


# --- POST unblock ---------------------------------------------------------------

def test_blocked_senders_view_post_unblock_calls_server_and_redirects(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(blocked_sender_summary=[_summary('spam@example.com', True)])
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:blocked_senders'), {'unblock': 'Spam@Example.com'})

    assert fake_server.unblock_sender_calls == ['spam@example.com']
    assert response.status_code == 302
    assert response.url.endswith('?sender=spam@example.com')


def test_blocked_senders_view_post_without_unblock_field_redirects_without_selection(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer()
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:blocked_senders'), {})

    assert fake_server.unblock_sender_calls == []
    assert response.status_code == 302
    assert response.url == reverse('django_app.messages:blocked_senders')
