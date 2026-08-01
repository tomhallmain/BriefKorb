"""Tests for django_app/messages/views.py's messages_view.

MessagesService (imported into this view module from .services) wraps live
Microsoft Graph API calls and the SenderCategorizationManager cache, so
every test patches `messages_views_module.MessagesService` with an
in-memory fake instead of letting the view construct a real one --
MessagesService itself already has its own unit coverage
(test_messages_services.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from django.test import Client
from django.urls import reverse

from django_app.messages import views as messages_views_module
from email_client.utils.sender_categorization import ImpactInference, ImpactLevel
from email_server.config import EmailServerConfig, ExternalApiConfig, ExternalApiToken, ProviderConfig


def _authenticate_via_session(client: Client, email: str = 'user@example.com') -> None:
    session = client.session
    session['user'] = {'is_authenticated': True, 'email': email}
    session.save()


@dataclass
class FakeMessagesService:
    user_info: Dict[str, Any] = field(default_factory=lambda: {'mailboxSettings': {'timeZone': 'UTC'}})
    messages: List[Dict[str, Any]] = field(default_factory=list)
    message_data: List[Dict[str, Any]] = field(default_factory=list)
    mark_read_result: bool = True
    delete_result: bool = True
    block_result: bool = True
    raise_on_get_messages: Optional[Exception] = None
    constructed_with: List[str] = field(default_factory=list)
    get_messages_calls: List[Dict[str, Any]] = field(default_factory=list)
    set_impact_calls: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    mark_read_calls: List[Tuple[List[str], str]] = field(default_factory=list)
    delete_calls: List[Tuple[List[str], str]] = field(default_factory=list)
    block_calls: List[List[str]] = field(default_factory=list)

    def get_user_info(self) -> Dict[str, Any]:
        return self.user_info

    def get_messages(self, mailbox: str, exclude_read: bool, max_messages: int, timezone: str) -> List[Dict[str, Any]]:
        if self.raise_on_get_messages:
            raise self.raise_on_get_messages
        self.get_messages_calls.append({
            'mailbox': mailbox, 'exclude_read': exclude_read,
            'max_messages': max_messages, 'timezone': timezone,
        })
        return self.messages

    def aggregate_messages_by_sender(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.message_data

    def annotate_sender_impact(self, message_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return message_data

    def set_sender_impact_exception(self, sender_address: str, impact: Optional[str]) -> None:
        self.set_impact_calls.append((sender_address, impact))

    def mark_messages_as_read(self, sender_names: List[str], mailbox: str = 'inbox') -> bool:
        self.mark_read_calls.append((list(sender_names), mailbox))
        return self.mark_read_result

    def delete_messages(self, sender_names: List[str], mailbox: str = 'inbox') -> bool:
        self.delete_calls.append((list(sender_names), mailbox))
        return self.delete_result

    def block_senders(self, sender_names: List[str]) -> bool:
        self.block_calls.append(list(sender_names))
        return self.block_result


def _patch_service(monkeypatch: pytest.MonkeyPatch, fake: FakeMessagesService) -> None:
    def factory(user_id: str) -> FakeMessagesService:
        fake.constructed_with.append(user_id)
        return fake

    monkeypatch.setattr(messages_views_module, 'MessagesService', factory)


# --- unauthenticated / error states ------------------------------------------

def test_messages_view_shows_unauthenticated_state_with_no_session(client: Client) -> None:
    response = client.get(reverse('django_app.messages:messages'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is False
    assert response.context['messageData'] == []


def test_messages_view_falls_back_to_error_state_when_service_raises(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService(raise_on_get_messages=RuntimeError('graph api down'))
    _patch_service(monkeypatch, fake)

    response = client.get(reverse('django_app.messages:messages'))

    assert response.context['is_authenticated'] is False
    assert 'graph api down' in response.context['error']


# --- GET listing --------------------------------------------------------------

def test_messages_view_get_lists_messages_with_default_mailbox(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService(
        messages=[{'id': 'm1'}, {'id': 'm2'}],
        message_data=[{'fromAddress': 'a@example.com', 'impact': ImpactLevel.LOW_IMPACT.value}],
    )
    _patch_service(monkeypatch, fake)

    response = client.get(reverse('django_app.messages:messages'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is True
    assert response.context['mailbox'] == 'inbox'
    assert response.context['exclude_read_messages'] is True
    assert response.context['messages_length'] == 2
    assert fake.get_messages_calls[0]['mailbox'] == 'inbox'
    assert fake.get_messages_calls[0]['exclude_read'] is True


def test_messages_view_high_impact_only_filters_message_data(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService(message_data=[
        {'fromAddress': 'a@example.com', 'impact': ImpactLevel.HIGH_IMPACT.value},
        {'fromAddress': 'b@example.com', 'impact': ImpactLevel.LOW_IMPACT.value},
    ])
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {'highImpactOnly': 'on'})

    assert len(response.context['messageData']) == 1
    assert response.context['messageData'][0]['fromAddress'] == 'a@example.com'


# --- POST: mailbox / exclude-read toggles -------------------------------------

def test_messages_view_post_mailbox_selection_changes_mailbox(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {'mailbox': 'archive'})

    assert response.context['mailbox'] == 'archive'
    assert fake.get_messages_calls[0]['mailbox'] == 'archive'


def test_messages_view_post_without_exclude_read_key_defaults_true(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {})

    assert response.context['exclude_read_messages'] is True


# --- POST: sender impact overrides --------------------------------------------

def test_messages_view_post_set_impact_updates_sender_and_reports_success(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {'setImpact': 'a@example.com|high-impact'})

    assert fake.set_impact_calls == [('a@example.com', 'high-impact')]
    assert response.context['has_performed_update'] is True
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Updated sender impact' in m for m in messages_shown)


def test_messages_view_post_set_impact_malformed_reports_error(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {'setImpact': 'no-pipe-here'})

    assert fake.set_impact_calls == []
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Invalid sender impact update request' in m for m in messages_shown)


def test_messages_view_post_clear_impact_calls_service_with_none(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {'clearImpact': 'a@example.com'})

    assert fake.set_impact_calls == [('a@example.com', None)]
    assert response.context['has_performed_update'] is True


# --- POST: single-sender context menu actions --------------------------------

def test_messages_view_post_context_mark_as_read(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {
        'context_sender': 'a@example.com', 'context_action': 'markAsRead',
    })

    assert fake.mark_read_calls == [(['a@example.com'], 'inbox')]
    assert response.context['has_performed_update'] is True


def test_messages_view_post_context_delete_message(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {
        'context_sender': 'a@example.com', 'context_action': 'deleteMessage',
    })

    assert fake.delete_calls == [(['a@example.com'], 'inbox')]


def test_messages_view_post_context_delete_and_block_reports_warning_on_partial_failure(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService(block_result=False)
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {
        'context_sender': 'a@example.com', 'context_action': 'deleteMessageBlockSender',
    })

    assert fake.delete_calls == [(['a@example.com'], 'inbox')]
    assert fake.block_calls == [['a@example.com']]
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('failed to create a block rule' in m or 'but failed to create' in m for m in messages_shown)


# --- POST: bulk selected-sender actions ---------------------------------------

def test_messages_view_post_bulk_mark_as_read(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {
        'selected_options': ['a@example.com', 'b@example.com'], 'markAsRead': '1',
    })

    assert fake.mark_read_calls == [(['a@example.com', 'b@example.com'], 'inbox')]
    assert response.context['has_performed_update'] is True


def test_messages_view_post_bulk_delete_and_block_all_succeed(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {
        'selected_options': ['a@example.com'], 'deleteMessageBlockSender': '1',
    })

    assert fake.delete_calls == [(['a@example.com'], 'inbox')]
    assert fake.block_calls == [['a@example.com']]
    messages_shown = [str(m) for m in response.context['messages']]
    assert any('Deleted messages and blocked' in m for m in messages_shown)


def test_messages_view_context_sender_takes_precedence_over_selected_options(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeMessagesService()
    _patch_service(monkeypatch, fake)

    response = client.post(reverse('django_app.messages:messages'), {
        'context_sender': 'context@example.com', 'context_action': 'markAsRead',
        'selected_options': ['bulk@example.com'], 'markAsRead': '1',
    })

    assert fake.mark_read_calls == [(['context@example.com'], 'inbox')]


# --- messages_api_view (GET /api/messages, external bearer-token auth) -------
#
# Auth (require_external_api_token) has its own dedicated coverage in
# test_authentication.py. Multi-provider aggregation correctness (grouping,
# provider tagging, per-provider failure handling, sorting) belongs to
# UnifiedEmailServer.get_message_digest() and is covered in
# test_unified_email_server.py, not here -- this view is just a thin caller
# of it, so these tests patch `messages_views_module.UnifiedEmailServer`
# wholesale and focus on the view's own job: config/auth gating, query
# params, impact annotation, and status codes.

def _write_external_api_config(tmp_path: Path, token: str = 'good-token', enabled: bool = True, provider_enabled: bool = True) -> Path:
    token_dir = tmp_path / 'tokens'
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=provider_enabled),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(token_dir),
        external_api=ExternalApiConfig(enabled=enabled, tokens=[ExternalApiToken(token=token, label='tagesform')]),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])
    return token_dir


def _auth_header(token: str = 'good-token') -> Dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


class FakeUnifiedEmailServer:
    def __init__(
        self,
        authenticated_providers: Optional[List[Any]] = None,
        digest: Optional[List[Dict[str, Any]]] = None,
        raise_on_digest: Optional[Exception] = None,
    ) -> None:
        self._authenticated_providers = ['fake'] if authenticated_providers is None else authenticated_providers
        self._digest = digest if digest is not None else []
        self._raise_on_digest = raise_on_digest
        self.get_message_digest_calls: List[Dict[str, Any]] = []

    def get_authenticated_providers(self) -> List[Any]:
        return self._authenticated_providers

    def get_message_digest(self, folder: str = 'inbox', unread_only: bool = True, max_messages: int = 1000) -> List[Dict[str, Any]]:
        self.get_message_digest_calls.append({'folder': folder, 'unread_only': unread_only, 'max_messages': max_messages})
        if self._raise_on_digest:
            raise self._raise_on_digest
        return self._digest


def _patch_unified_email_server(monkeypatch: pytest.MonkeyPatch, fake_server: FakeUnifiedEmailServer) -> None:
    monkeypatch.setattr(messages_views_module, 'UnifiedEmailServer', lambda config: fake_server)


@dataclass
class FakeSenderCategorizationManager:
    """Double for SenderCategorizationManager -- the real one is backed by
    AppInfoCache, which touches the OS keyring (see test_app_info_cache.py).
    messages_api_view constructs one directly (it can't go through a
    Microsoft-gated MessagesService), so it must be patched out here too."""
    impacts: Dict[str, str] = field(default_factory=dict)

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
        return False


def _patch_sender_categorization(monkeypatch: pytest.MonkeyPatch) -> FakeSenderCategorizationManager:
    fake = FakeSenderCategorizationManager()
    monkeypatch.setattr(messages_views_module, 'SenderCategorizationManager', lambda storage_path: fake)
    return fake


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
    own config_path.exists() check -- ever runs, since both read the same
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
    _patch_unified_email_server(monkeypatch, FakeUnifiedEmailServer(authenticated_providers=[]))

    response = client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert response.status_code == 503
    assert 'error' in response.json()


def test_messages_api_view_returns_aggregated_messages_as_json(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer(digest=[
        {'fromName': 'Alice', 'fromAddress': 'a@example.com', 'count': 1, 'lastReceivedDateTime': '2024-01-01T00:00:00Z', 'provider': 'microsoft'},
    ])
    _patch_unified_email_server(monkeypatch, fake_server)

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
    fake_server = FakeUnifiedEmailServer()
    _patch_unified_email_server(monkeypatch, fake_server)

    client.get(reverse('django_app.messages:messages_api') + '?mailbox=archive&unread_only=false', **_auth_header())

    assert fake_server.get_message_digest_calls == [{'folder': 'archive', 'unread_only': False, 'max_messages': 1000}]


def test_messages_api_view_unread_only_defaults_true(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    _patch_sender_categorization(monkeypatch)
    fake_server = FakeUnifiedEmailServer()
    _patch_unified_email_server(monkeypatch, fake_server)

    client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert fake_server.get_message_digest_calls[0]['unread_only'] is True


def test_messages_api_view_high_impact_only_filters_response(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    fake_categorization = _patch_sender_categorization(monkeypatch)
    fake_categorization.impacts = {'a@example.com': ImpactLevel.HIGH_IMPACT.value, 'b@example.com': ImpactLevel.LOW_IMPACT.value}
    fake_server = FakeUnifiedEmailServer(digest=[
        {'fromName': 'A', 'fromAddress': 'a@example.com', 'count': 1, 'provider': 'microsoft'},
        {'fromName': 'B', 'fromAddress': 'b@example.com', 'count': 1, 'provider': 'microsoft'},
    ])
    _patch_unified_email_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:messages_api') + '?high_impact_only=true', **_auth_header())

    assert [m['fromAddress'] for m in response.json()['messages']] == ['a@example.com']


def test_messages_api_view_returns_502_when_digest_raises(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_external_api_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(raise_on_digest=RuntimeError('graph api down'))
    _patch_unified_email_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:messages_api'), **_auth_header())

    assert response.status_code == 502
    assert 'graph api down' in response.json()['error']
