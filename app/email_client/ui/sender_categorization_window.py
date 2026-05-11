"""
Sender impact recategorization window.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QComboBox,
    QMessageBox,
)

from lib.multi_display_qt import SmartWindow
from email_client.utils.sender_categorization import (
    SenderCategorizationManager,
    ImpactLevel,
)


class SenderCategorizationWindow(SmartWindow):
    """Modeless window for reviewing inferred sender impact and applying exceptions."""

    def __init__(
        self,
        manager: SenderCategorizationManager,
        parent: Optional[QWidget] = None,
        on_reinfer: Optional[Callable[[], None]] = None,
    ):
        super().__init__(
            persistent_parent=parent,
            position_parent=parent,
            title="Sender Categorization",
            geometry="900x560",
            center=True,
        )
        self.manager = manager
        self.on_reinfer = on_reinfer
        self._init_ui()
        self._refresh_records()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        self.summary_label = QLabel("Review inferred sender impact and apply exceptions.")
        root.addWidget(self.summary_label)

        self.sender_list = QListWidget()
        self.sender_list.itemSelectionChanged.connect(self._update_details)
        root.addWidget(self.sender_list)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Set selected sender to:"))
        self.impact_combo = QComboBox()
        self.impact_combo.addItems([ImpactLevel.HIGH_IMPACT.value, ImpactLevel.LOW_IMPACT.value])
        controls.addWidget(self.impact_combo)

        self.apply_btn = QPushButton("Apply Exception")
        self.apply_btn.clicked.connect(self._apply_exception)
        controls.addWidget(self.apply_btn)

        self.clear_btn = QPushButton("Revert to Inferred")
        self.clear_btn.clicked.connect(self._clear_exception)
        controls.addWidget(self.clear_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_records)
        controls.addWidget(self.refresh_btn)

        self.rebuild_btn = QPushButton("Clear inferred & rebuild…")
        self.rebuild_btn.setToolTip(
            "Removes all machine-inferred sender/domain scores, then recomputes from the "
            "current message list. Manual overrides are kept."
        )
        self.rebuild_btn.clicked.connect(self._on_rebuild_inferred)
        controls.addWidget(self.rebuild_btn)
        controls.addStretch()
        root.addLayout(controls)

        self.detail_label = QLabel("No sender selected.")
        self.detail_label.setWordWrap(True)
        root.addWidget(self.detail_label)

    def _refresh_records(self) -> None:
        self.records = self.manager.list_sender_records()
        self.sender_list.clear()
        for record in self.records:
            prefix = "⚠" if record["has_exception"] else "•"
            item = QListWidgetItem(f"{prefix} {record['sender']} [{record['impact']}]")
            item.setData(32, record["sender"])
            self.sender_list.addItem(item)
        self.summary_label.setText(f"{len(self.records)} sender(s) tracked; ⚠ indicates manual exception.")
        self._update_details()

    def _on_rebuild_inferred(self) -> None:
        if self.on_reinfer is None:
            QMessageBox.information(
                self,
                "Sender Categorization",
                "Rebuild is only available from the main window when messages are loaded.",
            )
            return
        self.on_reinfer()
        self._refresh_records()

    def _selected_sender(self) -> Optional[str]:
        item = self.sender_list.currentItem()
        if item is None:
            return None
        return item.data(32)

    def _record_for_sender(self, sender: str) -> Optional[dict]:
        for record in self.records:
            if record["sender"] == sender:
                return record
        return None

    def _update_details(self) -> None:
        sender = self._selected_sender()
        if not sender:
            self.detail_label.setText("No sender selected.")
            return
        record = self._record_for_sender(sender)
        if not record:
            self.detail_label.setText("Record not found.")
            return
        confidence = record["confidence"]
        confidence_text = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "n/a"
        trace = record.get("decision_trace") or []
        trace_text = "\n".join(f"  • {t}" for t in trace[:24]) if trace else "n/a"
        self.detail_label.setText(
            f"Sender: {record['sender']}\n"
            f"Domain: {record['domain'] or 'n/a'}\n"
            f"Effective impact: {record['impact']} ({record['source']})\n"
            f"Inferred impact: {record['inferred_impact']}\n"
            f"Reason: {record['reason'] or 'n/a'}\n"
            f"Confidence: {confidence_text}\n"
            f"Scores: generic={float(record.get('generic_inference_score') or 0):.2f} "
            f"blocklist={float(record.get('blocklist_inference_score') or 0):.2f} "
            f"bot_spam={float(record.get('bot_spam_inference_score') or 0):.2f}\n"
            f"Decision trace (last run):\n{trace_text}\n"
            f"Audit log: use SenderCategorizationManager.get_inference_audit_tail() "
            f"(cache key {self.manager.INFERENCE_AUDIT_KEY!r})."
        )

    def _apply_exception(self) -> None:
        sender = self._selected_sender()
        if not sender:
            QMessageBox.information(self, "Sender Categorization", "Select a sender first.")
            return
        self.manager.set_sender_exception(sender, ImpactLevel(self.impact_combo.currentText()))
        self._refresh_records()

    def _clear_exception(self) -> None:
        sender = self._selected_sender()
        if not sender:
            QMessageBox.information(self, "Sender Categorization", "Select a sender first.")
            return
        self.manager.clear_sender_exception(sender)
        self._refresh_records()
