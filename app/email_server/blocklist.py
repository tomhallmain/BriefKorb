"""Sender blocklist backed by encrypted app cache.

A queryable set of suppressed sender addresses, shared by every caller
of UnifiedEmailServer (desktop and web) since it's constructed from the
same storage_path -- distinct from BlockedSenderTracker (blocked_sender_
tracking.py), which is an append-only audit log of block events rather
than a queryable suppression set.
"""
from typing import Set

from email_server.utils.app_info_cache import get_app_info_cache


class SenderBlocklist:
    """Manages a persistent set of blocked sender email addresses."""
    CACHE_KEY = "blocked_senders"

    def __init__(self, storage_path: str):
        self._cache = get_app_info_cache(storage_path)
        self._blocked: Set[str] = self._load()

    def _load(self) -> Set[str]:
        try:
            data = self._cache.get(self.CACHE_KEY, [])
            return set(addr.lower() for addr in data)
        except Exception:
            pass
        return set()

    def _save(self) -> None:
        self._cache.set(self.CACHE_KEY, sorted(self._blocked))
        self._cache.store()

    def block(self, email: str) -> None:
        self._blocked.add(email.lower())
        self._save()

    def is_blocked(self, email: str) -> bool:
        return email.lower() in self._blocked

    def get_all(self) -> Set[str]:
        return set(self._blocked)
