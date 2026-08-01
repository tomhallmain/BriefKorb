from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from django_app.calendar import services as calendar_services_module
from django_app.calendar.services import CalendarService, WINDOWS_TO_IANA_MAPPINGS, get_iana_from_windows
from email_server.config import EmailServerConfig, ProviderConfig


def test_known_windows_name_maps_to_iana() -> None:
    assert get_iana_from_windows('Pacific Standard Time') == 'America/Los_Angeles'


def test_every_mapping_entry_round_trips() -> None:
    for windows_name, iana_name in WINDOWS_TO_IANA_MAPPINGS.items():
        assert get_iana_from_windows(windows_name) == iana_name


def test_unknown_name_already_iana_shaped_passes_through() -> None:
    assert get_iana_from_windows('Region/City') == 'Region/City'


def test_unknown_name_not_iana_shaped_falls_back_to_utc() -> None:
    assert get_iana_from_windows('Some Made Up Timezone') == 'UTC'


def test_empty_string_falls_back_to_utc() -> None:
    assert get_iana_from_windows('') == 'UTC'


# --- CalendarService ---------------------------------------------------------
#
# __init__ resolves config via EmailServerConfig.resolve_path(), which honors
# BRIEFKORB_CONFIG_PATH ahead of the real app_dir-derived path (same as the
# Django view tests) -- so writing to os.environ['BRIEFKORB_CONFIG_PATH'] is
# enough isolation; no app_dir patch is needed here.

class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: Optional[Dict[str, Any]] = None) -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def json(self) -> Dict[str, Any]:
        return self._json_data


def _write_ms_config(tmp_path: Path, **overrides: Any) -> None:
    defaults: Dict[str, Any] = dict(
        enabled=True, client_id='cid', client_secret='csecret', tenant_id='tid',
        redirect_uri='http://x/ms-callback', scopes=['scope1'],
    )
    defaults.update(overrides)
    config = EmailServerConfig(
        microsoft=ProviderConfig(**defaults),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(tmp_path / 'tokens'),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])


def _service(tmp_path: Path) -> CalendarService:
    _write_ms_config(tmp_path)
    return CalendarService('user1')


def test_init_raises_file_not_found_when_config_missing() -> None:
    with pytest.raises(FileNotFoundError):
        CalendarService('user1')


def test_init_raises_value_error_when_microsoft_disabled(tmp_path: Path) -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=False), gmail=ProviderConfig(enabled=False),
        token_storage_path=str(tmp_path / 'tokens'),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])

    with pytest.raises(ValueError):
        CalendarService('user1')


def test_init_succeeds_with_valid_config(tmp_path: Path) -> None:
    service = _service(tmp_path)

    assert service.user_id == 'user1'
    assert service.base_url == 'https://graph.microsoft.com/v1.0'


def test_get_headers_raises_when_no_valid_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service.microsoft_oauth, 'get_valid_token', lambda user_id: None)

    with pytest.raises(ValueError):
        service._get_headers()


def test_get_headers_raises_when_token_missing_access_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service.microsoft_oauth, 'get_valid_token', lambda user_id: {'msal_cache': 'x'})

    with pytest.raises(ValueError):
        service._get_headers()


def test_get_headers_builds_bearer_header_and_optional_timezone_prefer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service.microsoft_oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})

    headers = service._get_headers()
    assert headers == {'Authorization': 'Bearer at', 'Content-Type': 'application/json'}

    headers_with_tz = service._get_headers(timezone='America/New_York')
    assert headers_with_tz['Prefer'] == 'outlook.timezone="America/New_York"'


def test_get_user_info_raises_when_no_valid_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service.microsoft_oauth, 'get_valid_token', lambda user_id: None)

    with pytest.raises(ValueError):
        service.get_user_info()


def test_get_user_info_returns_oauth_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service.microsoft_oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    captured: Dict[str, Any] = {}

    def fake_get_user_info(token: str) -> Dict[str, Any]:
        captured['token'] = token
        return {'mailboxSettings': {'timeZone': 'UTC'}}

    monkeypatch.setattr(service.microsoft_oauth, 'get_user_info', fake_get_user_info)

    result = service.get_user_info()

    assert captured['token'] == 'at'
    assert result == {'mailboxSettings': {'timeZone': 'UTC'}}


def test_get_calendar_events_builds_query_and_returns_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service.microsoft_oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    captured: Dict[str, Any] = {}

    def fake_get(url: str, headers: Any = None, params: Any = None) -> FakeResponse:
        captured['url'] = url
        captured['headers'] = headers
        captured['params'] = params
        return FakeResponse(json_data={'value': [{'subject': 'Standup'}]})

    monkeypatch.setattr(calendar_services_module.requests, 'get', fake_get)

    start = datetime(2024, 1, 1, 0, 0, 0)
    end = datetime(2024, 1, 8, 0, 0, 0)
    result = service.get_calendar_events(start, end, 'America/New_York')

    assert result == {'value': [{'subject': 'Standup'}]}
    assert captured['url'] == f'{service.base_url}/me/calendarview'
    assert captured['headers']['Prefer'] == 'outlook.timezone="America/New_York"'
    assert captured['params']['startDateTime'] == start.isoformat(timespec='seconds')
    assert captured['params']['endDateTime'] == end.isoformat(timespec='seconds')


def test_get_calendar_events_propagates_http_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service.microsoft_oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    monkeypatch.setattr(calendar_services_module.requests, 'get', lambda url, headers=None, params=None: FakeResponse(status_code=500))

    with pytest.raises(RuntimeError):
        service.get_calendar_events(datetime(2024, 1, 1), datetime(2024, 1, 8), 'UTC')


def test_create_event_posts_expected_payload_with_attendees_and_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service.microsoft_oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    captured: Dict[str, Any] = {}

    def fake_post(url: str, headers: Any = None, json: Any = None) -> FakeResponse:
        captured['url'] = url
        captured['json'] = json
        return FakeResponse(json_data={'id': 'event-1'})

    monkeypatch.setattr(calendar_services_module.requests, 'post', fake_post)

    start = datetime(2024, 1, 1, 9, 0, 0)
    end = datetime(2024, 1, 1, 10, 0, 0)
    result = service.create_event(
        subject='Standup', start=start, end=end, timezone='America/New_York',
        attendees=[' a@example.com ', 'b@example.com'], body='Daily sync',
    )

    assert result == {'id': 'event-1'}
    assert captured['url'] == f'{service.base_url}/me/events'
    payload = captured['json']
    assert payload['subject'] == 'Standup'
    assert payload['start'] == {'dateTime': start.isoformat(timespec='seconds'), 'timeZone': 'America/New_York'}
    assert payload['attendees'] == [
        {'type': 'required', 'emailAddress': {'address': 'a@example.com'}},
        {'type': 'required', 'emailAddress': {'address': 'b@example.com'}},
    ]
    assert payload['body'] == {'contentType': 'text', 'content': 'Daily sync'}


def test_create_event_omits_optional_fields_when_not_provided(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service.microsoft_oauth, 'get_valid_token', lambda user_id: {'access_token': 'at'})
    captured: Dict[str, Any] = {}

    def fake_post(url: str, headers: Any = None, json: Any = None) -> FakeResponse:
        captured['json'] = json
        return FakeResponse(json_data={'id': 'event-1'})

    monkeypatch.setattr(calendar_services_module.requests, 'post', fake_post)

    service.create_event(subject='S', start=datetime(2024, 1, 1, 9), end=datetime(2024, 1, 1, 10), timezone='UTC')

    assert 'attendees' not in captured['json']
    assert 'body' not in captured['json']
