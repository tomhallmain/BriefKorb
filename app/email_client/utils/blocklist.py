"""
Blocklist manager for filtering messages from blocked senders
"""

import json
from pathlib import Path
from typing import Set


class BlocklistManager:
    """Manages a persistent list of blocked sender email addresses"""

    def __init__(self, storage_path: str):
        self.path = Path(storage_path) / 'blocklist.json'
        self._blocked: Set[str] = self._load()

    def _load(self) -> Set[str]:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding='utf-8'))
                return set(addr.lower() for addr in data.get('blocked_senders', []))
        except Exception:
            pass
        return set()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({'blocked_senders': sorted(self._blocked)}, indent=2),
            encoding='utf-8'
        )

    def block(self, email: str) -> None:
        self._blocked.add(email.lower())
        self._save()

    def is_blocked(self, email: str) -> bool:
        return email.lower() in self._blocked

    def get_all(self) -> Set[str]:
        return set(self._blocked)
