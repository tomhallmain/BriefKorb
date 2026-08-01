"""Tests for django_app/messages/views.py's new additive views:
inbox_view, message_detail_view, and sender_categorization_view.

Unlike messages_view/messages_api_view's existing tests, inbox_view and
message_detail_view are built directly on UnifiedEmailServer with no
MessagesService involved, so these patch `messages_views_module.
UnifiedEmailServer` wholesale with a small fake exposing exactly the
methods these views call -- multi-provider correctness itself is
UnifiedEmailServer's own tested concern (test_unified_email_server.py).

sender_categorization_view constructs SenderCategorizationManager directly
(same reasoning as messages_api_view's Gmail path: it doesn't need
Microsoft to be configured), so its tests patch that out the same way
test_messages_views.py's FakeSenderCategorizationManager does.
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
from email_server.config import EmailServerConfig, ProviderConfig


def _write_config(tmp_path: Path, microsoft_enabled: bool = True, gmail_enabled: bool = False) -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=microsoft_enabled),
        gmail=ProviderConfig(enabled=gmail_enabled),
        token_storage_path=str(tmp_path / 'tokens'),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])


@dataclass
class FakeAuthenticatedProvider:
    provider_name: str
    user_id: str


class FakeUnifiedEmailServer:
    def __init__(
        self,
        authenticated_providers: Optional[List[FakeAuthenticatedProvider]] = None,
        messages: Optional[List[Any]] = None,
        digest: Optional[List[Dict[str, Any]]] = None,
        entity_count: int = 0,
        message_by_id: Optional[Dict[str, Any]] = None,
        raise_on_fetch: Optional[Exception] = None,
    ) -> None:
        self._authenticated_providers = authenticated_providers if authenticated_providers is not None else []
        self._messages = messages if messages is not None else []
        self._digest = digest if digest is not None else []
        self._entity_count = entity_count
        self._message_by_id = message_by_id or {}
        self._raise_on_fetch = raise_on_fetch
        self.get_user_messages_calls: List[Dict[str, Any]] = []
        self.get_message_digest_calls: List[Dict[str, Any]] = []
        self.extract_entities_calls: List[Any] = []
        self.get_message_calls: List[Dict[str, Any]] = []

    def get_authenticated_providers(self, provider_name: Optional[str] = None) -> List[FakeAuthenticatedProvider]:
        if provider_name is None:
            return self._authenticated_providers
        return [p for p in self._authenticated_providers if p.provider_name == provider_name]

    def get_user_messages(self, folder: str = 'inbox', unread_only: bool = False, max_messages: int = 100) -> List[Any]:
        self.get_user_messages_calls.append({'folder': folder, 'unread_only': unread_only, 'max_messages': max_messages})
        if self._raise_on_fetch:
            raise self._raise_on_fetch
        return self._messages

    def get_message_digest(self, messages: Optional[List[Any]] = None, **kwargs: Any) -> List[Dict[str, Any]]:
        self.get_message_digest_calls.append({'messages': messages, **kwargs})
        return self._digest

    def extract_entities(self, messages: Any) -> int:
        self.extract_entities_calls.append(messages)
        return self._entity_count

    def get_message(self, user_id: str, provider_name: str, message_id: str) -> Any:
        self.get_message_calls.append({'user_id': user_id, 'provider_name': provider_name, 'message_id': message_id})
        return self._message_by_id.get(message_id)


def _patch_server(monkeypatch: pytest.MonkeyPatch, fake_server: FakeUnifiedEmailServer) -> None:
    monkeypatch.setattr(messages_views_module, 'UnifiedEmailServer', lambda config: fake_server)


# --- inbox_view --------------------------------------------------------------

def test_inbox_view_shows_error_when_config_missing(client: Client) -> None:
    response = client.get(reverse('django_app.messages:inbox'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is False
    assert 'not configured' in response.context['error'].lower()


def test_inbox_view_shows_error_when_no_provider_configured(client: Client, tmp_path: Path) -> None:
    _write_config(tmp_path, microsoft_enabled=False, gmail_enabled=False)

    response = client.get(reverse('django_app.messages:inbox'))

    assert response.context['is_authenticated'] is False
    assert 'error' in response.context


def test_inbox_view_shows_error_when_no_authenticated_provider(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_server(monkeypatch, FakeUnifiedEmailServer(authenticated_providers=[]))

    response = client.get(reverse('django_app.messages:inbox'))

    assert response.context['is_authenticated'] is False
    assert 'authenticate' in response.context['error'].lower()


def test_inbox_view_renders_digest_and_entity_count(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        messages=['m1', 'm2'],
        digest=[{'fromName': 'Alice', 'provider': 'microsoft', 'count': 2, 'messages': []}],
        entity_count=3,
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:inbox'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is True
    assert response.context['messages_length'] == 2
    assert response.context['entity_count'] == 3
    assert response.context['messageData'] == fake_server._digest
    # get_message_digest must be called with the already-fetched list, not
    # trigger a second live fetch.
    assert fake_server.get_message_digest_calls[0]['messages'] == ['m1', 'm2']
    assert fake_server.extract_entities_calls == [['m1', 'm2']]


def test_inbox_view_passes_mailbox_and_unread_only_query_params(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    client.get(reverse('django_app.messages:inbox') + '?mailbox=archive&unread_only=false')

    assert fake_server.get_user_messages_calls[0]['folder'] == 'archive'
    assert fake_server.get_user_messages_calls[0]['unread_only'] is False


def test_inbox_view_shows_error_when_fetch_raises(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        raise_on_fetch=RuntimeError('graph api down'),
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:inbox'))

    assert response.context['is_authenticated'] is False
    assert 'graph api down' in response.context['error']


# --- message_detail_view --------------------------------------------------------

def test_message_detail_view_shows_error_when_config_missing(client: Client) -> None:
    response = client.get(reverse('django_app.messages:message_detail', args=['microsoft', 'm1']))

    assert response.status_code == 200
    assert 'not configured' in response.context['error'].lower()


def test_message_detail_view_shows_error_when_provider_not_authenticated(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_server(monkeypatch, FakeUnifiedEmailServer(authenticated_providers=[]))

    response = client.get(reverse('django_app.messages:message_detail', args=['microsoft', 'm1']))

    assert 'no authenticated microsoft' in response.context['error'].lower()


def test_message_detail_view_renders_found_message(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)

    @dataclass
    class FakeMessage:
        subject: str = 'Hello'
        sender: str = 'a@example.com'
        recipients: List[str] = field(default_factory=lambda: ['b@example.com'])
        received_date: Any = None
        provider: str = 'microsoft'
        body: str = '<p>Hi</p>'

    fake_server = FakeUnifiedEmailServer(
        authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')],
        message_by_id={'m1': FakeMessage()},
    )
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:message_detail', args=['microsoft', 'm1']))

    assert response.status_code == 200
    assert response.context['message'].subject == 'Hello'
    assert fake_server.get_message_calls == [{'user_id': 'user1', 'provider_name': 'microsoft', 'message_id': 'm1'}]
    # Django auto-escapes {{ message.body }} for the srcdoc="..." attribute
    # context -- the browser entity-decodes it back before parsing it as the
    # iframe's document, but the raw response bytes carry the escaped form.
    assert b'&lt;p&gt;Hi&lt;/p&gt;' in response.content


def test_message_detail_view_shows_not_found_when_message_missing(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake_server = FakeUnifiedEmailServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:message_detail', args=['microsoft', 'does-not-exist']))

    assert 'not found' in response.context['error'].lower()


def test_message_detail_view_shows_error_when_fetch_raises(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)

    class RaisingServer(FakeUnifiedEmailServer):
        def get_message(self, user_id: str, provider_name: str, message_id: str) -> Any:
            raise RuntimeError('graph api down')

    fake_server = RaisingServer(authenticated_providers=[FakeAuthenticatedProvider('microsoft', 'user1')])
    _patch_server(monkeypatch, fake_server)

    response = client.get(reverse('django_app.messages:message_detail', args=['microsoft', 'm1']))

    assert 'graph api down' in response.context['error']


# --- sender_categorization_view -----------------------------------------------

@dataclass
class FakeSenderCategorizationManager:
    records: List[Dict[str, Any]] = field(default_factory=list)
    set_exception_calls: List[Any] = field(default_factory=list)
    clear_exception_calls: List[str] = field(default_factory=list)

    def list_sender_records(self) -> List[Dict[str, Any]]:
        return self.records

    def set_sender_exception(self, sender_email: str, impact: ImpactLevel, source: str = 'manual') -> None:
        self.set_exception_calls.append((sender_email, impact, source))

    def clear_sender_exception(self, sender_email: str) -> None:
        self.clear_exception_calls.append(sender_email)


def _patch_sender_categorization(monkeypatch: pytest.MonkeyPatch, fake: Optional[FakeSenderCategorizationManager] = None) -> FakeSenderCategorizationManager:
    fake = fake or FakeSenderCategorizationManager()
    monkeypatch.setattr(messages_views_module, 'SenderCategorizationManager', lambda storage_path: fake)
    return fake


def test_sender_categorization_view_shows_error_when_config_missing(client: Client) -> None:
    response = client.get(reverse('django_app.messages:sender_categorization'))

    assert response.status_code == 200
    assert 'not configured' in response.context['error'].lower()


def test_sender_categorization_view_lists_records(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch, FakeSenderCategorizationManager(records=[
        {'sender': 'a@example.com', 'domain': 'example.com', 'impact': 'high-impact', 'source': 'inferred', 'has_exception': False},
    ]))

    response = client.get(reverse('django_app.messages:sender_categorization'))

    assert response.status_code == 200
    assert len(response.context['records']) == 1
    assert response.context['records'][0]['sender'] == 'a@example.com'


def test_sender_categorization_view_selects_record_by_query_param(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch, FakeSenderCategorizationManager(records=[
        {'sender': 'a@example.com', 'domain': 'example.com', 'impact': 'high-impact', 'source': 'inferred', 'has_exception': False, 'decision_trace': ['step1']},
    ]))

    response = client.get(reverse('django_app.messages:sender_categorization') + '?sender=a@example.com')

    assert response.context['selected_record']['sender'] == 'a@example.com'


def test_sender_categorization_view_shows_nothing_found_for_unknown_sender(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    _patch_sender_categorization(monkeypatch, FakeSenderCategorizationManager(records=[]))

    response = client.get(reverse('django_app.messages:sender_categorization') + '?sender=nobody@example.com')

    assert response.context['selected_record'] is None
    assert response.context['selected_sender'] == 'nobody@example.com'


def test_sender_categorization_view_post_set_impact_updates_and_redirects(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake = _patch_sender_categorization(monkeypatch)

    response = client.post(reverse('django_app.messages:sender_categorization'), {'setImpact': 'a@example.com|high-impact'})

    assert response.status_code == 302
    assert fake.set_exception_calls == [('a@example.com', ImpactLevel.HIGH_IMPACT, 'django_categorization_page')]


def test_sender_categorization_view_post_set_impact_malformed_does_not_call_manager(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake = _patch_sender_categorization(monkeypatch)

    response = client.post(reverse('django_app.messages:sender_categorization'), {'setImpact': 'no-pipe-here'})

    assert response.status_code == 302
    assert fake.set_exception_calls == []


def test_sender_categorization_view_post_clear_impact(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    fake = _patch_sender_categorization(monkeypatch)

    response = client.post(reverse('django_app.messages:sender_categorization'), {'clearImpact': 'a@example.com'})

    assert response.status_code == 302
    assert fake.clear_exception_calls == ['a@example.com']
