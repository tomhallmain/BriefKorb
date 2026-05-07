"""
Track blocked sender events for future auto-block analysis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


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
    """Persists blocked-sender events as JSONL for offline analysis/training."""

    def __init__(self, storage_path: str):
        self.path = Path(storage_path) / "blocked_sender_events.jsonl"

    def record(self, event: BlockEvent) -> None:
        """Append one block event. Failures are intentionally non-fatal."""
        try:
            payload = event.to_record()
            if not payload["sender"]:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, sort_keys=True))
                f.write("\n")
        except Exception:
            # Tracking must never break message operations.
            return
