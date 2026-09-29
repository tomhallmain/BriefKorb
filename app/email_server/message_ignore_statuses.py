"""Local per-message statuses that hide a message without marking it read,
deleting it, or blocking its sender. Nothing is sent to Graph/Gmail, and
`is_read` is independent of both. IGNORED wins when a message has both.

- SEEN_IN_SESSION: in memory for the life of the process. Module-level
  rather than per store instance because the Django views build a new
  UnifiedEmailServer per request. Desktop and Django are separate processes.
- IGNORED: persisted in the encrypted app cache (as SenderBlocklist is), so
  it survives restarts and is shared by both clients.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Iterable, Optional, Set, Tuple

from email_server.utils.app_info_cache import get_app_info_cache


class IgnoreStatus(str, Enum):
    SEEN_IN_SESSION = "seen_in_session"
    IGNORED = "ignored"


_session_seen: Set[Tuple[str, str]] = set()
_session_lock = threading.Lock()


def clear_session_seen() -> None:
    """Forget every SEEN_IN_SESSION mark in this process."""
    with _session_lock:
        _session_seen.clear()


class MessageIgnoreStore:
    """Reads and writes both ignore statuses, keyed by (provider, message id)."""
    CACHE_KEY = "ignored_messages"

    def __init__(self, storage_path: str):
        self._cache = get_app_info_cache(storage_path)

    def _load_ignored(self) -> Dict[str, Dict[str, str]]:
        """{provider: {message_id: ignored_at_iso}}; `{}` on a missing or
        corrupted cache value."""
        try:
            data = self._cache.get(self.CACHE_KEY, {})
            if not isinstance(data, dict):
                return {}
            return {
                provider: dict(ids)
                for provider, ids in data.items()
                if isinstance(provider, str) and isinstance(ids, dict)
            }
        except Exception:
            return {}

    def _save_ignored(self, data: Dict[str, Dict[str, str]]) -> None:
        self._cache.set(self.CACHE_KEY, {p: ids for p, ids in data.items() if ids})
        self._cache.store()

    def get_status(self, provider: str, message_id: str) -> Optional[IgnoreStatus]:
        if message_id in self._load_ignored().get(provider, {}):
            return IgnoreStatus.IGNORED
        with _session_lock:
            if (provider, message_id) in _session_seen:
                return IgnoreStatus.SEEN_IN_SESSION
        return None

    def get_statuses(self, keys: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], IgnoreStatus]:
        """Bulk form of get_status(), loading the persisted set once.
        Keys with no status are omitted from the result."""
        ignored = self._load_ignored()
        with _session_lock:
            session = set(_session_seen)
        result: Dict[Tuple[str, str], IgnoreStatus] = {}
        for provider, message_id in keys:
            if message_id in ignored.get(provider, {}):
                result[(provider, message_id)] = IgnoreStatus.IGNORED
            elif (provider, message_id) in session:
                result[(provider, message_id)] = IgnoreStatus.SEEN_IN_SESSION
        return result

    def mark_seen_in_session(self, provider: str, message_ids: Iterable[str]) -> None:
        with _session_lock:
            _session_seen.update((provider, message_id) for message_id in message_ids)

    def mark_ignored(self, provider: str, message_ids: Iterable[str]) -> None:
        data = self._load_ignored()
        provider_ids = data.setdefault(provider, {})
        now = datetime.now(timezone.utc).isoformat()
        for message_id in message_ids:
            provider_ids.setdefault(message_id, now)
        self._save_ignored(data)

    def unignore(self, provider: str, message_ids: Iterable[str]) -> None:
        """Clear both statuses for these messages."""
        message_ids = list(message_ids)
        data = self._load_ignored()
        provider_ids = data.get(provider, {})
        if any(message_id in provider_ids for message_id in message_ids):
            for message_id in message_ids:
                provider_ids.pop(message_id, None)
            self._save_ignored(data)
        with _session_lock:
            _session_seen.difference_update((provider, message_id) for message_id in message_ids)
