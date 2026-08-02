from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import pytest

from email_server import blocklist as blocklist_module
from email_server.blocklist import SenderBlocklist


@dataclass
class FakeAppInfoCache:
    """Minimal double for AppInfoCache -- just the .get/.set/.store surface
    SenderBlocklist actually uses."""
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
    monkeypatch.setattr(blocklist_module, "get_app_info_cache", lambda storage_path: cache)
    return cache


def test_new_blocklist_starts_empty(fake_cache: FakeAppInfoCache) -> None:
    sut = SenderBlocklist(storage_path="ignored")

    assert sut.get_all() == set()


def test_block_adds_lowercased_address_and_persists(fake_cache: FakeAppInfoCache) -> None:
    sut = SenderBlocklist(storage_path="ignored")

    sut.block("Spam@Example.COM")

    assert sut.get_all() == {"spam@example.com"}
    assert fake_cache.data[SenderBlocklist.CACHE_KEY] == ["spam@example.com"]
    assert fake_cache.store_calls == 1


def test_is_blocked_is_case_insensitive(fake_cache: FakeAppInfoCache) -> None:
    sut = SenderBlocklist(storage_path="ignored")
    sut.block("spam@example.com")

    assert sut.is_blocked("Spam@Example.com") is True
    assert sut.is_blocked("other@example.com") is False


def test_loads_existing_blocked_addresses_from_cache(fake_cache: FakeAppInfoCache) -> None:
    fake_cache.data[SenderBlocklist.CACHE_KEY] = ["already@example.com"]

    sut = SenderBlocklist(storage_path="ignored")

    assert sut.is_blocked("already@example.com") is True


def test_load_recovers_from_corrupted_cache_value(fake_cache: FakeAppInfoCache) -> None:
    # A list of non-strings raises AttributeError on .lower() during load
    # (unlike e.g. a bare corrupted string, which would iterate character-
    # by-character without raising) -- this is the shape that actually
    # exercises the broad except/recovery path.
    fake_cache.data[SenderBlocklist.CACHE_KEY] = [123, 456]

    sut = SenderBlocklist(storage_path="ignored")

    assert sut.get_all() == set()


def test_get_all_returns_a_copy(fake_cache: FakeAppInfoCache) -> None:
    sut = SenderBlocklist(storage_path="ignored")
    sut.block("spam@example.com")

    result = sut.get_all()
    result.add("not-actually-blocked@example.com")

    assert sut.get_all() == {"spam@example.com"}


def test_unblock_removes_a_blocked_address(fake_cache: FakeAppInfoCache) -> None:
    sut = SenderBlocklist(storage_path="ignored")
    sut.block("spam@example.com")

    sut.unblock("Spam@Example.COM")

    assert sut.get_all() == set()
    assert fake_cache.data[SenderBlocklist.CACHE_KEY] == []


def test_unblock_is_a_no_op_for_address_never_blocked(fake_cache: FakeAppInfoCache) -> None:
    sut = SenderBlocklist(storage_path="ignored")
    sut.block("other@example.com")

    sut.unblock("never-blocked@example.com")

    assert sut.get_all() == {"other@example.com"}
