"""Tests for email_server/__init__.py's UnifiedEmailServer -- the top-level
orchestration class tying providers, auth, and message operations together.
This is the layer a future API would sit on top of.

Real MicrosoftGraphProvider/GmailProvider construction is safe to let run
for real here (no network touches __init__, as established by their own
Tier 2 unit tests) -- so tests build a real UnifiedEmailServer via
_server() and then monkeypatch specific bound methods (oauth.*,
provider.authenticate/get_messages/...) per scenario, rather than
re-implementing fake provider classes from scratch.

_init_entity_graph_manager is patched to a no-op across every test (see the
autouse fixture below): EntityGraphManager is a real, heavier RDF-backed
component with its own dedicated test suite (test_entity_graph.py) --
UnifiedEmailServer's own tests only need to verify that extract_entities()
delegates to whatever object ends up on self.entity_graph_manager, not that
EntityGraphManager itself works.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from email_server import AuthenticatedProvider, EmailMessage, UnifiedEmailServer
from email_server.config import EmailServerConfig, ProviderConfig
from email_server.providers.gmail.gmail import GmailProvider
from email_server.providers.microsoft.microsoft import MicrosoftGraphProvider


@pytest.fixture(autouse=True)
def _no_entity_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(UnifiedEmailServer, '_init_entity_graph_manager', staticmethod(lambda token_storage_path: None))


def _server(tmp_path: Path, microsoft: bool = True, gmail: bool = False) -> UnifiedEmailServer:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=True, client_id='cid', client_secret='csecret', tenant_id='tid', redirect_uri='http://x/ms')
        if microsoft else ProviderConfig(enabled=False),
        gmail=ProviderConfig(enabled=True, credentials_path='creds.json', redirect_uri='http://x/gmail')
        if gmail else ProviderConfig(enabled=False),
        token_storage_path=str(tmp_path / 'tokens'),
    )
    return UnifiedEmailServer(config=config)


def _message(id: str, received_date: datetime, provider: str = 'microsoft') -> EmailMessage:
    return EmailMessage(
        id=id, subject='S', sender='a@example.com', recipients=['b@example.com'],
        received_date=received_date, body='body', is_read=False, provider=provider,
    )


# --- __init__ ------------------------------------------------------------

def test_init_from_explicit_config_registers_enabled_providers(tmp_path: Path) -> None:
    server = _server(tmp_path, microsoft=True, gmail=True)

    assert isinstance(server.get_provider('microsoft'), MicrosoftGraphProvider)
    assert isinstance(server.get_provider('gmail'), GmailProvider)
    assert server.get_provider('microsoft').token_manager is server.token_manager


def test_init_does_not_register_disabled_providers(tmp_path: Path) -> None:
    server = _server(tmp_path, microsoft=True, gmail=False)

    assert server.get_provider('gmail') is None


def test_init_from_config_path_loads_yaml_file(tmp_path: Path) -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=True, client_id='cid', client_secret='csecret', tenant_id='tid', redirect_uri='http://x/ms'),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(tmp_path / 'tokens'),
    )
    config_path = tmp_path / 'config.yaml'
    config.save(str(config_path))

    server = UnifiedEmailServer(config_path=str(config_path))

    assert server.get_provider('microsoft') is not None


def test_init_raises_when_config_invalid(tmp_path: Path) -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=False), gmail=ProviderConfig(enabled=False),
        token_storage_path=str(tmp_path / 'tokens'),
    )

    with pytest.raises(ValueError):
        UnifiedEmailServer(config=config)


# --- register_provider / get_provider ---------------------------------------

def test_register_and_get_provider_round_trips(tmp_path: Path) -> None:
    server = _server(tmp_path)
    sentinel = object()

    server.register_provider('custom', sentinel)  # type: ignore[arg-type]

    assert server.get_provider('custom') is sentinel


def test_get_provider_returns_none_for_unknown_name(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.get_provider('does-not-exist') is None


# --- handle_auth_callback ----------------------------------------------------

def test_handle_auth_callback_returns_false_for_unknown_provider(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.handle_auth_callback('does-not-exist', 'user1', 'code') is False


def test_handle_auth_callback_stores_token_and_user_info_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider.oauth, 'get_token_from_code', lambda code: {'access_token': 'at', 'refresh_token': 'rt'})
    monkeypatch.setattr(provider.oauth, 'get_user_info', lambda token: {'mail': 'user@example.com'})

    result = server.handle_auth_callback('microsoft', 'user1', 'auth-code')

    assert result is True
    assert server.token_manager.get_token('user1') == {'access_token': 'at', 'refresh_token': 'rt'}
    assert server.token_manager.get_user_info('user1') == {'mail': 'user@example.com'}


def test_handle_auth_callback_returns_false_when_token_exchange_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')

    def raise_error(code: str) -> Any:
        raise RuntimeError('exchange failed')

    monkeypatch.setattr(provider.oauth, 'get_token_from_code', raise_error)

    assert server.handle_auth_callback('microsoft', 'user1', 'auth-code') is False


def test_handle_auth_callback_succeeds_for_gmail_shaped_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: token_data's access-token key is provider-shape
    dependent (Microsoft: 'access_token', Gmail: 'token'). This used to read
    token_data['access_token'] unconditionally, which KeyError'd on a
    Gmail-shaped token, got swallowed by the broad except, and reported
    False even though store_token had already succeeded -- indistinguishable
    from a genuine failure. It now goes through TokenManager's
    provider-agnostic get_valid_token() accessor instead.
    """
    server = _server(tmp_path, microsoft=False, gmail=True)
    provider = server.get_provider('gmail')
    gmail_shaped_token = {'token': 'gt', 'token_uri': 'https://oauth2.googleapis.com/token'}
    monkeypatch.setattr(provider.oauth, 'get_token_from_code', lambda code: gmail_shaped_token)
    captured: Dict[str, Any] = {}

    def fake_get_user_info(access_token: str) -> Dict[str, Any]:
        captured['access_token'] = access_token
        return {'emailAddress': 'user1@example.com'}

    monkeypatch.setattr(provider.oauth, 'get_user_info', fake_get_user_info)

    result = server.handle_auth_callback('gmail', 'user1', 'auth-code')

    assert result is True
    assert captured['access_token'] == 'gt'
    assert server.token_manager.get_token('user1') == gmail_shaped_token
    assert server.token_manager.get_user_info('user1') == {'emailAddress': 'user1@example.com'}


def test_handle_auth_callback_returns_false_when_token_has_no_usable_access_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    token_without_access_token = {'msal_cache': 'x'}
    monkeypatch.setattr(provider.oauth, 'get_token_from_code', lambda code: token_without_access_token)

    def fail_if_called(access_token: str) -> Any:
        raise AssertionError('get_user_info should not be called without a usable access token')

    monkeypatch.setattr(provider.oauth, 'get_user_info', fail_if_called)

    result = server.handle_auth_callback('microsoft', 'user1', 'auth-code')

    assert result is False
    assert server.token_manager.get_token('user1') == token_without_access_token


# --- get_authenticated_providers / get_authenticated_users -------------------

def test_get_authenticated_providers_returns_empty_when_no_tokens_stored(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.get_authenticated_providers() == []


def test_get_authenticated_providers_includes_users_whose_authenticate_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    server.token_manager.store_user_info('user1', {'mail': 'user1@example.com'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)

    result = server.get_authenticated_providers()

    assert len(result) == 1
    assert result[0].user_id == 'user1'
    assert result[0].provider_name == 'microsoft'
    assert result[0].user_info == {'mail': 'user1@example.com'}


def test_get_authenticated_providers_skips_users_whose_authenticate_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert server.get_authenticated_providers() == []


def test_get_authenticated_providers_filters_by_provider_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path, microsoft=True, gmail=True)
    ms_provider = server.get_provider('microsoft')
    gmail_provider = server.get_provider('gmail')
    server.token_manager.store_token('ms-user', {'access_token': 'at'})
    monkeypatch.setattr(ms_provider, 'authenticate', lambda user_id: True)
    monkeypatch.setattr(gmail_provider, 'authenticate', lambda user_id: True)

    result = server.get_authenticated_providers(provider_name='microsoft')

    assert [r.provider_name for r in result] == ['microsoft']


def test_get_authenticated_providers_ignores_unregistered_provider_name(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.get_authenticated_providers(provider_name='not-registered') == []


def test_get_authenticated_users_groups_user_ids_by_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    server.token_manager.store_token('user2', {'access_token': 'at2'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)

    result = server.get_authenticated_users()

    assert sorted(result['microsoft']) == ['user1', 'user2']


# --- get_user_messages --------------------------------------------------------

def test_get_user_messages_uses_all_authenticated_providers_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    older = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    newer = _message('m2', datetime(2024, 6, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: [older, newer])

    result = server.get_user_messages()

    assert [m.id for m in result] == ['m2', 'm1']  # newest first


def test_get_user_messages_with_single_provider_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: [_message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))])

    result = server.get_user_messages(providers=provider)

    assert [m.id for m in result] == ['m1']


def test_get_user_messages_with_list_of_provider_instances(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: [_message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))])

    result = server.get_user_messages(providers=[provider])

    assert [m.id for m in result] == ['m1']


def test_get_user_messages_with_list_of_authenticated_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: [_message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))])
    auth_provider = AuthenticatedProvider(provider=provider, provider_name='microsoft', user_id='user1', user_info={})

    result = server.get_user_messages(providers=[auth_provider])

    assert [m.id for m in result] == ['m1']


def test_get_user_messages_skips_provider_that_raises_and_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path, microsoft=True, gmail=True)
    ms_provider = server.get_provider('microsoft')
    gmail_provider = server.get_provider('gmail')
    server.token_manager.store_token('ms-user', {'access_token': 'at'})
    server.token_manager.store_token('gmail-user', {'token': 'gt', 'token_uri': 'https://oauth2.googleapis.com/token'})
    # ms_provider and gmail_provider share one TokenManager (by design -- see
    # UnifiedEmailServer._initialize_providers), so get_all_user_ids() returns
    # *both* stored users to *each* provider's authenticate() check. Real
    # authenticate() implementations filter by token shape (verify_for_provider_type);
    # these mocks must do the same or every user "authenticates" against every
    # provider, double-counting messages.
    monkeypatch.setattr(ms_provider, 'authenticate', lambda user_id: user_id == 'ms-user')
    monkeypatch.setattr(gmail_provider, 'authenticate', lambda user_id: user_id == 'gmail-user')

    def raise_error(**kwargs: Any) -> Any:
        raise RuntimeError('graph api down')

    monkeypatch.setattr(ms_provider, 'get_messages', raise_error)
    monkeypatch.setattr(gmail_provider, 'get_messages', lambda **kwargs: [_message('g1', datetime(2024, 1, 1, tzinfo=timezone.utc), provider='gmail')])

    result = server.get_user_messages()

    assert [m.id for m in result] == ['g1']


def test_get_user_messages_returns_empty_list_for_empty_provider_list(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.get_user_messages(providers=[]) == []


# --- get_sent_messages -----------------------------------------------------

def test_get_sent_messages_uses_provider_specific_sent_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    captured: Dict[str, Any] = {}
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: captured.update(kwargs) or [])

    server.get_sent_messages()

    assert captured['folder'] == 'sentitems'
    assert captured['unread_only'] is False


def test_get_sent_messages_merges_multiple_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path, microsoft=True, gmail=True)
    ms_provider = server.get_provider('microsoft')
    gmail_provider = server.get_provider('gmail')
    server.token_manager.store_token('ms-user', {'access_token': 'at'})
    server.token_manager.store_token('gmail-user', {'token': 'gt', 'token_uri': 'https://oauth2.googleapis.com/token'})
    monkeypatch.setattr(ms_provider, 'authenticate', lambda user_id: user_id == 'ms-user')
    monkeypatch.setattr(gmail_provider, 'authenticate', lambda user_id: user_id == 'gmail-user')
    monkeypatch.setattr(ms_provider, 'get_messages', lambda **kwargs: [_message('ms1', datetime(2024, 1, 1, tzinfo=timezone.utc))])
    monkeypatch.setattr(gmail_provider, 'get_messages', lambda **kwargs: [_message('g1', datetime(2024, 1, 1, tzinfo=timezone.utc), provider='gmail')])

    result = server.get_sent_messages()

    assert {m.id for m in result} == {'ms1', 'g1'}


def test_get_sent_messages_skips_provider_that_raises_and_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)

    def raise_error(**kwargs: Any) -> Any:
        raise RuntimeError('graph api down')

    monkeypatch.setattr(provider, 'get_messages', raise_error)

    assert server.get_sent_messages() == []


def test_get_sent_messages_returns_empty_list_when_no_authenticated_providers(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.get_sent_messages() == []


def test_get_user_messages_excludes_blocked_senders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    blocked = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    blocked.sender = 'spam@example.com'
    allowed = _message('m2', datetime(2024, 6, 1, tzinfo=timezone.utc))
    allowed.sender = 'friend@example.com'
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: [blocked, allowed])
    server.block_sender('spam@example.com')

    result = server.get_user_messages()

    assert [m.id for m in result] == ['m2']


# --- blocklist -------------------------------------------------------------

def test_block_sender_and_is_sender_blocked_round_trip(tmp_path: Path) -> None:
    server = _server(tmp_path)

    assert server.is_sender_blocked('spam@example.com') is False

    server.block_sender('Spam@Example.COM')

    assert server.is_sender_blocked('spam@example.com') is True


def test_get_blocked_senders_returns_all_blocked(tmp_path: Path) -> None:
    server = _server(tmp_path)
    server.block_sender('a@example.com')
    server.block_sender('b@example.com')

    assert server.get_blocked_senders() == {'a@example.com', 'b@example.com'}


def test_unblock_sender_removes_from_blocked_senders(tmp_path: Path) -> None:
    server = _server(tmp_path)
    server.block_sender('spam@example.com')

    server.unblock_sender('Spam@Example.COM')

    assert server.is_sender_blocked('spam@example.com') is False
    assert server.get_blocked_senders() == set()


def test_get_block_events_round_trips_through_blocked_sender_tracker(tmp_path: Path) -> None:
    from email_server.blocked_sender_tracking import BlockEvent

    server = _server(tmp_path)
    server.blocked_sender_tracker.record(BlockEvent(sender='spam@example.com', source='desktop_email_client'))

    events = server.get_block_events()

    assert [e['sender'] for e in events] == ['spam@example.com']


def test_get_block_events_filters_by_sender(tmp_path: Path) -> None:
    from email_server.blocked_sender_tracking import BlockEvent

    server = _server(tmp_path)
    server.blocked_sender_tracker.record(BlockEvent(sender='a@example.com', source='s'))
    server.blocked_sender_tracker.record(BlockEvent(sender='b@example.com', source='s'))

    events = server.get_block_events(sender='a@example.com')

    assert [e['sender'] for e in events] == ['a@example.com']


def test_get_blocked_sender_summary_combines_events_and_local_block_state(tmp_path: Path) -> None:
    from email_server.blocked_sender_tracking import BlockEvent

    server = _server(tmp_path)
    server.blocked_sender_tracker.record(BlockEvent(sender='a@example.com', source='desktop_email_client'))
    server.blocked_sender_tracker.record(BlockEvent(sender='b@example.com', source='django_web_messages', sender_kind='display_name'))
    server.block_sender('a@example.com')  # only a@example.com is locally suppressed

    summaries = server.get_blocked_sender_summary()

    by_sender = {s['sender']: s for s in summaries}
    assert by_sender['a@example.com']['is_locally_blocked'] is True
    assert by_sender['b@example.com']['is_locally_blocked'] is False


def test_get_blocked_sender_summary_returns_empty_list_with_no_history(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.get_blocked_sender_summary() == []


# --- send_message --------------------------------------------------------------

def test_send_message_returns_false_for_unknown_provider(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.send_message('user1', 'does-not-exist', 'to@example.com', 'S', 'B') is False


def test_send_message_returns_false_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert server.send_message('user1', 'microsoft', 'to@example.com', 'S', 'B') is False


def test_send_message_delegates_to_provider_when_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    captured: Dict[str, Any] = {}

    def fake_send(user_id: str, to: Any, subject: str, body: str, cc: Any = None, bcc: Any = None) -> bool:
        captured.update(user_id=user_id, to=to, subject=subject, body=body)
        return True

    monkeypatch.setattr(provider, 'send_message', fake_send)

    result = server.send_message('user1', 'microsoft', 'to@example.com', 'Subj', 'Body')

    assert result is True
    assert captured == {'user_id': 'user1', 'to': 'to@example.com', 'subject': 'Subj', 'body': 'Body'}


# --- mark_messages_as_read ---------------------------------------------------

def test_mark_messages_as_read_returns_false_for_unknown_provider(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.mark_messages_as_read('user1', 'does-not-exist', ['m1']) is False


def test_mark_messages_as_read_returns_false_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert server.mark_messages_as_read('user1', 'microsoft', ['m1']) is False


def test_mark_messages_as_read_delegates_to_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    calls = []
    monkeypatch.setattr(provider, 'mark_as_read', lambda user_id, message_ids: calls.append((user_id, message_ids)) or True)

    result = server.mark_messages_as_read('user1', 'microsoft', ['m1', 'm2'])

    assert result is True
    assert calls == [('user1', ['m1', 'm2'])]


# --- delete_user_messages ---------------------------------------------------

def test_delete_user_messages_returns_false_for_unknown_provider(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.delete_user_messages('user1', 'does-not-exist', ['m1']) is False


def test_delete_user_messages_returns_false_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert server.delete_user_messages('user1', 'microsoft', ['m1']) is False


def test_delete_user_messages_delegates_to_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    calls = []
    monkeypatch.setattr(provider, 'delete_messages', lambda user_id, message_ids: calls.append((user_id, message_ids)) or True)

    result = server.delete_user_messages('user1', 'microsoft', ['m1'])

    assert result is True
    assert calls == [('user1', ['m1'])]


# --- extract_entities --------------------------------------------------------

def test_extract_entities_returns_zero_when_manager_unavailable(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.entity_graph_manager is None

    assert server.extract_entities([]) == 0


def test_extract_entities_delegates_to_manager_when_available(tmp_path: Path) -> None:
    server = _server(tmp_path)

    @dataclass
    class FakeEntityGraphManager:
        process_messages_calls: List[Any] = field(default_factory=list)

        def process_messages(self, messages: Any) -> int:
            self.process_messages_calls.append(messages)
            return 3

    fake_manager = FakeEntityGraphManager()
    server.entity_graph_manager = fake_manager
    messages = [_message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))]

    result = server.extract_entities(messages)

    assert result == 3
    assert fake_manager.process_messages_calls == [messages]


# --- get_message_digest -------------------------------------------------------

def test_get_message_digest_returns_empty_list_when_no_messages(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.get_message_digest() == []


def test_get_message_digest_aggregates_by_sender_and_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: [
        _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc)),
        _message('m2', datetime(2024, 1, 2, tzinfo=timezone.utc)),
    ])

    digest = server.get_message_digest()

    assert len(digest) == 1
    assert digest[0]['count'] == 2
    assert digest[0]['provider'] == 'microsoft'


def test_get_message_digest_parses_display_name_and_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    message = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    message.sender = 'Alice Smith <alice@example.com>'
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: [message])

    digest = server.get_message_digest()

    assert digest[0]['fromName'] == 'Alice Smith'
    assert digest[0]['fromAddress'] == 'alice@example.com'


def test_get_message_digest_falls_back_to_unknown_name_for_bare_address_sender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Microsoft's provider strips EmailMessage.sender down to a bare
    address (no display name), unlike Gmail's raw From header -- this must
    still produce a usable bucket rather than erroring or mis-parsing."""
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    message = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    message.sender = 'alice@example.com'
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: [message])

    digest = server.get_message_digest()

    assert digest[0]['fromName'] == 'Unknown'
    assert digest[0]['fromAddress'] == 'alice@example.com'


def test_get_message_digest_keeps_same_display_name_separate_across_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path, microsoft=True, gmail=True)
    ms_provider = server.get_provider('microsoft')
    gmail_provider = server.get_provider('gmail')
    server.token_manager.store_token('ms-user', {'access_token': 'at'})
    server.token_manager.store_token('gmail-user', {'token': 'gt', 'token_uri': 'https://oauth2.googleapis.com/token'})
    monkeypatch.setattr(ms_provider, 'authenticate', lambda user_id: user_id == 'ms-user')
    monkeypatch.setattr(gmail_provider, 'authenticate', lambda user_id: user_id == 'gmail-user')
    ms_message = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    ms_message.sender = 'Bob <bob@work.example.com>'
    gmail_message = _message('g1', datetime(2024, 1, 1, tzinfo=timezone.utc), provider='gmail')
    gmail_message.sender = 'Bob <bob@personal.example.com>'
    monkeypatch.setattr(ms_provider, 'get_messages', lambda **kwargs: [ms_message])
    monkeypatch.setattr(gmail_provider, 'get_messages', lambda **kwargs: [gmail_message])

    digest = server.get_message_digest()

    assert len(digest) == 2
    assert {d['provider'] for d in digest} == {'microsoft', 'gmail'}


def test_get_message_digest_sorts_by_count_descending_then_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)

    def _senders(sender: str, count: int) -> List[EmailMessage]:
        msgs = []
        for i in range(count):
            m = _message(f'{sender}-{i}', datetime(2024, 1, 1, tzinfo=timezone.utc))
            m.sender = sender
            msgs.append(m)
        return msgs

    messages = _senders('Zed <zed@example.com>', 3) + _senders('Amy <amy@example.com>', 1) + _senders('Bob <bob@example.com>', 3)
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: messages)

    digest = server.get_message_digest()

    assert [(d['fromName'], d['count']) for d in digest] == [('Bob', 3), ('Zed', 3), ('Amy', 1)]


def test_get_message_digest_passes_through_folder_unread_only_and_max_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    server.token_manager.store_token('user1', {'access_token': 'at'})
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    captured: Dict[str, Any] = {}
    monkeypatch.setattr(provider, 'get_messages', lambda **kwargs: captured.update(kwargs) or [])

    server.get_message_digest(folder='archive', unread_only=False, max_messages=50)

    assert captured['folder'] == 'archive'
    assert captured['unread_only'] is False
    assert captured['max_messages'] == 50


def test_get_message_digest_aggregates_from_given_messages_without_fetching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')

    def fail_if_called(**kwargs: Any) -> Any:
        raise AssertionError('should not fetch when messages= is given')

    monkeypatch.setattr(provider, 'get_messages', fail_if_called)
    messages = [_message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))]

    digest = server.get_message_digest(messages=messages)

    assert len(digest) == 1
    assert digest[0]['count'] == 1


def test_get_message_digest_bucket_carries_per_message_summaries(tmp_path: Path) -> None:
    server = _server(tmp_path)
    m1 = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    m1.is_read = False
    m2 = _message('m2', datetime(2024, 1, 2, tzinfo=timezone.utc))
    m2.is_read = True

    digest = server.get_message_digest(messages=[m1, m2])

    assert len(digest) == 1
    bucket = digest[0]
    assert bucket['count'] == 2
    assert {s['id'] for s in bucket['messages']} == {'m1', 'm2'}
    m1_summary = next(s for s in bucket['messages'] if s['id'] == 'm1')
    assert m1_summary['isRead'] is False
    assert m1_summary['subject'] == 'S'
    assert m1_summary['lastReceivedDateTime'] == m1.received_date.isoformat()


# --- get_message_digest: subject_keyword / sender_search --------------------

def test_get_message_digest_subject_keyword_filters_before_aggregation(tmp_path: Path) -> None:
    server = _server(tmp_path)
    matching = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    matching.subject = 'Invoice attached'
    matching.sender = 'a@example.com'
    non_matching = _message('m2', datetime(2024, 1, 2, tzinfo=timezone.utc))
    non_matching.subject = 'Hello there'
    non_matching.sender = 'b@example.com'

    digest = server.get_message_digest(messages=[matching, non_matching], subject_keyword='invoice')

    assert len(digest) == 1
    assert digest[0]['fromAddress'] == 'a@example.com'
    assert digest[0]['count'] == 1


def test_get_message_digest_subject_keyword_drops_sender_with_no_matches(tmp_path: Path) -> None:
    server = _server(tmp_path)
    message = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    message.subject = 'Hello there'

    digest = server.get_message_digest(messages=[message], subject_keyword='invoice')

    assert digest == []


def test_get_message_digest_sender_search_matches_name_or_address(tmp_path: Path) -> None:
    server = _server(tmp_path)
    alice = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    alice.sender = 'Alice Smith <alice@example.com>'
    bob = _message('m2', datetime(2024, 1, 2, tzinfo=timezone.utc))
    bob.sender = 'Bob Jones <bob@example.com>'

    digest = server.get_message_digest(messages=[alice, bob], sender_search='alice')

    assert len(digest) == 1
    assert digest[0]['fromName'] == 'Alice Smith'


def test_get_message_digest_sender_search_matches_address_when_name_differs(tmp_path: Path) -> None:
    server = _server(tmp_path)
    message = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    message.sender = 'Newsletter <updates@example.com>'

    digest = server.get_message_digest(messages=[message], sender_search='updates@')

    assert len(digest) == 1


def test_get_message_digest_sender_search_is_case_insensitive(tmp_path: Path) -> None:
    server = _server(tmp_path)
    message = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    message.sender = 'Alice Smith <alice@example.com>'

    digest = server.get_message_digest(messages=[message], sender_search='ALICE')

    assert len(digest) == 1


def test_get_message_digest_sender_search_excludes_non_matching_senders(tmp_path: Path) -> None:
    server = _server(tmp_path)
    message = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    message.sender = 'Alice Smith <alice@example.com>'

    digest = server.get_message_digest(messages=[message], sender_search='nobody')

    assert digest == []


# --- get_message_digest: response status ------------------------------------

def _sent_message(msg_id: str, received_date: datetime, to: str, provider: str = 'microsoft') -> EmailMessage:
    m = _message(msg_id, received_date, provider=provider)
    m.recipients = [to]
    return m


def test_get_message_digest_awaiting_your_reply_when_no_sent_message_and_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    old_received = datetime.now(timezone.utc) - timedelta(days=10)
    message = _message('m1', old_received)
    message.sender = 'alice@example.com'
    monkeypatch.setattr(server, 'get_sent_messages', lambda **kwargs: [])

    digest = server.get_message_digest(messages=[message], include_response_status=True)

    assert digest[0]['awaitingYourReply'] is True
    assert digest[0]['awaitingTheirReply'] is False
    assert digest[0]['lastSentToSender'] is None


def test_get_message_digest_awaiting_their_reply_when_sent_after_last_received_and_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    old_received = datetime.now(timezone.utc) - timedelta(days=10)
    old_sent = datetime.now(timezone.utc) - timedelta(days=5)
    message = _message('m1', old_received)
    message.sender = 'alice@example.com'
    sent = _sent_message('s1', old_sent, to='alice@example.com')
    monkeypatch.setattr(server, 'get_sent_messages', lambda **kwargs: [sent])

    digest = server.get_message_digest(messages=[message], include_response_status=True)

    assert digest[0]['awaitingYourReply'] is False
    assert digest[0]['awaitingTheirReply'] is True
    assert digest[0]['lastSentToSender'] == old_sent.isoformat()


def test_get_message_digest_not_stale_within_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    recent_received = datetime.now(timezone.utc) - timedelta(hours=1)
    message = _message('m1', recent_received)
    message.sender = 'alice@example.com'
    monkeypatch.setattr(server, 'get_sent_messages', lambda **kwargs: [])

    digest = server.get_message_digest(messages=[message], include_response_status=True)

    assert digest[0]['awaitingYourReply'] is False
    assert digest[0]['awaitingTheirReply'] is False


def test_get_message_digest_response_status_respects_stale_after_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    received = datetime.now(timezone.utc) - timedelta(days=2)
    message = _message('m1', received)
    message.sender = 'alice@example.com'
    monkeypatch.setattr(server, 'get_sent_messages', lambda **kwargs: [])

    not_yet_stale = server.get_message_digest(messages=[message], include_response_status=True, stale_after_days=5.0)
    already_stale = server.get_message_digest(messages=[message], include_response_status=True, stale_after_days=1.0)

    assert not_yet_stale[0]['awaitingYourReply'] is False
    assert already_stale[0]['awaitingYourReply'] is True


def test_get_message_digest_no_response_status_fields_when_not_requested(tmp_path: Path) -> None:
    server = _server(tmp_path)
    message = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))

    digest = server.get_message_digest(messages=[message])

    assert 'awaitingYourReply' not in digest[0]
    assert 'lastSentToSender' not in digest[0]


def test_get_message_digest_awaiting_your_reply_only_filters_and_implies_computation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    stale = _message('m1', datetime.now(timezone.utc) - timedelta(days=10))
    stale.sender = 'stale@example.com'
    fresh = _message('m2', datetime.now(timezone.utc) - timedelta(hours=1))
    fresh.sender = 'fresh@example.com'
    monkeypatch.setattr(server, 'get_sent_messages', lambda **kwargs: [])

    # include_response_status not explicitly set -- the _only flag alone
    # must still trigger computation.
    digest = server.get_message_digest(messages=[stale, fresh], awaiting_your_reply_only=True)

    assert [d['fromAddress'] for d in digest] == ['stale@example.com']


def test_get_message_digest_awaiting_their_reply_only_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    message = _message('m1', datetime.now(timezone.utc) - timedelta(days=10))
    message.sender = 'alice@example.com'
    old_sent = _sent_message('s1', datetime.now(timezone.utc) - timedelta(days=5), to='alice@example.com')
    monkeypatch.setattr(server, 'get_sent_messages', lambda **kwargs: [old_sent])

    awaiting_their_reply = server.get_message_digest(messages=[message], awaiting_their_reply_only=True)
    awaiting_your_reply = server.get_message_digest(messages=[message], awaiting_your_reply_only=True)

    assert len(awaiting_their_reply) == 1
    assert awaiting_your_reply == []


def test_get_message_digest_response_status_normalizes_gmail_style_recipients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gmail's EmailMessage.recipients holds raw 'Name <addr>' header
    strings, unlike Microsoft's bare addresses -- the response-status
    lookup must normalize both the same way sender parsing already does."""
    server = _server(tmp_path)
    old_received = datetime.now(timezone.utc) - timedelta(days=10)
    old_sent = datetime.now(timezone.utc) - timedelta(days=5)
    message = _message('m1', old_received, provider='gmail')
    message.sender = 'alice@example.com'
    sent = _sent_message('s1', old_sent, to='Alice Smith <alice@example.com>', provider='gmail')
    monkeypatch.setattr(server, 'get_sent_messages', lambda **kwargs: [sent])

    digest = server.get_message_digest(messages=[message], include_response_status=True)

    assert digest[0]['awaitingTheirReply'] is True


# --- get_message --------------------------------------------------------------

def test_get_message_returns_none_for_unknown_provider(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.get_message('user1', 'does-not-exist', 'm1') is None


def test_get_message_returns_none_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert server.get_message('user1', 'microsoft', 'm1') is None


def test_get_message_delegates_to_provider_when_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    expected = _message('m1', datetime(2024, 1, 1, tzinfo=timezone.utc))
    captured: Dict[str, Any] = {}

    def fake_get_message(user_id: str, message_id: str) -> Any:
        captured.update(user_id=user_id, message_id=message_id)
        return expected

    monkeypatch.setattr(provider, 'get_message', fake_get_message)

    result = server.get_message('user1', 'microsoft', 'm1')

    assert result is expected
    assert captured == {'user_id': 'user1', 'message_id': 'm1'}


# --- block_senders --------------------------------------------------------------
#
# UnifiedEmailServer.block_senders() is the single place blocking is
# handled: local suppression (SenderBlocklist) and BlockEvent recording
# always happen once a provider is reached and authenticated, regardless
# of whether that provider can create a durable server-side rule --
# providers (EmailProvider.block_senders) only report which senders got a
# durable rule and do no suppression/recording themselves.

def test_block_senders_returns_false_for_unknown_provider(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.block_senders('user1', 'does-not-exist', ['Alice']) is False
    assert server.is_sender_blocked('Alice') is False
    assert server.get_block_events() == []


def test_block_senders_returns_false_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert server.block_senders('user1', 'microsoft', ['Alice']) is False
    assert server.is_sender_blocked('Alice') is False
    assert server.get_block_events() == []


def test_block_senders_delegates_to_provider_when_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    captured: Dict[str, Any] = {}

    def fake_block_senders(user_id: str, sender_names: List[str]) -> List[str]:
        captured.update(user_id=user_id, sender_names=sender_names)
        return sender_names

    monkeypatch.setattr(provider, 'block_senders', fake_block_senders)

    result = server.block_senders('user1', 'microsoft', ['Alice', 'Bob'])

    assert result is True
    assert captured == {'user_id': 'user1', 'sender_names': ['Alice', 'Bob']}


def test_block_senders_always_locally_suppresses_every_sender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Local suppression happens for every sender once the provider is
    reached, independent of the durable-rule outcome (matters most for
    partial failures, where only some senders got a durable rule)."""
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    monkeypatch.setattr(provider, 'block_senders', lambda user_id, sender_names: ['alice@example.com'])

    server.block_senders('user1', 'microsoft', ['alice@example.com', 'bob@example.com'])

    assert server.is_sender_blocked('alice@example.com') is True
    assert server.is_sender_blocked('bob@example.com') is True


def test_block_senders_records_one_event_per_sender_with_provider_only_for_durable_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    monkeypatch.setattr(provider, 'block_senders', lambda user_id, sender_names: ['alice@example.com'])

    server.block_senders('user1', 'microsoft', ['alice@example.com', 'bob@example.com'], source='desktop_email_client')

    events = {e['sender']: e for e in server.get_block_events()}
    assert set(events) == {'alice@example.com', 'bob@example.com'}
    assert events['alice@example.com']['provider'] == 'microsoft'
    assert events['bob@example.com']['provider'] is None
    assert all(e['source'] == 'desktop_email_client' for e in events.values())


def test_block_senders_records_exactly_one_event_per_sender_no_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: blocking used to be recorded both by
    MicrosoftGraphProvider.block_senders() itself and, on desktop, a second
    time by the caller -- producing two BlockEvents for one block action.
    Recording now happens exactly once, here, regardless of caller."""
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    monkeypatch.setattr(provider, 'block_senders', lambda user_id, sender_names: sender_names)

    server.block_senders('user1', 'microsoft', ['alice@example.com'])

    assert len(server.get_block_events(sender='alice@example.com')) == 1


def test_block_senders_passes_through_sender_details_to_recorded_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(tmp_path)
    provider = server.get_provider('microsoft')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)
    monkeypatch.setattr(provider, 'block_senders', lambda user_id, sender_names: sender_names)
    details = {'alice@example.com': {'display_name': 'Alice', 'subjects': ['Hi'], 'message_count': 3}}

    server.block_senders('user1', 'microsoft', ['alice@example.com'], sender_details=details)

    [event] = server.get_block_events(sender='alice@example.com')
    assert event['sender_display_name'] == 'Alice'
    assert event['message_subjects'] == ['Hi']
    assert event['message_count'] == 3


def test_block_senders_returns_false_for_gmail_since_it_is_unsupported_but_still_locally_suppresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a dispatch-layer test of GmailProvider's own behavior (that's
    test_gmail_provider.py's job) -- just confirms UnifiedEmailServer
    surfaces "unsupported" the same way as any other durable-rule failure
    (return False), while still locally suppressing and recording an
    audit event -- Gmail has no durable server-side block, but that
    shouldn't mean blocking a Gmail sender does nothing at all."""
    server = _server(tmp_path, microsoft=False, gmail=True)
    provider = server.get_provider('gmail')
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: True)

    result = server.block_senders('user1', 'gmail', ['alice@example.com'])

    assert result is False
    assert server.is_sender_blocked('alice@example.com') is True
    [event] = server.get_block_events(sender='alice@example.com')
    assert event['provider'] is None
