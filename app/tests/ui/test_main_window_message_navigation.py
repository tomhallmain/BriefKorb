"""Tests for the desktop client's within-group message navigation buttons
(First / Previous / Next / Last), covering docs/code-briefkorb-tasks-*.tsv's
"Navigate to end/start of grouping buttons" item.

There is no web-app equivalent to extend: the Django ``messages`` app lists
every message in a sender bucket at once (see inbox.html's <details> markup)
rather than paging through them one at a time, so "jump to start/end of the
grouping" only has meaning in the desktop client's single-message viewer.
"""

from __future__ import annotations

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why a guard inside
# that fixture's body can't substitute for this.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from helpers import make_group, select_group  # noqa: E402


def test_no_group_selected_disables_all_nav_buttons(window):
    assert window.current_group_index is None
    assert not window.first_msg_btn.isEnabled()
    assert not window.prev_msg_btn.isEnabled()
    assert not window.next_msg_btn.isEnabled()
    assert not window.last_msg_btn.isEnabled()


def test_selecting_group_starts_at_first_message(window, qtbot):
    group = make_group(4)
    select_group(window, group, qtbot)

    assert window.current_message_index == 0
    assert window.message_nav_label.text() == "Message 1 of 4"
    assert not window.first_msg_btn.isEnabled()
    assert not window.prev_msg_btn.isEnabled()
    assert window.next_msg_btn.isEnabled()
    assert window.last_msg_btn.isEnabled()


def test_last_button_jumps_to_final_message(window, qtbot):
    group = make_group(4)
    select_group(window, group, qtbot)

    qtbot.mouseClick(window.last_msg_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)

    assert window.current_message_index == 3
    assert window.message_nav_label.text() == "Message 4 of 4"
    assert window.first_msg_btn.isEnabled()
    assert window.prev_msg_btn.isEnabled()
    assert not window.next_msg_btn.isEnabled()
    assert not window.last_msg_btn.isEnabled()


def test_first_button_jumps_back_to_start(window, qtbot):
    group = make_group(4)
    select_group(window, group, qtbot)

    window._last_message()
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)
    assert window.current_message_index == 3

    window._first_message()
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)

    assert window.current_message_index == 0
    assert window.message_nav_label.text() == "Message 1 of 4"
    assert not window.first_msg_btn.isEnabled()
    assert not window.prev_msg_btn.isEnabled()
    assert window.next_msg_btn.isEnabled()
    assert window.last_msg_btn.isEnabled()


def test_first_and_last_are_noops_at_their_own_boundary(window, qtbot):
    group = make_group(3)
    select_group(window, group, qtbot)

    # Already at the first message -- _first_message() must not touch state
    # (mirrors the existing _previous_message()/_next_message() boundary
    # guards, which no-op rather than raising when there's nowhere to go).
    window._first_message()
    assert window.current_message_index == 0

    window._last_message()
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)
    assert window.current_message_index == 2

    window._last_message()
    assert window.current_message_index == 2


def test_single_message_group_disables_all_nav_buttons(window, qtbot):
    group = make_group(1)
    select_group(window, group, qtbot)

    assert window.message_nav_label.text() == "Message 1 of 1"
    assert not window.first_msg_btn.isEnabled()
    assert not window.prev_msg_btn.isEnabled()
    assert not window.next_msg_btn.isEnabled()
    assert not window.last_msg_btn.isEnabled()
