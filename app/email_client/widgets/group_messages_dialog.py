"""
Dialog listing every message's title/sender/date in a group, without
loading any message body -- opened via the group context menu's "View
Message Titles..." action.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from email_client.utils.message_grouping import MessageGroup


class GroupMessagesDialog(QDialog):
    """Modal, read-mostly view of a single sender group's message titles.

    Built entirely from already-in-memory MessageGroup/EmailMessage data --
    no MessageBodyWorkerThread, no network call. Mark-Read/Delete/Block
    reuse the exact same group-scoped MainWindow methods the group's
    right-click context menu already calls, so there's exactly one place
    each of those behaviors lives. Being modal means the parent's
    current_groups can't change out from under this dialog while it's
    open, so checking whether `group` is still present there after an
    action is a reliable way to tell whether it actually went through (the
    action methods show their own confirmation prompts and can be
    cancelled, so they don't always remove the group).
    """

    def __init__(
        self,
        group: MessageGroup,
        on_mark_read: Callable[[MessageGroup], None],
        on_delete_group: Callable[[MessageGroup], None],
        on_block_sender: Callable[[MessageGroup], None],
        on_open_message: Callable[[MessageGroup, int], None],
        parent=None,
    ):
        super().__init__(parent)
        self.group = group
        self._on_mark_read = on_mark_read
        self._on_delete_group = on_delete_group
        self._on_block_sender = on_block_sender
        self._on_open_message = on_open_message
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle(f"Messages from {self.group.display_name}")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        layout = QVBoxLayout(self)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.message_list = QListWidget()
        self.message_list.itemDoubleClicked.connect(self._open_selected_message)
        layout.addWidget(self.message_list)

        self._populate_list()

        actions = QHBoxLayout()
        self.mark_read_btn = QPushButton("Mark Group as Read")
        self.mark_read_btn.clicked.connect(self._mark_read)
        actions.addWidget(self.mark_read_btn)

        self.delete_btn = QPushButton("Delete Group")
        self.delete_btn.clicked.connect(self._delete_group)
        actions.addWidget(self.delete_btn)

        self.block_btn = QPushButton("Block Sender and Delete Group")
        self.block_btn.clicked.connect(self._block_sender)
        actions.addWidget(self.block_btn)

        actions.addStretch()
        layout.addLayout(actions)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def _populate_list(self) -> None:
        """(Re)build the row list from self.group.messages' current state.

        Called on init and after Mark as Read, since that mutates each
        EmailMessage.is_read in place on the same objects self.group
        already holds -- the unread indicators would otherwise go stale
        until the dialog was reopened.
        """
        self.message_list.clear()
        for index, message in enumerate(self.group.messages):
            unread_indicator = "●" if not message.is_read else "○"
            date_str = message.received_date.strftime("%Y-%m-%d %H:%M")
            item = QListWidgetItem(
                f"{unread_indicator} {message.subject or '(No Subject)'}  —  {message.sender}  —  {date_str}"
            )
            item.setData(Qt.UserRole, index)
            self.message_list.addItem(item)
        self.summary_label.setText(
            f"{self.group.count} message(s) from {self.group.display_name}. "
            "Double-click a message to open it."
        )

    def _group_still_present(self) -> bool:
        parent = self.parent()
        current_groups = getattr(parent, "current_groups", None)
        if current_groups is None:
            return True
        return any(g is self.group for g in current_groups)

    def _open_selected_message(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.UserRole)
        self._on_open_message(self.group, index)
        self.accept()

    def _mark_read(self) -> None:
        self._on_mark_read(self.group)
        self._populate_list()

    def _delete_group(self) -> None:
        self._on_delete_group(self.group)
        if not self._group_still_present():
            self.accept()

    def _block_sender(self) -> None:
        self._on_block_sender(self.group)
        if not self._group_still_present():
            self.accept()
