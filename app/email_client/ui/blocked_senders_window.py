"""
Blocked senders viewer window.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QMessageBox,
)

from lib.multi_display_qt import SmartWindow
from email_server import UnifiedEmailServer


class BlockedSendersWindow(SmartWindow):
    """Modeless window for reviewing blocked-sender history and unblocking senders."""

    def __init__(
        self,
        server: UnifiedEmailServer,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(
            persistent_parent=parent,
            position_parent=parent,
            title="Blocked Senders",
            geometry="900x560",
            center=True,
        )
        self.server = server
        self._init_ui()
        self._refresh_summaries()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        self.summary_label = QLabel("Review blocked-sender history and unblock local suppression.")
        root.addWidget(self.summary_label)

        self.sender_list = QListWidget()
        self.sender_list.itemSelectionChanged.connect(self._update_details)
        root.addWidget(self.sender_list)

        controls = QHBoxLayout()
        self.unblock_btn = QPushButton("Unblock")
        self.unblock_btn.clicked.connect(self._unblock_selected)
        controls.addWidget(self.unblock_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_summaries)
        controls.addWidget(self.refresh_btn)
        controls.addStretch()
        root.addLayout(controls)

        self.detail_label = QLabel("No sender selected.")
        self.detail_label.setWordWrap(True)
        root.addWidget(self.detail_label)

    def _refresh_summaries(self) -> None:
        self.summaries = self.server.get_blocked_sender_summary()
        self.sender_list.clear()
        for summary in self.summaries:
            marker = "●" if summary["is_locally_blocked"] else "○"
            label = summary["sender"]
            if summary.get("sender_display_name"):
                label += f" ({summary['sender_display_name']})"
            item = QListWidgetItem(f"{marker} {label} — {summary['event_count']} block(s)")
            item.setData(32, summary["sender"])
            self.sender_list.addItem(item)
        self.summary_label.setText(
            f"{len(self.summaries)} sender(s) with block history; "
            "● = currently suppressed locally, ○ = history only (e.g. a web-side rule block)."
        )
        self._update_details()

    def _selected_sender(self) -> Optional[str]:
        item = self.sender_list.currentItem()
        if item is None:
            return None
        return item.data(32)

    def _summary_for_sender(self, sender: str) -> Optional[dict]:
        for summary in self.summaries:
            if summary["sender"] == sender:
                return summary
        return None

    def _update_details(self) -> None:
        sender = self._selected_sender()
        if not sender:
            self.detail_label.setText("No sender selected.")
            self.unblock_btn.setEnabled(False)
            return
        summary = self._summary_for_sender(sender)
        if not summary:
            self.detail_label.setText("Record not found.")
            self.unblock_btn.setEnabled(False)
            return

        self.unblock_btn.setEnabled(bool(summary["is_locally_blocked"]))

        history_lines: List[str] = []
        for event in summary["events"][:24]:
            subjects = event.get("message_subjects") or []
            subjects_text = "; ".join(subjects) if subjects else "n/a"
            history_lines.append(
                f"  • {event.get('timestamp_utc', 'n/a')} via {event.get('source', 'n/a')} "
                f"(provider={event.get('provider') or 'n/a'}, messages={event.get('message_count') if event.get('message_count') is not None else 'n/a'})\n"
                f"    subjects: {subjects_text}"
            )
        history_text = "\n".join(history_lines) if history_lines else "n/a"

        self.detail_label.setText(
            f"Sender: {summary['sender']}\n"
            f"Display name: {summary.get('sender_display_name') or 'n/a'}\n"
            f"Currently suppressed locally: {'Yes' if summary['is_locally_blocked'] else 'No (history only)'}\n"
            f"Block events ({summary['event_count']}):\n{history_text}"
        )

    def _unblock_selected(self) -> None:
        sender = self._selected_sender()
        if not sender:
            QMessageBox.information(self, "Blocked Senders", "Select a sender first.")
            return
        self.server.unblock_sender(sender)
        self._refresh_summaries()
