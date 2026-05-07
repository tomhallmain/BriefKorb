"""
Track blocked sender events for future auto-block analysis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from email_server.utils.app_info_cache import get_app_info_cache

@dataclass(frozen=True)
class BlockEvent:
    """Structured event describing a manual sender block."""

    sender: str
    source: str
    action: str = "manual_block"
    sender_kind: str = "email"
    provider: Optional[str] = None
    mailbox: Optional[str] = None
    message_count: Optional[int] = None
    sender_domain: Optional[str] = None

    def to_record(self) -> Dict[str, Any]:
        """Convert event data into a JSON-serialisable record."""
        return {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "sender": self.sender.strip().lower(),
            "sender_kind": self.sender_kind,
            "source": self.source,
            "action": self.action,
            "provider": self.provider,
            "mailbox": self.mailbox,
            "message_count": self.message_count,
            "sender_domain": self.sender_domain,
        }


class BlockedSenderTracker:
    """Persists blocked-sender events in encrypted app cache."""
    CACHE_KEY = "blocked_sender_events"

    def __init__(self, storage_path: str):
        self._cache = get_app_info_cache(storage_path)

    def record(self, event: BlockEvent) -> None:
        """Append one block event. Failures are intentionally non-fatal."""
        try:
            payload = event.to_record()
            if not payload["sender"]:
                return
            existing = self._cache.get(self.CACHE_KEY, [])
            if not isinstance(existing, list):
                existing = []
            existing.append(json.loads(json.dumps(payload, sort_keys=True)))
            self._cache.set(self.CACHE_KEY, existing)
            self._cache.store()
        except Exception:
            # Tracking must never break message operations.
            return
