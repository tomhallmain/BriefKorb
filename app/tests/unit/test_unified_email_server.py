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
from datetime import datetime, timezone
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


def test_handle_auth_callback_reports_false_but_still_stores_token_for_gmail_shaped_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Documents current behavior: this method unconditionally reads
    token_data['access_token'] to fetch user info, which is Microsoft's key
    shape, not Gmail's ('token'). For a Gmail-shaped token this KeyErrors,
    gets swallowed by the broad except, and handle_auth_callback reports
    False -- indistinguishable from a real failure -- even though
    store_token already succeeded and persisted the token.
    """
    server = _server(tmp_path, microsoft=False, gmail=True)
    provider = server.get_provider('gmail')
    gmail_shaped_token = {'token': 'gt', 'token_uri': 'https://oauth2.googleapis.com/token'}
    monkeypatch.setattr(provider.oauth, 'get_token_from_code', lambda code: gmail_shaped_token)

    result = server.handle_auth_callback('gmail', 'user1', 'auth-code')

    assert result is False
    assert server.token_manager.get_token('user1') == gmail_shaped_token


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
