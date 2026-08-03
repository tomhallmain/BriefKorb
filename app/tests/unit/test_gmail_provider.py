"""Tests for email_server/providers/gmail/gmail.py's GmailProvider.

``authenticate()`` does local ``from google.oauth2.credentials import
Credentials`` / ``from googleapiclient.discovery import build`` inside the
method body (shadowing the module-level ``build`` import), so those names
must be patched on the real third-party modules -- patching
``providers.gmail.gmail.build`` would not reach the local import. All other
methods (``get_messages`` etc.) operate purely on ``self._service``, so
those tests skip ``authenticate()`` entirely by assigning a fake service
directly.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from googleapiclient import discovery as googleapiclient_discovery_module
import google.oauth2.credentials as google_credentials_module

from email_server.auth import TokenManager
from email_server.providers.gmail.gmail import GmailProvider


def _provider(tmp_path: Path) -> GmailProvider:
    token_manager = TokenManager(storage_path=str(tmp_path))
    return GmailProvider(credentials_path='creds.json', redirect_uri='http://x/callback', token_manager=token_manager)


def _patch_service_build(monkeypatch: pytest.MonkeyPatch, fake_service: Any) -> None:
    monkeypatch.setattr(google_credentials_module, 'Credentials', lambda **kwargs: object())
    monkeypatch.setattr(googleapiclient_discovery_module, 'build', lambda *a, **k: fake_service)


class _Execable:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return self._result


@dataclass
class FakeMessagesResource:
    list_results: Dict[str, Any] = field(default_factory=dict)
    get_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    list_calls: List[Dict[str, Any]] = field(default_factory=list)
    get_calls: List[Dict[str, Any]] = field(default_factory=list)
    modify_calls: List[Dict[str, Any]] = field(default_factory=list)
    trash_calls: List[str] = field(default_factory=list)
    send_calls: List[Dict[str, Any]] = field(default_factory=list)

    def list(self, userId: str, q: Optional[str] = None, maxResults: Optional[int] = None) -> _Execable:
        self.list_calls.append({'userId': userId, 'q': q, 'maxResults': maxResults})
        return _Execable(self.list_results)

    def get(self, userId: str, id: str, format: Optional[str] = None) -> _Execable:
        self.get_calls.append({'userId': userId, 'id': id, 'format': format})
        return _Execable(self.get_results[id])

    def modify(self, userId: str, id: str, body: Dict[str, Any]) -> _Execable:
        self.modify_calls.append({'userId': userId, 'id': id, 'body': body})
        return _Execable({})

    def trash(self, userId: str, id: str) -> _Execable:
        self.trash_calls.append(id)
        return _Execable({})

    def send(self, userId: str, body: Dict[str, Any]) -> _Execable:
        self.send_calls.append({'userId': userId, 'body': body})
        return _Execable({})


@dataclass
class FakeGmailService:
    messages_resource: FakeMessagesResource

    def users(self) -> 'FakeGmailService':
        return self

    def messages(self) -> FakeMessagesResource:
        return self.messages_resource


def _gmail_message(
    msg_id: str,
    subject: str = 'Hi',
    sender: str = 'a@example.com',
    date_str: Optional[str] = 'Mon, 01 Jan 2024 12:00:00 GMT',
    to: str = 'b@example.com',
    unread: bool = True,
    html_body: Optional[str] = None,
    plain_body: Optional[str] = None,
) -> Dict[str, Any]:
    headers = [
        {'name': 'Subject', 'value': subject},
        {'name': 'From', 'value': sender},
        {'name': 'To', 'value': to},
    ]
    if date_str is not None:
        headers.append({'name': 'Date', 'value': date_str})

    parts = []
    if html_body is not None:
        parts.append({'mimeType': 'text/html', 'body': {'data': base64.urlsafe_b64encode(html_body.encode('utf-8')).decode('utf-8')}})
    if plain_body is not None:
        parts.append({'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(plain_body.encode('utf-8')).decode('utf-8')}})

    payload: Dict[str, Any] = {'headers': headers}
    if parts:
        payload['parts'] = parts

    return {
        'id': msg_id,
        'payload': payload,
        'labelIds': ['UNREAD'] if unread else ['INBOX'],
    }


class _ExplodingMessagesResource:
    def list(self, **kwargs: Any) -> Any:
        raise RuntimeError('api down')

    def get(self, **kwargs: Any) -> Any:
        raise RuntimeError('api down')

    def modify(self, **kwargs: Any) -> Any:
        raise RuntimeError('api down')

    def trash(self, **kwargs: Any) -> Any:
        raise RuntimeError('api down')

    def send(self, **kwargs: Any) -> Any:
        raise RuntimeError('api down')


class _ExplodingService:
    def users(self) -> '_ExplodingService':
        return self

    def messages(self) -> _ExplodingMessagesResource:
        return _ExplodingMessagesResource()


# --- __init__ ----------------------------------------------------------------

def test_init_sets_up_oauth_with_shared_token_manager_and_no_service(tmp_path: Path) -> None:
    provider = _provider(tmp_path)

    assert provider.credentials_path == 'creds.json'
    assert provider.redirect_uri == 'http://x/callback'
    assert provider.oauth.token_manager is provider.token_manager
    assert provider._service is None


# --- authenticate --------------------------------------------------------------

def test_authenticate_returns_false_when_no_valid_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: None)

    assert provider.authenticate('user1') is False


def test_authenticate_returns_false_when_token_data_missing_access_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'refresh_token': 'r'})

    assert provider.authenticate('user1') is False


def test_authenticate_succeeds_fetches_user_info_and_builds_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    stored_token = {
        'token': 'at', 'refresh_token': 'r', 'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'cid', 'client_secret': 'csecret', 'scopes': ['s'],
    }
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: stored_token)
    monkeypatch.setattr(provider.oauth, 'get_user_info', lambda token_data: {'emailAddress': 'user@example.com'})
    fake_service = object()
    _patch_service_build(monkeypatch, fake_service)

    result = provider.authenticate('user1')

    assert result is True
    assert provider._service is fake_service
    assert provider.token_manager.get_user_info('user1') == {'emailAddress': 'user@example.com'}


def test_authenticate_skips_user_info_fetch_when_already_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    provider.token_manager.store_user_info('user1', {'emailAddress': 'cached@example.com'})
    stored_token = {'token': 'at', 'refresh_token': 'r', 'token_uri': 'u', 'client_id': 'c', 'client_secret': 's', 'scopes': []}
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: stored_token)

    def fail_if_called(token_data: Any) -> Any:
        raise AssertionError('get_user_info should not be called when user info is already cached')

    monkeypatch.setattr(provider.oauth, 'get_user_info', fail_if_called)
    _patch_service_build(monkeypatch, object())

    assert provider.authenticate('user1') is True


def test_authenticate_still_succeeds_when_user_info_fetch_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    stored_token = {'token': 'at', 'refresh_token': 'r', 'token_uri': 'u', 'client_id': 'c', 'client_secret': 's', 'scopes': []}
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: stored_token)

    def raise_error(token_data: Any) -> Any:
        raise RuntimeError('user info API down')

    monkeypatch.setattr(provider.oauth, 'get_user_info', raise_error)
    _patch_service_build(monkeypatch, object())

    assert provider.authenticate('user1') is True


def test_authenticate_returns_false_when_service_build_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    stored_token = {'token': 'at', 'refresh_token': 'r', 'token_uri': 'u', 'client_id': 'c', 'client_secret': 's', 'scopes': []}
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: stored_token)
    monkeypatch.setattr(provider.oauth, 'get_user_info', lambda token_data: {})
    monkeypatch.setattr(google_credentials_module, 'Credentials', lambda **kwargs: object())

    def raise_build(*a: Any, **k: Any) -> Any:
        raise RuntimeError('build failed')

    monkeypatch.setattr(googleapiclient_discovery_module, 'build', raise_build)

    assert provider.authenticate('user1') is False


# --- get_messages --------------------------------------------------------------

def test_get_messages_returns_empty_list_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert provider.get_messages('user1') == []


def test_get_messages_parses_html_body_subject_sender_and_unread_flag(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    msg = _gmail_message('m1', subject='Hello', sender='alice@example.com', to='bob@example.com', unread=True, html_body='<p>Hi</p>')
    provider._service = FakeGmailService(FakeMessagesResource(list_results={'messages': [{'id': 'm1'}]}, get_results={'m1': msg}))

    messages = provider.get_messages('user1')

    assert len(messages) == 1
    message = messages[0]
    assert message.id == 'm1'
    assert message.subject == 'Hello'
    assert message.sender == 'alice@example.com'
    assert message.recipients == ['bob@example.com']
    assert message.body == '<p>Hi</p>'
    assert message.is_read is False
    assert message.provider == 'gmail'


def test_get_messages_falls_back_to_plain_text_when_no_html_part(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    msg = _gmail_message('m1', unread=False, plain_body='hello there')
    provider._service = FakeGmailService(FakeMessagesResource(list_results={'messages': [{'id': 'm1'}]}, get_results={'m1': msg}))

    messages = provider.get_messages('user1')

    assert messages[0].body == 'hello there'
    assert messages[0].is_read is True


def test_get_messages_reads_body_directly_from_payload_when_no_parts(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    msg = {
        'id': 'm1',
        'payload': {
            'headers': [{'name': 'Subject', 'value': 'S'}, {'name': 'From', 'value': 'a@example.com'}],
            'mimeType': 'text/html',
            'body': {'data': base64.urlsafe_b64encode(b'<b>direct</b>').decode('utf-8')},
        },
        'labelIds': ['INBOX'],
    }
    provider._service = FakeGmailService(FakeMessagesResource(list_results={'messages': [{'id': 'm1'}]}, get_results={'m1': msg}))

    messages = provider.get_messages('user1')

    assert messages[0].body == '<b>direct</b>'


def test_get_messages_uses_current_time_when_date_header_missing(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    msg = _gmail_message('m1', date_str=None)
    provider._service = FakeGmailService(FakeMessagesResource(list_results={'messages': [{'id': 'm1'}]}, get_results={'m1': msg}))

    before = datetime.now(timezone.utc)
    messages = provider.get_messages('user1')
    after = datetime.now(timezone.utc)

    assert before <= messages[0].received_date <= after


def test_get_messages_unread_only_adds_query_filter(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    resource = FakeMessagesResource(list_results={'messages': []}, get_results={})
    provider._service = FakeGmailService(resource)

    provider.get_messages('user1', unread_only=True)

    assert resource.list_calls[0]['q'] == 'in:inbox is:unread'


def test_get_messages_defaults_to_inbox_folder(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    resource = FakeMessagesResource(list_results={'messages': []}, get_results={})
    provider._service = FakeGmailService(resource)

    provider.get_messages('user1')

    assert resource.list_calls[0]['q'] == 'in:inbox'


def test_get_messages_queries_the_requested_folder(tmp_path: Path) -> None:
    """Regression test: get_messages() used to hardcode 'in:inbox'
    regardless of the folder argument, silently ignoring it."""
    provider = _provider(tmp_path)
    resource = FakeMessagesResource(list_results={'messages': []}, get_results={})
    provider._service = FakeGmailService(resource)

    provider.get_messages('user1', folder='sent')

    assert resource.list_calls[0]['q'] == 'in:sent'


def test_get_messages_queries_requested_folder_with_unread_filter(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    resource = FakeMessagesResource(list_results={'messages': []}, get_results={})
    provider._service = FakeGmailService(resource)

    provider.get_messages('user1', folder='sent', unread_only=True)

    assert resource.list_calls[0]['q'] == 'in:sent is:unread'


def test_sent_folder_class_attribute() -> None:
    assert GmailProvider.SENT_FOLDER == 'sent'


def test_get_messages_returns_empty_list_on_api_exception(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider._service = _ExplodingService()

    assert provider.get_messages('user1') == []


# --- get_message --------------------------------------------------------------

def test_get_message_returns_none_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert provider.get_message('user1', 'm1') is None


def test_get_message_returns_parsed_message_with_body(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    msg = _gmail_message('m1', subject='Hello', sender='alice@example.com', unread=False, html_body='<p>Hi</p>')
    resource = FakeMessagesResource(get_results={'m1': msg})
    provider._service = FakeGmailService(resource)

    message = provider.get_message('user1', 'm1')

    assert message is not None
    assert message.id == 'm1'
    assert message.subject == 'Hello'
    assert message.body == '<p>Hi</p>'
    assert message.is_read is True
    assert message.provider == 'gmail'
    assert resource.get_calls == [{'userId': 'me', 'id': 'm1', 'format': 'full'}]


def test_get_message_returns_none_when_not_found(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    resource = FakeMessagesResource(get_results={})  # 'm1' not present -> KeyError inside get()
    provider._service = FakeGmailService(resource)

    assert provider.get_message('user1', 'm1') is None


def test_get_message_returns_none_on_api_exception(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider._service = _ExplodingService()

    assert provider.get_message('user1', 'm1') is None


# --- send_message --------------------------------------------------------------

def test_send_message_returns_false_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert provider.send_message('user1', 'to@example.com', 'Subj', 'Body') is False


def test_send_message_sends_raw_base64_encoded_mime_with_cc_and_bcc(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    resource = FakeMessagesResource()
    provider._service = FakeGmailService(resource)

    result = provider.send_message(
        'user1', 'to@example.com', 'Subj', '<p>Body</p>',
        cc=['cc@example.com'], bcc=['bcc@example.com'],
    )

    assert result is True
    assert len(resource.send_calls) == 1
    raw = resource.send_calls[0]['body']['raw']
    decoded = base64.urlsafe_b64decode(raw).decode('utf-8')
    assert 'to@example.com' in decoded
    assert 'cc@example.com' in decoded
    assert 'bcc@example.com' in decoded
    assert 'Subj' in decoded


def test_send_message_returns_false_on_api_exception(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider._service = _ExplodingService()

    assert provider.send_message('user1', 'to@example.com', 'S', 'B') is False


# --- mark_as_read --------------------------------------------------------------

def test_mark_as_read_returns_false_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert provider.mark_as_read('user1', ['m1']) is False


def test_mark_as_read_calls_modify_removing_unread_label_for_each_id(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    resource = FakeMessagesResource()
    provider._service = FakeGmailService(resource)

    result = provider.mark_as_read('user1', ['m1', 'm2'])

    assert result is True
    assert [c['id'] for c in resource.modify_calls] == ['m1', 'm2']
    assert resource.modify_calls[0]['body'] == {'removeLabelIds': ['UNREAD']}


def test_mark_as_read_returns_false_on_api_exception(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider._service = _ExplodingService()

    assert provider.mark_as_read('user1', ['m1']) is False


# --- delete_messages -------------------------------------------------------------

def test_delete_messages_returns_false_when_authentication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider, 'authenticate', lambda user_id: False)

    assert provider.delete_messages('user1', ['m1']) is False


def test_delete_messages_trashes_each_id(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    resource = FakeMessagesResource()
    provider._service = FakeGmailService(resource)

    result = provider.delete_messages('user1', ['m1', 'm2'])

    assert result is True
    assert resource.trash_calls == ['m1', 'm2']


def test_delete_messages_returns_false_on_api_exception(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider._service = _ExplodingService()

    assert provider.delete_messages('user1', ['m1']) is False


# --- block_senders --------------------------------------------------------------

def test_block_senders_always_returns_empty_list(tmp_path: Path) -> None:
    """Gmail has no equivalent to Microsoft Graph's inbox-rule mechanism in
    this codebase -- confirmed unsupported, not a stand-in for "not
    implemented yet". UnifiedEmailServer.block_senders() still locally
    suppresses these senders regardless (see test_unified_email_server.py)."""
    provider = _provider(tmp_path)

    assert provider.block_senders('user1', ['Alice', 'Bob']) == []


def test_block_senders_returns_empty_list_for_empty_input(tmp_path: Path) -> None:
    provider = _provider(tmp_path)

    assert provider.block_senders('user1', []) == []
