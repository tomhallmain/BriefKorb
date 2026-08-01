"""Tests for email_server/providers/microsoft/microsoft.py's MicrosoftGraphProvider.

Unlike the Gmail provider, this module does plain module-level ``import
requests`` / ``import time``, so patching ``microsoft_provider_module.requests.*``
and ``microsoft_provider_module.time.sleep`` is sufficient -- no local-import
shadowing to work around. OAuth internals (MSAL, token refresh) are already
covered by test_microsoft_oauth.py, so here ``provider.oauth.get_valid_token``
/ ``get_user_info`` are patched directly rather than re-faking MSAL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from email_server.auth import TokenManager
from email_server.providers.microsoft import microsoft as microsoft_provider_module
from email_server.providers.microsoft.microsoft import MicrosoftGraphProvider


def _provider(tmp_path: Path) -> MicrosoftGraphProvider:
    token_manager = TokenManager(storage_path=str(tmp_path))
    return MicrosoftGraphProvider(
        client_id='cid', client_secret='csecret', tenant_id='tenant-id',
        redirect_uri='http://x/callback', token_manager=token_manager,
    )


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: Optional[Dict[str, Any]] = None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.ok = status_code < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def json(self) -> Dict[str, Any]:
        return self._json_data


# --- __init__ ----------------------------------------------------------------

def test_init_sets_up_oauth_with_shared_token_manager(tmp_path: Path) -> None:
    provider = _provider(tmp_path)

    assert provider.base_url == "https://graph.microsoft.com/v1.0"
    assert provider.oauth.token_manager is provider.token_manager


# --- authenticate --------------------------------------------------------------

def test_authenticate_returns_false_when_no_valid_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: None)

    assert provider.authenticate('user1') is False


def test_authenticate_fetches_and_caches_user_info_using_access_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    captured: Dict[str, Any] = {}

    def fake_get_user_info(token: str) -> Dict[str, Any]:
        captured['token'] = token
        return {'mail': 'user@example.com'}

    monkeypatch.setattr(provider.oauth, 'get_user_info', fake_get_user_info)

    result = provider.authenticate('user1')

    assert result is True
    assert captured['token'] == 'at'
    assert provider.token_manager.get_user_info('user1') == {'mail': 'user@example.com'}


def test_authenticate_falls_back_to_token_key_when_access_token_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'token': 'legacy-token'})
    captured: Dict[str, Any] = {}
    monkeypatch.setattr(provider.oauth, 'get_user_info', lambda token: captured.setdefault('token', token) or {'mail': 'x'})

    provider.authenticate('user1')

    assert captured['token'] == 'legacy-token'


def test_authenticate_skips_user_info_fetch_when_no_access_token_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'msal_cache': 'x'})

    def fail_if_called(token: str) -> Any:
        raise AssertionError('get_user_info should not be called without an access token')

    monkeypatch.setattr(provider.oauth, 'get_user_info', fail_if_called)

    assert provider.authenticate('user1') is True


def test_authenticate_skips_user_info_fetch_when_already_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    provider.token_manager.store_user_info('user1', {'mail': 'cached@example.com'})
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})

    def fail_if_called(token: str) -> Any:
        raise AssertionError('get_user_info should not be called when user info is already cached')

    monkeypatch.setattr(provider.oauth, 'get_user_info', fail_if_called)

    assert provider.authenticate('user1') is True


def test_authenticate_still_succeeds_when_user_info_fetch_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})

    def raise_error(token: str) -> Any:
        raise RuntimeError('graph api down')

    monkeypatch.setattr(provider.oauth, 'get_user_info', raise_error)

    assert provider.authenticate('user1') is True


# --- _get_headers --------------------------------------------------------------

def test_get_headers_raises_when_no_valid_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: None)

    with pytest.raises(RuntimeError, match='Not authenticated'):
        provider._get_headers('user1')


def test_get_headers_raises_when_token_missing_access_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'msal_cache': 'x'})

    with pytest.raises(RuntimeError, match='Invalid token data'):
        provider._get_headers('user1')


def test_get_headers_builds_bearer_auth_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})

    headers = provider._get_headers('user1')

    assert headers == {'Authorization': 'Bearer at', 'Content-Type': 'application/json'}


# --- _retry_request --------------------------------------------------------------

def test_retry_request_returns_response_on_first_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(microsoft_provider_module.time, 'sleep', lambda s: None)
    fake_response = _FakeResponse(status_code=200)
    calls: List[int] = []

    def request_func() -> _FakeResponse:
        calls.append(1)
        return fake_response

    result = provider._retry_request(request_func)

    assert result is fake_response
    assert len(calls) == 1


def test_retry_request_retries_on_failure_then_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(microsoft_provider_module.time, 'sleep', lambda s: None)
    responses = [_FakeResponse(status_code=500), _FakeResponse(status_code=200)]

    result = provider._retry_request(lambda: responses.pop(0), max_retries=3)

    assert result is not None
    assert result.status_code == 200


def test_retry_request_returns_last_response_after_exhausting_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(microsoft_provider_module.time, 'sleep', lambda s: None)
    fake_response = _FakeResponse(status_code=500)

    result = provider._retry_request(lambda: fake_response, max_retries=2)

    assert result is fake_response


def test_retry_request_returns_none_when_request_func_always_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(microsoft_provider_module.time, 'sleep', lambda s: None)

    def always_raise() -> Any:
        raise RuntimeError('network error')

    result = provider._retry_request(always_raise, max_retries=2)

    assert result is None


# --- get_messages --------------------------------------------------------------

def test_get_messages_returns_empty_list_when_not_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: None)

    assert provider.get_messages('user1') == []


def test_get_messages_parses_html_body_sender_and_recipients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    list_response = _FakeResponse(json_data={'value': [{'id': 'm1'}]})
    full_msg = {
        'id': 'm1', 'subject': 'Hello', 'isRead': False,
        'from': {'emailAddress': {'address': 'alice@example.com'}},
        'toRecipients': [{'emailAddress': {'address': 'bob@example.com'}}],
        'receivedDateTime': '2024-01-01T12:00:00Z',
        'body': {'content': '<p>Hi</p>', 'contentType': 'html'},
    }
    detail_response = _FakeResponse(json_data=full_msg)

    def fake_get(url: str, headers: Any = None, params: Any = None) -> _FakeResponse:
        return list_response if url.endswith('/messages') else detail_response

    monkeypatch.setattr(microsoft_provider_module.requests, 'get', fake_get)

    messages = provider.get_messages('user1')

    assert len(messages) == 1
    message = messages[0]
    assert message.subject == 'Hello'
    assert message.sender == 'alice@example.com'
    assert message.recipients == ['bob@example.com']
    assert message.body == '<p>Hi</p>'
    assert message.is_read is False
    assert message.provider == 'microsoft'


def test_get_messages_converts_plain_text_body_to_escaped_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    list_response = _FakeResponse(json_data={'value': [{'id': 'm1'}]})
    full_msg = {
        'id': 'm1', 'subject': 'S', 'isRead': True,
        'from': {'emailAddress': {'address': 'a@example.com'}},
        'receivedDateTime': '2024-01-01T12:00:00Z',
        'body': {'content': 'line1\nline2 & more', 'contentType': 'text'},
    }
    detail_response = _FakeResponse(json_data=full_msg)

    def fake_get(url: str, headers: Any = None, params: Any = None) -> _FakeResponse:
        return list_response if url.endswith('/messages') else detail_response

    monkeypatch.setattr(microsoft_provider_module.requests, 'get', fake_get)

    messages = provider.get_messages('user1')

    assert messages[0].body == 'line1<br>line2 &amp; more'


def test_get_messages_falls_back_to_body_preview_when_body_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    list_response = _FakeResponse(json_data={'value': [{'id': 'm1'}]})
    full_msg = {
        'id': 'm1', 'subject': 'S', 'isRead': True,
        'from': {'emailAddress': {'address': 'a@example.com'}},
        'receivedDateTime': '2024-01-01T12:00:00Z',
        'bodyPreview': 'preview text',
    }
    detail_response = _FakeResponse(json_data=full_msg)

    def fake_get(url: str, headers: Any = None, params: Any = None) -> _FakeResponse:
        return list_response if url.endswith('/messages') else detail_response

    monkeypatch.setattr(microsoft_provider_module.requests, 'get', fake_get)

    messages = provider.get_messages('user1')

    assert messages[0].body == 'preview text'


def test_get_messages_unread_only_sets_filter_query_param(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    captured: Dict[str, Any] = {}

    def fake_get(url: str, headers: Any = None, params: Any = None) -> _FakeResponse:
        captured['params'] = params
        return _FakeResponse(json_data={'value': []})

    monkeypatch.setattr(microsoft_provider_module.requests, 'get', fake_get)

    provider.get_messages('user1', unread_only=True)

    assert captured['params']['$filter'] == 'isRead eq false'


def test_get_messages_skips_message_when_detail_fetch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    list_response = _FakeResponse(json_data={'value': [{'id': 'm1'}, {'id': 'm2'}]})
    good_msg = {
        'id': 'm2', 'subject': 'Good', 'isRead': True,
        'from': {'emailAddress': {'address': 'a@example.com'}},
        'receivedDateTime': '2024-01-01T12:00:00Z',
        'bodyPreview': 'ok',
    }

    def fake_get(url: str, headers: Any = None, params: Any = None) -> _FakeResponse:
        if url.endswith('/messages'):
            return list_response
        if url.endswith('/m1'):
            return _FakeResponse(status_code=500, text='boom')
        return _FakeResponse(json_data=good_msg)

    monkeypatch.setattr(microsoft_provider_module.requests, 'get', fake_get)

    messages = provider.get_messages('user1')

    assert [m.id for m in messages] == ['m2']


def test_get_messages_returns_empty_list_on_top_level_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})

    def raise_get(url: str, headers: Any = None, params: Any = None) -> Any:
        raise RuntimeError('network down')

    monkeypatch.setattr(microsoft_provider_module.requests, 'get', raise_get)

    assert provider.get_messages('user1') == []


# --- send_message --------------------------------------------------------------

def test_send_message_returns_false_when_not_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: None)

    assert provider.send_message('user1', 'to@example.com', 'S', 'B') is False


def test_send_message_posts_expected_payload_with_cc_and_bcc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    captured: Dict[str, Any] = {}

    def fake_post(url: str, headers: Any = None, json: Any = None) -> _FakeResponse:
        captured['url'] = url
        captured['json'] = json
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(microsoft_provider_module.requests, 'post', fake_post)

    result = provider.send_message('user1', 'to@example.com', 'Subj', '<p>Body</p>', cc='cc@example.com', bcc='bcc@example.com')

    assert result is True
    assert captured['url'] == f"{provider.base_url}/me/sendMail"
    message = captured['json']['message']
    assert message['subject'] == 'Subj'
    assert message['toRecipients'] == [{'emailAddress': {'address': 'to@example.com'}}]
    assert message['ccRecipients'] == [{'emailAddress': {'address': 'cc@example.com'}}]
    assert message['bccRecipients'] == [{'emailAddress': {'address': 'bcc@example.com'}}]


def test_send_message_returns_false_on_http_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    monkeypatch.setattr(microsoft_provider_module.requests, 'post', lambda url, headers=None, json=None: _FakeResponse(status_code=500))

    assert provider.send_message('user1', 'to@example.com', 'S', 'B') is False


# --- mark_as_read --------------------------------------------------------------

def test_mark_as_read_returns_true_immediately_for_empty_list(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assert provider.mark_as_read('user1', []) is True


def test_mark_as_read_returns_false_when_not_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: None)

    assert provider.mark_as_read('user1', ['m1']) is False


def test_mark_as_read_patches_each_message_and_returns_true_on_full_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    monkeypatch.setattr(microsoft_provider_module.time, 'sleep', lambda s: None)
    patched_urls: List[str] = []

    def fake_patch(url: str, headers: Any = None, json: Any = None) -> _FakeResponse:
        patched_urls.append(url)
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(microsoft_provider_module.requests, 'patch', fake_patch)

    result = provider.mark_as_read('user1', ['m1', 'm2'])

    assert result is True
    assert sorted(patched_urls) == sorted([
        f"{provider.base_url}/me/messages/m1",
        f"{provider.base_url}/me/messages/m2",
    ])


def test_mark_as_read_returns_true_when_some_succeed_and_some_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    monkeypatch.setattr(microsoft_provider_module.time, 'sleep', lambda s: None)

    def fake_patch(url: str, headers: Any = None, json: Any = None) -> _FakeResponse:
        return _FakeResponse(status_code=200) if url.endswith('m1') else _FakeResponse(status_code=500)

    monkeypatch.setattr(microsoft_provider_module.requests, 'patch', fake_patch)

    assert provider.mark_as_read('user1', ['m1', 'm2']) is True


def test_mark_as_read_returns_false_when_all_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    monkeypatch.setattr(microsoft_provider_module.time, 'sleep', lambda s: None)
    monkeypatch.setattr(microsoft_provider_module.requests, 'patch', lambda url, headers=None, json=None: _FakeResponse(status_code=500))

    assert provider.mark_as_read('user1', ['m1', 'm2']) is False


# --- delete_messages -------------------------------------------------------------

def test_delete_messages_returns_true_immediately_for_empty_list(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assert provider.delete_messages('user1', []) is True


def test_delete_messages_returns_false_when_not_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: None)

    assert provider.delete_messages('user1', ['m1']) is False


def test_delete_messages_deletes_each_message_and_returns_true_on_full_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    monkeypatch.setattr(microsoft_provider_module.time, 'sleep', lambda s: None)
    deleted_urls: List[str] = []

    def fake_delete(url: str, headers: Any = None) -> _FakeResponse:
        deleted_urls.append(url)
        return _FakeResponse(status_code=204)

    monkeypatch.setattr(microsoft_provider_module.requests, 'delete', fake_delete)

    result = provider.delete_messages('user1', ['m1', 'm2'])

    assert result is True
    assert sorted(deleted_urls) == sorted([
        f"{provider.base_url}/me/messages/m1",
        f"{provider.base_url}/me/messages/m2",
    ])


def test_delete_messages_returns_false_when_all_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    monkeypatch.setattr(microsoft_provider_module.time, 'sleep', lambda s: None)
    monkeypatch.setattr(microsoft_provider_module.requests, 'delete', lambda url, headers=None: _FakeResponse(status_code=500))

    assert provider.delete_messages('user1', ['m1']) is False
