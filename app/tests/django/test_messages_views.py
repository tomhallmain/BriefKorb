"""Tests for django_app/messages/views.py's messages_view and
messages_api_view -- both built on UnifiedEmailServer as of the
MessagesService migration (see services.py's module docstring for why
MessagesService itself is gone). Multi-provider correctness (aggregation,
dispatch, auth resolution) is UnifiedEmailServer's own tested concern
(test_unified_email_server.py); these tests patch it wholesale via the
shared `FakeUnifiedEmailServer` and focus on the views' own jobs: request
parsing, provider-aware sender-selection resolution, action dispatch, and
response/context shape.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from django.test import Client
from django.urls import reverse

from django_app.messages import views as messages_views_module
from email_client.utils.sender_categorization import ImpactInference, ImpactLevel
from email_server.config import EmailServerConfig, ExternalApiConfig, ExternalApiToken, ProviderConfig

from _fake_unified_email_server import FakeAuthenticatedProvider, FakeUnifiedEmailServer, patch_server as _patch_server


def _write_config(
    tmp_path: Path, microsoft_enabled: bool = True, gmail_enabled: bool = False,
    max_messages: int = 200,
) -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=microsoft_enabled),
        gmail=ProviderConfig(enabled=gmail_enabled),
        token_storage_path=str(tmp_path / 'tokens'),
        max_messages=max_messages,
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])


@dataclass
class FakeSenderCategorizationManager:
    """Double for SenderCategorizationManager -- the real one is backed by
    AppInfoCache, which touches the OS keyring (see test_app_info_cache.py).
    Both messages_view and messages_api_view construct one directly (no
    Microsoft-gated MessagesService involved anymore), so it must be
    patched out here."""
    impacts: Dict[str, str] = field(default_factory=dict)
    exceptions: Dict[str, str] = field(default_factory=dict)

    def infer_for_sender(self, sender_email: str, subjects: List[str]) -> ImpactInference:
        return ImpactInference(
            impact=ImpactLevel(self.impacts.get(sender_email, ImpactLevel.UNCLASSIFIED.value)),
            reason='fake', confidence=0.5,
            generic_inference_score=0.1, blocklist_inference_score=0.2, bot_spam_inference_score=0.3,
        )

    def set_inferred_sender_impact(self, sender_email: str, inference: ImpactInference) -> None:
        self.impacts[sender_email] = inference.impact.value

    def get_sender_impact(self, sender_email: str) -> ImpactLevel:
        return ImpactLevel(self.impacts.get(sender_email, ImpactLevel.UNCLASSIFIED.value))

    def has_sender_exception(self, sender_email: str) -> bool:
        return sender_email in self.exceptions

    def set_sender_exception(self, sender_email: str, impact: ImpactLevel, source: str = 'manual') -> None:
        self.exceptions[sender_email] = impact

    def clear_sender_exception(self, sender_email: str) -> None:
        self.exceptions.pop(sender_email, None)


def _patch_sender_categorization(monkeypatch: pytest.MonkeyPatch, fake: Optional[FakeSenderCategorizationManager] = None) -> FakeSenderCategorizationManager:
    fake = fake or FakeSenderCategorizationManager()
    monkeypatch.setattr(messages_views_module, 'SenderCategorizationManager', lambda storage_path: fake)
    return fake


def _bucket(provider: str, from_name: str, from_address: str, message_ids: List[str], **overrides: Any) -> Dict[str, Any]:
    """A digest bucket shaped like UnifiedEmailServer.get_message_digest()'s
    output, with just enough to drive messages_view's display and its
    action-resolution lookup (bucket['messages'] -> ids)."""
    bucket = {
        'fromName': from_name, 'fromAddress': from_address, 'provider': provider,
        'subject': 'Hi', 'lastReceivedDateTime': '2024-01-01T00:00:00+00:00',
        'count': len(message_ids),
        'messages': [{'id': mid, 'subject': 'Hi', 'lastReceivedDateTime': '', 'isRead': False} for mid in message_ids],
    }
    bucket.update(overrides)
    return bucket


# --- messages_view: unauthenticated / error states ----------------------------

def test_messages_view_shows_unauthenticated_state_when_config_missing(client: Client) -> None:
    response = client.get(reverse('django_app.messages:messages'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is False
    assert response.context['messageData'] == []


def test_messages_view_shows_unauthenticated_state_when_no_authenticated_provider(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_server(monkeypatch, FakeUnifiedEmailServer(authenticated_providers=[]))

    response = client.get(reverse('django_app.messages:messages'))

    assert response.context['is_authenticated'] is False


def test_messages_view_falls_back_to_error_state_when_fetch_raises(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        raise_on_fetch=RuntimeError('graph api down'),
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:messages'))

    assert response.context['is_authenticated'] is False
    assert 'graph api down' in response.context['error']


# --- messages_view: GET listing ------------------------------------------------

def test_messages_view_get_lists_messages_with_default_mailbox(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        messages=['m1', 'm2'],
        digest=[_bucket('microsoft', 'Alice', 'a@example.com', ['m1', 'm2'])],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:messages'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is True
    assert response.context['mailbox'] == 'inbox'
    assert response.context['exclude_read_messages'] is True
    assert response.context['messages_length'] == 2
    assert fake_server.get_user_messages_calls[0]['folder'] == 'inbox'
    assert fake_server.get_user_messages_calls[0]['unread_only'] is True
    assert response.context['messageData'][0]['fromName'] == 'Alice'
    assert response.context['messageData'][0]['provider'] == 'microsoft'


def test_messages_view_fetches_with_configured_max_messages(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path, max_messages=77)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    client.get(reverse('django_app.messages:messages'))

    assert fake_server.get_user_messages_calls[0]['max_messages'] == 77


def test_messages_view_high_impact_only_filters_message_data(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_categorization = _patch_sender_categorization(monkeypatch)
    fake_categorization.impacts = {'a@example.com': ImpactLevel.HIGH_IMPACT.value, 'b@example.com': ImpactLevel.LOW_IMPACT.value}
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[
            _bucket('microsoft', 'A', 'a@example.com', ['m1']),
            _bucket('microsoft', 'B', 'b@example.com', ['m2']),
        ],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {'highImpactOnly': 'on'})

    assert len(response.context['messageData']) == 1
    assert response.context['messageData'][0]['fromAddress'] == 'a@example.com'


def test_messages_view_default_route_excludes_low_impact_senders(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_categorization = _patch_sender_categorization(monkeypatch)
    fake_categorization.impacts = {
        'a@example.com': ImpactLevel.HIGH_IMPACT.value,
        'b@example.com': ImpactLevel.LOW_IMPACT.value,
        'c@example.com': ImpactLevel.UNCLASSIFIED.value,
    }
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[
            _bucket('microsoft', 'A', 'a@example.com', ['m1']),
            _bucket('microsoft', 'B', 'b@example.com', ['m2']),
            _bucket('microsoft', 'C', 'c@example.com', ['m3']),
        ],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:messages'))

    addresses = [b['fromAddress'] for b in response.context['messageData']]
    assert 'b@example.com' not in addresses
    assert 'a@example.com' in addresses
    assert 'c@example.com' in addresses  # unclassified is unaffected
    assert response.context['low_impact_only'] is False


def test_low_impact_senders_route_shows_only_low_impact_senders(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_categorization = _patch_sender_categorization(monkeypatch)
    fake_categorization.impacts = {
        'a@example.com': ImpactLevel.HIGH_IMPACT.value,
        'b@example.com': ImpactLevel.LOW_IMPACT.value,
    }
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[
            _bucket('microsoft', 'A', 'a@example.com', ['m1']),
            _bucket('microsoft', 'B', 'b@example.com', ['m2']),
        ],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:low_impact_senders'))

    assert [b['fromAddress'] for b in response.context['messageData']] == ['b@example.com']
    assert response.context['low_impact_only'] is True


def test_low_impact_senders_route_reuses_messages_view_actions(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The low-impact route is the same view+template as `messages` (see
    urls.py's extra-kwargs wiring) -- setImpact (the existing per-row
    "Treat as High-Impact" button) should work identically there, with no
    separate action-handling code needed."""
    _write_config(tmp_path)
    fake_categorization = _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    response = client.post(
        reverse('django_app.messages:low_impact_senders'),
        {'setImpact': 'a@example.com|high-impact'},
    )

    assert fake_categorization.exceptions == {'a@example.com': ImpactLevel.HIGH_IMPACT}
    assert response.context['low_impact_only'] is True


def test_messages_view_oldest_first_sorts_message_data_ascending(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[
            _bucket('microsoft', 'Newest', 'newest@example.com', ['m1'], lastReceivedDateTime='2024-03-01T00:00:00+00:00'),
            _bucket('microsoft', 'Oldest', 'oldest@example.com', ['m2'], lastReceivedDateTime='2024-01-01T00:00:00+00:00'),
            _bucket('microsoft', 'Middle', 'middle@example.com', ['m3'], lastReceivedDateTime='2024-02-01T00:00:00+00:00'),
        ],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {'oldestFirst': 'on'})

    assert [b['fromAddress'] for b in response.context['messageData']] == [
        'oldest@example.com', 'middle@example.com', 'newest@example.com',
    ]
    assert response.context['oldest_first'] is True


def test_messages_view_without_oldest_first_key_leaves_digest_order_unchanged(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[
            _bucket('microsoft', 'Newest', 'newest@example.com', ['m1'], lastReceivedDateTime='2024-03-01T00:00:00+00:00'),
            _bucket('microsoft', 'Oldest', 'oldest@example.com', ['m2'], lastReceivedDateTime='2024-01-01T00:00:00+00:00'),
        ],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {})

    # No oldestFirst key at all -- distinct from oldestFirst being present
    # but unchecked -- must leave get_message_digest()'s own order (by
    # count, not date) untouched, matching this view's pre-existing default.
    assert [b['fromAddress'] for b in response.context['messageData']] == [
        'newest@example.com', 'oldest@example.com',
    ]
    assert response.context['oldest_first'] is False


# --- messages_view: POST mailbox / exclude-read toggles -----------------------

def test_messages_view_post_mailbox_selection_changes_mailbox(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {'mailbox': 'archive'})

    assert response.context['mailbox'] == 'archive'
    assert fake_server.get_user_messages_calls[-1]['folder'] == 'archive'


def test_messages_view_post_without_exclude_read_key_defaults_true(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {})

    assert response.context['exclude_read_messages'] is True


# --- messages_view: POST sender impact overrides -------------------------------

def test_messages_view_post_set_impact_updates_sender_and_reports_success(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_categorization = _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {'setImpact': 'a@example.com|high-impact'})

    assert fake_categorization.exceptions == {'a@example.com': ImpactLevel.HIGH_IMPACT}
    assert response.context['has_performed_update'] is True
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Updated sender impact' in m for m in messages_shown)


def test_messages_view_post_set_impact_malformed_reports_error(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_categorization = _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {'setImpact': 'no-pipe-here'})

    assert fake_categorization.exceptions == {}
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Invalid sender impact update request' in m for m in messages_shown)


def test_messages_view_post_clear_impact(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_categorization = _patch_sender_categorization(monkeypatch, FakeSenderCategorizationManager(exceptions={'a@example.com': ImpactLevel.HIGH_IMPACT}))
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {'clearImpact': 'a@example.com'})

    assert fake_categorization.exceptions == {}
    assert response.context['has_performed_update'] is True


# --- messages_view: POST single-sender context menu actions -------------------

def test_messages_view_post_context_mark_as_read(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[_bucket('microsoft', 'Alice', 'a@example.com', ['m1'])],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {
        'context_sender': 'microsoft|Alice', 'context_action': 'markAsRead',
    })

    assert fake_server.mark_messages_as_read_calls == [{'user_id': 'user1', 'provider_name': 'microsoft', 'message_ids': ['m1']}]
    assert response.context['has_performed_update'] is True


def test_messages_view_post_context_delete_message(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[_bucket('microsoft', 'Alice', 'a@example.com', ['m1'])],
    )
    _patch_server(monkeypatch, fake_server)

    client.post(reverse('django_app.messages:messages'), {
        'context_sender': 'microsoft|Alice', 'context_action': 'deleteMessage',
    })

    assert fake_server.delete_user_messages_calls == [{'user_id': 'user1', 'provider_name': 'microsoft', 'message_ids': ['m1']}]


def test_messages_view_post_context_delete_and_block_reports_warning_when_durable_rule_fails(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers deleteMessageBlockSender when block_senders() reports False
    for the durable-rule result (quota, API error, etc.) -- delete still
    succeeds, and (per UnifiedEmailServer.block_senders()) the sender is
    still locally suppressed even though False is returned here; that
    part is exercised in test_unified_email_server.py, not through this
    fake."""
    _write_config(tmp_path, gmail_enabled=True)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('gmail', 'user1')],
        digest=[_bucket('gmail', 'Alice', 'a@example.com', ['m1'])],
        block_result=False,
    )
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {
        'context_sender': 'gmail|Alice', 'context_action': 'deleteMessageBlockSender',
    })

    assert fake_server.delete_user_messages_calls == [{'user_id': 'user1', 'provider_name': 'gmail', 'message_ids': ['m1']}]
    assert fake_server.block_senders_calls == [{
        'user_id': 'user1', 'provider_name': 'gmail', 'sender_names': ['a@example.com'], 'source': 'django_web_messages',
        'sender_details': {'a@example.com': {'display_name': 'Alice', 'subjects': ['Hi'], 'message_count': 1}},
    }]
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('failed to create some block rules' in m for m in messages_shown)


# --- messages_view: POST bulk selected-sender actions --------------------------

def test_messages_view_post_bulk_mark_as_read(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[
            _bucket('microsoft', 'Alice', 'a@example.com', ['m1']),
            _bucket('microsoft', 'Bob', 'b@example.com', ['m2']),
        ],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {
        'selected_options': ['microsoft|Alice', 'microsoft|Bob'], 'markAsRead': '1',
    })

    assert fake_server.mark_messages_as_read_calls == [{'user_id': 'user1', 'provider_name': 'microsoft', 'message_ids': ['m1', 'm2']}]
    assert response.context['has_performed_update'] is True


def test_messages_view_post_bulk_spanning_two_providers_dispatches_each_separately(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path, gmail_enabled=True)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[
            FakeAuthenticatedProvider('microsoft', 'ms-user'),
            FakeAuthenticatedProvider('gmail', 'gmail-user'),
        ],
        digest=[
            _bucket('microsoft', 'Alice', 'a@example.com', ['m1']),
            _bucket('gmail', 'Bob', 'b@example.com', ['m2']),
        ],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {
        'selected_options': ['microsoft|Alice', 'gmail|Bob'], 'deleteMessage': '1',
    })

    calls = {c['provider_name']: c for c in fake_server.delete_user_messages_calls}
    assert calls['microsoft'] == {'user_id': 'ms-user', 'provider_name': 'microsoft', 'message_ids': ['m1']}
    assert calls['gmail'] == {'user_id': 'gmail-user', 'provider_name': 'gmail', 'message_ids': ['m2']}
    assert response.context['has_performed_update'] is True


def test_messages_view_post_no_matching_senders_reports_error(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')], digest=[])
    _patch_server(monkeypatch, fake_server)

    response = client.post(reverse('django_app.messages:messages'), {
        'selected_options': ['microsoft|Ghost'], 'markAsRead': '1',
    })

    assert fake_server.mark_messages_as_read_calls == []
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('No matching messages found' in m for m in messages_shown)


def test_messages_view_context_sender_takes_precedence_over_selected_options(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[_bucket('microsoft', 'Context', 'context@example.com', ['m1'])],
    )
    _patch_server(monkeypatch, fake_server)

    client.post(reverse('django_app.messages:messages'), {
        'context_sender': 'microsoft|Context', 'context_action': 'markAsRead',
        'selected_options': ['microsoft|Bulk'], 'markAsRead': '1',
    })

    assert fake_server.mark_messages_as_read_calls == [{'user_id': 'user1', 'provider_name': 'microsoft', 'message_ids': ['m1']}]


# --- messages_api_view (GET /api/messages, external bearer-token auth) -------
#
# Auth (require_external_api_token) has its own dedicated coverage in
# test_authentication.py.

def _write_external_api_config(
    tmp_path: Path, token: str = 'good-token', enabled: bool = True, provider_enabled: bool = True,
    max_messages: int = 200,
) -> Path:
    token_dir = tmp_path / 'tokens'
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=provider_enabled),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(token_dir),
        max_messages=max_messages,
        external_api=ExternalApiConfig(enabled=enabled, tokens=[ExternalApiToken(token=token, label='tagesform')]),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])
    return token_dir


def _auth_header(token: str = 'good-token') -> Dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


def test_messages_api_view_returns_401_when_unauthorized(client: Client) -> None:
    response = client.get(reverse('django_app.messages:messages_api'))

    assert response.status_code == 401
    assert response.json() == {'error': 'Unauthorized'}


def test_messages_api_view_returns_405_for_post(client: Client, tmp_path: Path) -> None:
    _write_external_api_config(tmp_path)
    response = client.post(reverse('django_app.messages:messages_api'), **_auth_header())

    assert response.status_code == 405


def test_messages_api_view_returns_401_when_config_missing_entirely(client: Client) -> None:
    """With no config.yaml at all, require_external_api_token's own config
    load (external_api defaults to disabled/empty when the file is absent)
    already rejects the request with 401 before the view body -- and its
    own config check -- ever runs, since both read the same
    (env-var-resolved) config path."""
    response = client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert response.status_code == 401


def test_messages_api_view_returns_503_when_no_provider_configured(client: Client, tmp_path: Path) -> None:
    _write_external_api_config(tmp_path, provider_enabled=False)

    response = client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert response.status_code == 503
    assert 'error' in response.json()


def test_messages_api_view_returns_503_when_no_mailbox_user_configured(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    _patch_server(monkeypatch, FakeUnifiedEmailServer(authenticated_providers=[]))

    response = client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert response.status_code == 503
    assert 'error' in response.json()


def test_messages_api_view_returns_aggregated_messages_as_json(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[{'fromName': 'Alice', 'fromAddress': 'a@example.com', 'count': 1, 'lastReceivedDateTime': '2024-01-01T00:00:00Z', 'provider': 'microsoft'}],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert response.status_code == 200
    messages = response.json()['messages']
    assert len(messages) == 1
    assert messages[0]['fromName'] == 'Alice'
    assert messages[0]['provider'] == 'microsoft'
    assert messages[0]['impact'] == 'unclassified'  # annotated by the (faked) SenderCategorizationManager


def test_messages_api_view_passes_query_params_to_digest(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    client.get(reverse('django_app.messages:messages_api') + '?mailbox=archive&unread_only=false', **_auth_header())

    assert fake_server.get_message_digest_calls == [{
        'messages': None, 'folder': 'archive', 'unread_only': False, 'max_messages': 200,
        'sender_search': None, 'subject_keyword': None,
        'include_response_status': False, 'stale_after_days': 3.0,
        'awaiting_your_reply_only': False, 'awaiting_their_reply_only': False,
    }]


def test_messages_api_view_uses_configured_max_messages(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path, max_messages=88)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert fake_server.get_message_digest_calls[0]['max_messages'] == 88


def test_messages_api_view_passes_new_filter_params_to_digest(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    client.get(
        reverse('django_app.messages:messages_api')
        + '?senderSearch=alice&subjectKeyword=invoice&staleAfterDays=5&awaitingYourReply=true&awaitingTheirReply=true',
        **_auth_header(),
    )

    call = fake_server.get_message_digest_calls[0]
    assert call['sender_search'] == 'alice'
    assert call['subject_keyword'] == 'invoice'
    assert call['stale_after_days'] == 5.0
    assert call['awaiting_your_reply_only'] is True
    assert call['awaiting_their_reply_only'] is True


def test_messages_api_view_include_response_status_param(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    client.get(reverse('django_app.messages:messages_api') + '?includeResponseStatus=true', **_auth_header())

    assert fake_server.get_message_digest_calls[0]['include_response_status'] is True


def test_messages_api_view_unread_only_defaults_true(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert fake_server.get_message_digest_calls[0]['unread_only'] is True


def test_messages_api_view_high_impact_only_filters_response(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    fake_categorization = _patch_sender_categorization(monkeypatch)
    fake_categorization.impacts = {'a@example.com': ImpactLevel.HIGH_IMPACT.value, 'b@example.com': ImpactLevel.LOW_IMPACT.value}
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        digest=[
            {'fromName': 'A', 'fromAddress': 'a@example.com', 'count': 1, 'provider': 'microsoft'},
            {'fromName': 'B', 'fromAddress': 'b@example.com', 'count': 1, 'provider': 'microsoft'},
        ],
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:messages_api') + '?high_impact_only=true', **_auth_header())

    assert [m['fromAddress'] for m in response.json()['messages']] == ['a@example.com']


def test_messages_api_view_returns_502_when_digest_raises(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        raise_on_digest=RuntimeError('graph api down'),
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert response.status_code == 502
    assert 'graph api down' in response.json()['error']
