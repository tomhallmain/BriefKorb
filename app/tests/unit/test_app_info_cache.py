"""Tests for email_server/utils/app_info_cache.py's AppInfoCache.

encrypt_data_to_file()/decrypt_data_from_file() (from encryptor.py) go through
the real OS keyring for key material -- every test here patches those two
names on the app_info_cache module (a plain top-level ``from .encryptor
import ...``, so patching the local binding is enough) with a no-crypto
stand-in, so no test ever touches the OS credential store. BRIEFKORB_CACHE_DIR
is also repointed at a fresh tmp_path per test, since AppInfoCache treats
that env var as an override that beats any storage_path argument -- without
this, tests would share (and mutate) the one cache dir the whole unit-test
session already has bootstrapped in conftest.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from email_server.utils import app_info_cache as app_info_cache_module
from email_server.utils.app_info_cache import AppInfoCache


def _fake_encrypt(data: bytes, service_name: str, app_identifier: str, output_path: str) -> None:
    with open(output_path, 'wb') as f:
        f.write(data)


def _fake_decrypt(path: str, service_name: str, app_identifier: str) -> bytes:
    with open(path, 'rb') as f:
        return f.read()


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BRIEFKORB_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(app_info_cache_module, 'encrypt_data_to_file', _fake_encrypt)
    monkeypatch.setattr(app_info_cache_module, 'decrypt_data_from_file', _fake_decrypt)
    app_info_cache_module._cache_instances.clear()


def test_init_starts_with_empty_cache_when_no_file_exists() -> None:
    cache = AppInfoCache()
    assert cache.get('anything') is None
    assert cache.has_changes is False


def test_set_and_get_round_trip_in_memory() -> None:
    cache = AppInfoCache()
    cache.set('theme', 'dark')
    assert cache.get('theme') == 'dark'
    assert cache.has_changes is True


def test_get_returns_default_for_missing_key() -> None:
    cache = AppInfoCache()
    assert cache.get('missing', default_val='fallback') == 'fallback'


def test_store_then_fresh_instance_reloads_persisted_value(tmp_path: Path) -> None:
    cache = AppInfoCache(storage_path=str(tmp_path))
    cache.set('theme', 'dark')

    assert cache.store() is True
    assert cache.has_changes is False

    reloaded = AppInfoCache(storage_path=str(tmp_path))
    assert reloaded.get('theme') == 'dark'


def test_store_falls_back_to_json_when_encryption_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = AppInfoCache(storage_path=str(tmp_path))
    cache.set('theme', 'dark')

    def raise_encrypt(*a: Any, **k: Any) -> None:
        raise RuntimeError('encryption unavailable')

    monkeypatch.setattr(app_info_cache_module, 'encrypt_data_to_file', raise_encrypt)

    assert cache.store() is False
    assert json.loads(Path(cache._json_loc).read_text())['info']['theme'] == 'dark'


def test_set_directory_value_round_trips_and_normalizes_path(tmp_path: Path) -> None:
    cache = AppInfoCache()
    cache.set_directory_color(str(tmp_path), '#112233')

    assert cache.get_directory_color(str(tmp_path)) == '#112233'


def test_get_app_info_cache_returns_singleton_per_storage_path(tmp_path: Path) -> None:
    first = app_info_cache_module.get_app_info_cache(storage_path=str(tmp_path))
    second = app_info_cache_module.get_app_info_cache(storage_path=str(tmp_path))
    assert first is second


def test_lazy_default_app_info_cache_proxies_to_singleton() -> None:
    assert app_info_cache_module.app_info_cache.get('anything') is None
