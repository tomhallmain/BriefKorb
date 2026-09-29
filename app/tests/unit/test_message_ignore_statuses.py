from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import pytest

from email_server import message_ignore_statuses as module
from email_server.message_ignore_statuses import IgnoreStatus, MessageIgnoreStore, clear_session_seen


@dataclass
class FakeAppInfoCache:
    """Just the .get/.set/.store surface MessageIgnoreStore uses."""
    data: Dict[str, Any] = field(default_factory=dict)
    store_calls: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def store(self) -> None:
        self.store_calls += 1


@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> FakeAppInfoCache:
    cache = FakeAppInfoCache()
    monkeypatch.setattr(module, "get_app_info_cache", lambda storage_path: cache)
    return cache


def test_unmarked_message_has_no_status(fake_cache: FakeAppInfoCache) -> None:
    assert MessageIgnoreStore("ignored").get_status("microsoft", "m1") is None


def test_mark_ignored_persists_per_provider(fake_cache: FakeAppInfoCache) -> None:
    sut = MessageIgnoreStore("ignored")

    sut.mark_ignored("microsoft", ["m1"])

    assert sut.get_status("microsoft", "m1") is IgnoreStatus.IGNORED
    assert sut.get_status("gmail", "m1") is None
    assert list(fake_cache.data[MessageIgnoreStore.CACHE_KEY]["microsoft"]) == ["m1"]
    assert fake_cache.store_calls == 1


def test_mark_ignored_is_visible_to_a_new_store_instance(fake_cache: FakeAppInfoCache) -> None:
    MessageIgnoreStore("ignored").mark_ignored("gmail", ["g1"])

    assert MessageIgnoreStore("ignored").get_status("gmail", "g1") is IgnoreStatus.IGNORED


def test_seen_in_session_is_not_persisted_but_shared_across_instances(fake_cache: FakeAppInfoCache) -> None:
    MessageIgnoreStore("ignored").mark_seen_in_session("microsoft", ["m1"])

    assert MessageIgnoreStore("ignored").get_status("microsoft", "m1") is IgnoreStatus.SEEN_IN_SESSION
    assert MessageIgnoreStore.CACHE_KEY not in fake_cache.data
    assert fake_cache.store_calls == 0


def test_clear_session_seen_forgets_session_marks_only(fake_cache: FakeAppInfoCache) -> None:
    sut = MessageIgnoreStore("ignored")
    sut.mark_seen_in_session("microsoft", ["m1"])
    sut.mark_ignored("microsoft", ["m2"])

    clear_session_seen()

    assert sut.get_status("microsoft", "m1") is None
    assert sut.get_status("microsoft", "m2") is IgnoreStatus.IGNORED


def test_ignored_takes_precedence_over_seen_in_session(fake_cache: FakeAppInfoCache) -> None:
    sut = MessageIgnoreStore("ignored")
    sut.mark_seen_in_session("microsoft", ["m1"])
    sut.mark_ignored("microsoft", ["m1"])

    assert sut.get_status("microsoft", "m1") is IgnoreStatus.IGNORED
    assert sut.get_statuses([("microsoft", "m1")]) == {("microsoft", "m1"): IgnoreStatus.IGNORED}


def test_unignore_clears_both_statuses(fake_cache: FakeAppInfoCache) -> None:
    sut = MessageIgnoreStore("ignored")
    sut.mark_seen_in_session("microsoft", ["m1"])
    sut.mark_ignored("microsoft", ["m1", "m2"])

    sut.unignore("microsoft", ["m1"])

    assert sut.get_status("microsoft", "m1") is None
    assert sut.get_status("microsoft", "m2") is IgnoreStatus.IGNORED


def test_unignore_last_id_drops_the_provider_entry(fake_cache: FakeAppInfoCache) -> None:
    sut = MessageIgnoreStore("ignored")
    sut.mark_ignored("microsoft", ["m1"])

    sut.unignore("microsoft", ["m1"])

    assert fake_cache.data[MessageIgnoreStore.CACHE_KEY] == {}


def test_get_statuses_omits_unmarked_keys(fake_cache: FakeAppInfoCache) -> None:
    sut = MessageIgnoreStore("ignored")
    sut.mark_ignored("microsoft", ["m1"])
    sut.mark_seen_in_session("gmail", ["g1"])

    result = sut.get_statuses([("microsoft", "m1"), ("gmail", "g1"), ("gmail", "g2")])

    assert result == {
        ("microsoft", "m1"): IgnoreStatus.IGNORED,
        ("gmail", "g1"): IgnoreStatus.SEEN_IN_SESSION,
    }


@pytest.mark.parametrize("corrupted", ["not-a-dict", ["m1"], {"microsoft": ["m1"]}, {1: {"m1": "t"}}])
def test_load_recovers_from_corrupted_cache_value(fake_cache: FakeAppInfoCache, corrupted: Any) -> None:
    fake_cache.data[MessageIgnoreStore.CACHE_KEY] = corrupted
    sut = MessageIgnoreStore("ignored")

    assert sut.get_status("microsoft", "m1") is None
    sut.mark_ignored("microsoft", ["m1"])
    assert sut.get_status("microsoft", "m1") is IgnoreStatus.IGNORED
