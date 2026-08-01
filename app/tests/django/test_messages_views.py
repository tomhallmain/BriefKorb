"""Tests for django_app/messages/views.py's messages_view.

MessagesService (imported into this view module from .services) wraps live
Microsoft Graph API calls and the SenderCategorizationManager cache, so
every test patches `messages_views_module.MessagesService` with an
in-memory fake instead of letting the view construct a real one --
MessagesService itself already has its own unit coverage
(test_messages_services.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pytest
from django.test import Client
from django.urls import reverse

from django_app.messages import views as messages_views_module
from email_client.utils.sender_categorization import ImpactLevel


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
