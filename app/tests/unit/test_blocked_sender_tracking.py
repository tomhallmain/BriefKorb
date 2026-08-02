from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from email_server import blocked_sender_tracking as bst
from email_server.blocked_sender_tracking import (
    BlockedSenderTracker,
    BlockEvent,
    group_events_by_sender,
)


# --- BlockEvent.to_record (pure) -------------------------------------------

def test_to_record_normalizes_sender_case_and_whitespace() -> None:
    event = BlockEvent(sender='  Spam@Example.COM  ', source='django_web_messages')

    record = event.to_record()

    assert record['sender'] == 'spam@example.com'


def test_to_record_includes_defaults_for_optional_fields() -> None:
    event = BlockEvent(sender='spam@example.com', source='django_web_messages')

    record = event.to_record()

    assert record['action'] == 'manual_block'
    assert record['sender_kind'] == 'email'
    assert record['provider'] is None
    assert record['mailbox'] is None
    assert record['message_count'] is None
    assert record['sender_domain'] is None
    assert 'timestamp_utc' in record


def test_to_record_carries_through_explicit_optional_fields() -> None:
    event = BlockEvent(
        sender='Spammer',
        source='django_web_messages',
        sender_kind='display_name',
        provider='microsoft',
        mailbox='inbox',
        message_count=5,
        sender_domain='example.com',
    )

    record = event.to_record()

    assert record['sender_kind'] == 'display_name'
    assert record['provider'] == 'microsoft'
    assert record['mailbox'] == 'inbox'
    assert record['message_count'] == 5
    assert record['sender_domain'] == 'example.com'


def test_to_record_includes_display_name_and_subjects() -> None:
    event = BlockEvent(
        sender='spam@example.com',
        source='desktop_email_client',
        sender_display_name='Spammy Inc',
        message_subjects=['Buy now', 'Last chance'],
    )

    record = event.to_record()

    assert record['sender_display_name'] == 'Spammy Inc'
    assert record['message_subjects'] == ['Buy now', 'Last chance']


def test_to_record_defaults_display_name_and_subjects_to_none() -> None:
    event = BlockEvent(sender='spam@example.com', source='desktop_email_client')

    record = event.to_record()

    assert record['sender_display_name'] is None
    assert record['message_subjects'] is None


def test_to_record_treats_empty_subjects_list_as_none() -> None:
    event = BlockEvent(sender='spam@example.com', source='desktop_email_client', message_subjects=[])

    record = event.to_record()

    assert record['message_subjects'] is None


# --- BlockedSenderTracker.record --------------------------------------------

@dataclass
class FakeAppInfoCache:
    """Minimal double for AppInfoCache -- just the .get/.set/.store surface
    BlockedSenderTracker actually uses."""
    data: Dict[str, Any]
    store_calls: int = 0
    raise_on_store: bool = False

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def store(self) -> None:
        self.store_calls += 1
        if self.raise_on_store:
            raise RuntimeError("disk full")


@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> FakeAppInfoCache:
    cache = FakeAppInfoCache(data={})
    monkeypatch.setattr(bst, "get_app_info_cache", lambda storage_path: cache)
    return cache


def test_record_appends_event_to_cache(fake_cache: FakeAppInfoCache) -> None:
    tracker = BlockedSenderTracker(storage_path="ignored")

    tracker.record(BlockEvent(sender="spam@example.com", source="django_web_messages"))

    events = fake_cache.data[BlockedSenderTracker.CACHE_KEY]
    assert len(events) == 1
    assert events[0]['sender'] == 'spam@example.com'
    assert fake_cache.store_calls == 1


def test_record_appends_to_existing_list(fake_cache: FakeAppInfoCache) -> None:
    fake_cache.data[BlockedSenderTracker.CACHE_KEY] = [{'sender': 'old@example.com'}]
    tracker = BlockedSenderTracker(storage_path="ignored")

    tracker.record(BlockEvent(sender="new@example.com", source="django_web_messages"))

    events = fake_cache.data[BlockedSenderTracker.CACHE_KEY]
    assert len(events) == 2
    assert events[0]['sender'] == 'old@example.com'
    assert events[1]['sender'] == 'new@example.com'


def test_record_recovers_from_non_list_existing_value(fake_cache: FakeAppInfoCache) -> None:
    fake_cache.data[BlockedSenderTracker.CACHE_KEY] = "corrupted, not a list"
    tracker = BlockedSenderTracker(storage_path="ignored")

    tracker.record(BlockEvent(sender="new@example.com", source="django_web_messages"))

    events = fake_cache.data[BlockedSenderTracker.CACHE_KEY]
    assert events == [events[0]]
    assert events[0]['sender'] == 'new@example.com'


def test_record_skips_storing_when_sender_is_blank(fake_cache: FakeAppInfoCache) -> None:
    tracker = BlockedSenderTracker(storage_path="ignored")

    tracker.record(BlockEvent(sender="   ", source="django_web_messages"))

    assert BlockedSenderTracker.CACHE_KEY not in fake_cache.data
    assert fake_cache.store_calls == 0


def test_record_swallows_store_failures_silently(fake_cache: FakeAppInfoCache) -> None:
    fake_cache.raise_on_store = True
    tracker = BlockedSenderTracker(storage_path="ignored")

    # Must not raise -- tracking is intentionally best-effort/non-fatal.
    tracker.record(BlockEvent(sender="spam@example.com", source="django_web_messages"))


# --- BlockedSenderTracker.get_events -----------------------------------------

def test_get_events_returns_empty_list_when_cache_empty(fake_cache: FakeAppInfoCache) -> None:
    tracker = BlockedSenderTracker(storage_path="ignored")
    assert tracker.get_events() == []


def test_get_events_returns_empty_list_for_corrupted_cache_value(fake_cache: FakeAppInfoCache) -> None:
    fake_cache.data[BlockedSenderTracker.CACHE_KEY] = "corrupted, not a list"
    tracker = BlockedSenderTracker(storage_path="ignored")
    assert tracker.get_events() == []


def test_get_events_returns_newest_first(fake_cache: FakeAppInfoCache) -> None:
    tracker = BlockedSenderTracker(storage_path="ignored")
    tracker.record(BlockEvent(sender="a@example.com", source="s"))
    tracker.record(BlockEvent(sender="b@example.com", source="s"))

    events = tracker.get_events()

    assert [e['sender'] for e in events] == ['b@example.com', 'a@example.com']


def test_get_events_filters_by_sender_case_insensitively(fake_cache: FakeAppInfoCache) -> None:
    tracker = BlockedSenderTracker(storage_path="ignored")
    tracker.record(BlockEvent(sender="spam@example.com", source="s"))
    tracker.record(BlockEvent(sender="other@example.com", source="s"))

    events = tracker.get_events(sender="Spam@Example.com")

    assert [e['sender'] for e in events] == ['spam@example.com']


def test_get_events_filters_by_since(fake_cache: FakeAppInfoCache) -> None:
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    fake_cache.data[BlockedSenderTracker.CACHE_KEY] = [
        {'sender': 'old@example.com', 'timestamp_utc': old_ts},
    ]
    tracker = BlockedSenderTracker(storage_path="ignored")
    tracker.record(BlockEvent(sender="new@example.com", source="s"))

    events = tracker.get_events(since=datetime.now(timezone.utc) - timedelta(days=1))

    assert [e['sender'] for e in events] == ['new@example.com']


def test_get_events_skips_non_dict_entries(fake_cache: FakeAppInfoCache) -> None:
    fake_cache.data[BlockedSenderTracker.CACHE_KEY] = ["not-a-dict", {'sender': 'ok@example.com', 'timestamp_utc': 'x'}]
    tracker = BlockedSenderTracker(storage_path="ignored")

    events = tracker.get_events()

    assert [e['sender'] for e in events] == ['ok@example.com']


# --- group_events_by_sender (pure) -------------------------------------------

def test_group_events_by_sender_groups_and_counts() -> None:
    events = [
        {'sender': 'a@example.com', 'sender_kind': 'email', 'sender_display_name': 'A Co'},
        {'sender': 'b@example.com', 'sender_kind': 'email', 'sender_display_name': 'B Co'},
        {'sender': 'a@example.com', 'sender_kind': 'email', 'sender_display_name': 'A Co'},
    ]

    summaries = group_events_by_sender(events)

    assert [s['sender'] for s in summaries] == ['a@example.com', 'b@example.com']
    assert summaries[0]['event_count'] == 2
    assert summaries[1]['event_count'] == 1


def test_group_events_by_sender_latest_event_is_first_occurrence() -> None:
    events = [
        {'sender': 'a@example.com', 'timestamp_utc': 'newest'},
        {'sender': 'a@example.com', 'timestamp_utc': 'oldest'},
    ]

    summaries = group_events_by_sender(events)

    assert summaries[0]['latest_event']['timestamp_utc'] == 'newest'
    assert [e['timestamp_utc'] for e in summaries[0]['events']] == ['newest', 'oldest']


def test_group_events_by_sender_returns_empty_list_for_no_events() -> None:
    assert group_events_by_sender([]) == []
