"""Tests for GroupMessagesDialog, covering docs/code-briefkorb-tasks-*.tsv's
"Grouped message titles and info view mode" item: view every message's
title/sender/date in a group without loading any message body, plus quick
group-level actions mirroring the existing right-click context menu (see
docs/grouped-message-titles-dialog-spec.md for the design this implements).

Constructs GroupMessagesDialog directly with spy callbacks rather than via
MainWindow._open_group_messages_dialog()/dialog.exec() -- exec() blocks on
a real modal event loop, so exercising the dialog's logic by constructing
it and calling its handler methods directly (never calling .show()/.exec())
avoids hanging the test suite.

Web-app parity: inbox_view now also handles the same three actions (POST,
reusing _resolve_selected_buckets()/_perform_bulk_action()) from
inbox.html's per-bucket buttons -- covered by test_inbox_views.py, not here.
"""

from __future__ import annotations

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why a guard inside
# that fixture's body can't substitute for this.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog  # noqa: E402

from widgets.group_messages_dialog import GroupMessagesDialog  # noqa: E402

from helpers import make_group  # noqa: E402


class _Callbacks:
    def __init__(self):
        self.mark_read_calls = []
        self.delete_calls = []
        self.block_calls = []
        self.open_message_calls = []

    def on_mark_read(self, group):
        self.mark_read_calls.append(group)

    def on_delete_group(self, group):
        self.delete_calls.append(group)

    def on_block_sender(self, group):
        self.block_calls.append(group)

    def on_open_message(self, group, index):
        self.open_message_calls.append((group, index))


def _make_dialog(group, callbacks, parent=None):
    return GroupMessagesDialog(
        group=group,
        on_mark_read=callbacks.on_mark_read,
        on_delete_group=callbacks.on_delete_group,
        on_block_sender=callbacks.on_block_sender,
        on_open_message=callbacks.on_open_message,
        parent=parent,
    )


def test_dialog_lists_every_message_title_without_loading_body(qtbot):
    group = make_group(3)
    callbacks = _Callbacks()
    dialog = _make_dialog(group, callbacks)
    qtbot.addWidget(dialog)

    assert dialog.message_list.count() == 3
    for i in range(3):
        text = dialog.message_list.item(i).text()
        assert group.messages[i].subject in text
        assert group.messages[i].sender in text
    # No body-loading pipeline exists in this dialog at all -- nothing to
    # assert "didn't run" beyond the dialog simply not referencing it,
    # which the module's imports (no MessageBodyWorkerThread) already show.


def test_double_click_opens_message_and_closes_dialog(qtbot):
    group = make_group(2)
    callbacks = _Callbacks()
    dialog = _make_dialog(group, callbacks)
    qtbot.addWidget(dialog)

    item = dialog.message_list.item(1)
    dialog._open_selected_message(item)

    assert callbacks.open_message_calls == [(group, 1)]
    assert dialog.result() == QDialog.Accepted


def test_mark_read_refreshes_list_without_closing_dialog(qtbot):
    group = make_group(2, unread_count=2)
    callbacks = _Callbacks()
    dialog = _make_dialog(group, callbacks)
    qtbot.addWidget(dialog)

    # Simulate what the real _mark_group_as_read_for_group does: mutate
    # is_read in place on the same message objects self.group already holds.
    def _mark_read_and_mutate(g):
        callbacks.mark_read_calls.append(g)
        for m in g.messages:
            m.is_read = True

    dialog._on_mark_read = _mark_read_and_mutate
    dialog._mark_read()

    assert callbacks.mark_read_calls == [group]
    assert dialog.result() != QDialog.Accepted  # still open
    for i in range(dialog.message_list.count()):
        assert dialog.message_list.item(i).text().startswith("○")  # no longer unread


def test_delete_closes_dialog_when_group_removed_from_parent(window, qtbot):
    group = make_group(1)
    window.current_groups = []  # simulates the group having just been deleted
    callbacks = _Callbacks()
    dialog = _make_dialog(group, callbacks, parent=window)
    qtbot.addWidget(dialog)

    dialog._delete_group()

    assert callbacks.delete_calls == [group]
    assert dialog.result() == QDialog.Accepted


def test_delete_keeps_dialog_open_when_group_still_present(window, qtbot):
    group = make_group(1)
    window.current_groups = [group]  # simulates a cancelled confirmation -- group untouched
    callbacks = _Callbacks()
    dialog = _make_dialog(group, callbacks, parent=window)
    qtbot.addWidget(dialog)

    dialog._delete_group()

    assert callbacks.delete_calls == [group]
    assert dialog.result() != QDialog.Accepted


def test_block_closes_dialog_when_group_removed_from_parent(window, qtbot):
    group = make_group(1)
    window.current_groups = []
    callbacks = _Callbacks()
    dialog = _make_dialog(group, callbacks, parent=window)
    qtbot.addWidget(dialog)

    dialog._block_sender()

    assert callbacks.block_calls == [group]
    assert dialog.result() == QDialog.Accepted


def test_open_message_from_dialog_selects_group_and_message(window, qtbot):
    group = make_group(3)
    window.current_groups = [group]

    window._open_message_from_dialog(group, 2)
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)

    assert window.current_group_index == 0
    assert window.current_message_index == 2
