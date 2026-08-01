"""Tests for django_app/calendar/views.py.

CalendarService (imported into this view module from .services) wraps live
Microsoft Graph API calls, so every test patches
`calendar_views_module.CalendarService` with an in-memory fake rather than
letting the view construct a real one -- CalendarService itself already has
its own unit coverage (test_calendar_services.py). get_iana_from_windows is
a pure lookup with no I/O, so it's left real.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from django.test import Client
from django.urls import reverse

from django_app.calendar import views as calendar_views_module
from email_server.auth import TokenManager
from email_server.config import EmailServerConfig, ProviderConfig


def _write_config(tmp_path: Path, token_dir: Path) -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=True),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(token_dir),
    )
    config.save(os.environ['BRIEFKORB_CONFIG_PATH'])


def _authenticate_via_session(client: Client, email: str = 'user@example.com') -> None:
    session = client.session
    session['user'] = {'is_authenticated': True, 'email': email}
    session.save()


@dataclass
class FakeCalendarService:
    user_info: Dict[str, Any] = field(default_factory=lambda: {'mailboxSettings': {'timeZone': 'UTC'}})
    events_response: Dict[str, Any] = field(default_factory=lambda: {'value': []})
    create_event_calls: List[Dict[str, Any]] = field(default_factory=list)
    raise_on_get_events: Optional[Exception] = None
    raise_on_create_event: Optional[Exception] = None
    constructed_with: List[str] = field(default_factory=list)

    def get_user_info(self) -> Dict[str, Any]:
        return self.user_info

    def get_calendar_events(self, start, end, timezone) -> Dict[str, Any]:
        if self.raise_on_get_events:
            raise self.raise_on_get_events
        return self.events_response

    def create_event(self, subject, start, end, timezone, attendees=None, body=None) -> None:
        if self.raise_on_create_event:
            raise self.raise_on_create_event
        self.create_event_calls.append({
            'subject': subject, 'start': start, 'end': end,
            'timezone': timezone, 'attendees': attendees, 'body': body,
        })


def _patch_calendar_service(monkeypatch: pytest.MonkeyPatch, fake: FakeCalendarService) -> None:
    def factory(user_id: str) -> FakeCalendarService:
        fake.constructed_with.append(user_id)
        return fake

    monkeypatch.setattr(calendar_views_module, 'CalendarService', factory)


# --- calendar_view --------------------------------------------------------------

def test_calendar_view_shows_unauthenticated_state_with_no_session_or_config(client: Client) -> None:
    response = client.get(reverse('django_app.calendar:calendar'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is False
    assert response.context['events'] == []


def test_calendar_view_renders_events_for_session_authenticated_user(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeCalendarService(events_response={'value': [
        {'start': {'dateTime': '2024-01-01T09:00:00Z'}, 'end': {'dateTime': '2024-01-01T10:00:00Z'}, 'subject': 'Standup'},
    ]})
    _patch_calendar_service(monkeypatch, fake)

    response = client.get(reverse('django_app.calendar:calendar'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is True
    assert len(response.context['events']) == 1
    assert fake.constructed_with == ['user@example.com']
    from datetime import datetime
    assert isinstance(response.context['events'][0]['start']['dateTime'], datetime)


def test_calendar_view_falls_back_to_error_state_when_service_raises(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeCalendarService(raise_on_get_events=RuntimeError('graph api down'))
    _patch_calendar_service(monkeypatch, fake)

    response = client.get(reverse('django_app.calendar:calendar'))

    assert response.context['is_authenticated'] is False
    assert 'graph api down' in response.context['error']


def test_calendar_view_authenticates_via_token_manager_fallback(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token_dir = tmp_path / 'tokens'
    _write_config(tmp_path, token_dir)
    token_manager = TokenManager(storage_path=str(token_dir))
    token_manager.store_token('desktop@example.com', {'access_token': 'at'})
    fake = FakeCalendarService()
    _patch_calendar_service(monkeypatch, fake)

    response = client.get(reverse('django_app.calendar:calendar'))

    assert response.context['is_authenticated'] is True
    assert fake.constructed_with == ['desktop@example.com']


# --- new_event_view --------------------------------------------------------------

def test_new_event_view_redirects_to_calendar_when_unauthenticated(client: Client) -> None:
    response = client.get(reverse('django_app.calendar:new_event'))

    assert response.status_code == 302
    assert response.url == reverse('django_app.calendar:calendar')


def test_new_event_view_get_renders_form_for_authenticated_user(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeCalendarService()
    _patch_calendar_service(monkeypatch, fake)

    response = client.get(reverse('django_app.calendar:new_event'))

    assert response.status_code == 200
    assert response.context['is_authenticated'] is True


def test_new_event_view_get_redirects_when_user_info_lookup_fails(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeCalendarService()

    def raise_get_user_info():
        raise RuntimeError('graph api down')

    fake.get_user_info = raise_get_user_info
    _patch_calendar_service(monkeypatch, fake)

    response = client.get(reverse('django_app.calendar:new_event'))

    assert response.status_code == 302
    assert response.url == reverse('django_app.calendar:calendar')


def test_new_event_view_post_missing_required_fields_rerenders_form(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeCalendarService()
    _patch_calendar_service(monkeypatch, fake)

    response = client.post(reverse('django_app.calendar:new_event'), {'ev-subject': '', 'ev-start': '', 'ev-end': ''})

    assert response.status_code == 200
    assert fake.create_event_calls == []


def test_new_event_view_post_creates_event_and_redirects(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeCalendarService(user_info={'mailboxSettings': {'timeZone': 'UTC'}})
    _patch_calendar_service(monkeypatch, fake)

    response = client.post(reverse('django_app.calendar:new_event'), {
        'ev-subject': 'Standup',
        'ev-start': '2024-01-01T09:00',
        'ev-end': '2024-01-01T10:00',
        'ev-attendees': 'a@example.com; b@example.com',
        'ev-body': 'Daily sync',
    })

    assert response.status_code == 302
    assert response.url == reverse('django_app.calendar:calendar')
    assert len(fake.create_event_calls) == 1
    call = fake.create_event_calls[0]
    assert call['subject'] == 'Standup'
    assert call['attendees'] == ['a@example.com', 'b@example.com']
    assert call['body'] == 'Daily sync'


def test_new_event_view_post_rerenders_form_when_create_event_raises(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _authenticate_via_session(client)
    fake = FakeCalendarService(raise_on_create_event=RuntimeError('create failed'))
    _patch_calendar_service(monkeypatch, fake)

    response = client.post(reverse('django_app.calendar:new_event'), {
        'ev-subject': 'Standup',
        'ev-start': '2024-01-01T09:00',
        'ev-end': '2024-01-01T10:00',
    })

    assert response.status_code == 200
    assert response.context['is_authenticated'] is True
