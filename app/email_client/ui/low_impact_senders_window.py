"""
Low-impact senders viewer window.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from lib.multi_display_qt import SmartWindow
from email_client.utils.message_grouping import MessageGroup


class LowImpactSendersWindow(SmartWindow):
    """Modeless window listing sender groups classified as low-impact
    (subscriptions, ads, etc.) -- these are excluded from the main message
    list by default (see main_window.py's _update_message_list()), and this
    is the "separate view" they're still reachable from.

    Holds no server/categorization reference of its own -- every action
    delegates to the corresponding group-scoped method already on
    MainWindow (the same ones the main list's right-click menu uses), so
    there's exactly one place each of those behaviors lives.
    """

    def __init__(
        self,
        groups: List[MessageGroup],
        on_mark_read: Callable[[MessageGroup], None],
        on_delete_group: Callable[[MessageGroup], None],
        on_block_sender: Callable[[MessageGroup], None],
        on_promote_to_high_impact: Callable[[MessageGroup], None],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(
            persistent_parent=parent,
            position_parent=parent,
            title="Low-Impact Senders",
            geometry="900x560",
            center=True,
        )
        self._on_mark_read = on_mark_read
        self._on_delete_group = on_delete_group
        self._on_block_sender = on_block_sender
        self._on_promote_to_high_impact = on_promote_to_high_impact
        self.groups: List[MessageGroup] = []
        self._init_ui()
        self.set_groups(groups)

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        self.summary_label = QLabel(
            "Senders classified as low-impact (subscriptions, ads, etc.) -- kept out of the main list."
        )
        root.addWidget(self.summary_label)

        self.sender_list = QListWidget()
        self.sender_list.itemSelectionChanged.connect(self._update_button_state)
        root.addWidget(self.sender_list)

        controls = QHBoxLayout()
        self.mark_read_btn = QPushButton("Mark Group as Read")
        self.mark_read_btn.clicked.connect(self._mark_read_selected)
        controls.addWidget(self.mark_read_btn)

        self.delete_btn = QPushButton("Delete Group")
        self.delete_btn.clicked.connect(self._delete_selected)
        controls.addWidget(self.delete_btn)

        self.block_btn = QPushButton("Block Sender and Delete Group")
        self.block_btn.clicked.connect(self._block_selected)
        controls.addWidget(self.block_btn)

        self.promote_btn = QPushButton("Treat as High-Impact")
        self.promote_btn.setToolTip(
            "Move this sender back to the main list by overriding its impact classification to high-impact."
        )
        self.promote_btn.clicked.connect(self._promote_selected)
        controls.addWidget(self.promote_btn)

        controls.addStretch()
        root.addLayout(controls)
        self._update_button_state()

    def set_groups(self, groups: List[MessageGroup]) -> None:
        """Replace the displayed groups. Called on open and after
        MainWindow._update_message_list() runs, so this always reflects
        current state (a promoted/deleted/blocked sender disappears from
        this list the same way it would from the main one)."""
        self.groups = groups
        self.sender_list.clear()
        for group in groups:
            unread_indicator = "●" if group.unread_count > 0 else "○"
            date_str = group.latest_date.strftime("%Y-%m-%d %H:%M")
            item = QListWidgetItem(f"{unread_indicator} {group.display_name} ({group.count} messages) - {date_str}")
            item.setData(Qt.UserRole, group)
            self.sender_list.addItem(item)
        self.summary_label.setText(
            f"{len(groups)} low-impact sender(s), kept out of the main list."
        )
        self._update_button_state()

    def _selected_group(self) -> Optional[MessageGroup]:
        item = self.sender_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _update_button_state(self) -> None:
        has_selection = self._selected_group() is not None
        self.mark_read_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)
        self.block_btn.setEnabled(has_selection)
        self.promote_btn.setEnabled(has_selection)

    def _mark_read_selected(self) -> None:
        group = self._selected_group()
        if group is not None:
            self._on_mark_read(group)

    def _delete_selected(self) -> None:
        group = self._selected_group()
        if group is not None:
            self._on_delete_group(group)

    def _block_selected(self) -> None:
        group = self._selected_group()
        if group is not None:
            self._on_block_sender(group)

    def _promote_selected(self) -> None:
        group = self._selected_group()
        if group is not None:
            self._on_promote_to_high_impact(group)
