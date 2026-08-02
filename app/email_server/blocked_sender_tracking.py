"""
Track blocked sender events for future auto-block analysis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from email_server.utils.app_info_cache import get_app_info_cache

# Cap on how many message subjects a single BlockEvent carries -- callers
# should slice their subject lists to this before constructing an event, so
# a large bulk block doesn't balloon the stored record.
MAX_TRACKED_SUBJECTS = 5

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
    sender_display_name: Optional[str] = None
    message_subjects: Optional[List[str]] = None

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
            "sender_display_name": self.sender_display_name,
            "message_subjects": list(self.message_subjects) if self.message_subjects else None,
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

    def get_events(self, sender: Optional[str] = None, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Return recorded block events, newest first.

        `sender` filters to an exact (case-insensitive) match on the
        recorded sender value; `since` filters to events at or after that
        UTC timestamp. Recovers to `[]` on a missing/corrupted cache value,
        same defensiveness `record()` applies on write.
        """
        try:
            events = self._cache.get(self.CACHE_KEY, [])
            if not isinstance(events, list):
                return []
        except Exception:
            return []

        sender_filter = sender.strip().lower() if sender else None
        result: List[Dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if sender_filter is not None and event.get("sender") != sender_filter:
                continue
            if since is not None:
                timestamp = event.get("timestamp_utc")
                try:
                    if timestamp is None or datetime.fromisoformat(timestamp) < since:
                        continue
                except ValueError:
                    continue
            result.append(event)

        # record() always appends, so the cache list is already chronological
        # (oldest first) -- reverse it rather than sorting by timestamp_utc,
        # since two events recorded microseconds apart can share an identical
        # timestamp string, and a stable sort with reverse=True keeps equal
        # keys in their *original* order, silently defeating "newest first"
        # for same-timestamp events.
        result.reverse()
        return result


def group_events_by_sender(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group block events (as returned by get_events(), newest-first) into
    one summary per sender, for viewer UIs.

    Pure function -- no cache access -- so it's testable and reusable by
    both clients without needing a tracker instance. Input order is
    preserved as encounter order, so passing already-newest-first events
    (get_events()'s contract) makes each summary's `latest_event` correct
    without an extra sort here.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for event in events:
        sender = event.get("sender", "")
        if sender not in grouped:
            grouped[sender] = []
            order.append(sender)
        grouped[sender].append(event)

    summaries = []
    for sender in order:
        sender_events = grouped[sender]
        latest = sender_events[0]
        summaries.append({
            "sender": sender,
            "sender_kind": latest.get("sender_kind"),
            "sender_display_name": latest.get("sender_display_name"),
            "event_count": len(sender_events),
            "latest_event": latest,
            "events": sender_events,
        })
    return summaries
