"""Tests for the desktop client's "Oldest First" group-order toggle, covering
docs/code-briefkorb-tasks-*.tsv's "Reverse order of groupings" item
("Start with the earliest first").

Groups are normally sorted most-recent-first by group_messages_by_sender()
before ever reaching MainWindow; this toggle only reverses how the already-
computed self.current_groups list is *displayed*, so these tests build
current_groups directly (in an arbitrary order standing in for "most recent
first") and assert on the resulting QListWidget item order rather than on
timestamps.

There is no web-app equivalent to extend: the Django ``messages`` inbox
template has no group-order control to begin with (see
test_message_list_quick_stats.py's module docstring for the same point about
its missing quick-stats label).
"""

from __future__ import annotations

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why a guard inside
# that fixture's body can't substitute for this.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from helpers import make_group  # noqa: E402


def _displayed_sender_order(window):
    return [
        window.message_list.item(i).data(Qt.UserRole).sender_email
        for i in range(window.message_list.count())
    ]


def _three_groups():
    return [
        make_group(1, sender_email="a@example.com"),
        make_group(1, sender_email="b@example.com"),
        make_group(1, sender_email="c@example.com"),
    ]


def test_default_shows_current_groups_order_unchanged(window):
    window.current_groups = _three_groups()
    window._update_message_list()

    assert not window.oldest_first_checkbox.isChecked()
    assert _displayed_sender_order(window) == ["a@example.com", "b@example.com", "c@example.com"]


def test_toggling_on_reverses_display_order(window):
    window.current_groups = _three_groups()
    window._update_message_list()

    window.oldest_first_checkbox.setChecked(True)

    assert _displayed_sender_order(window) == ["c@example.com", "b@example.com", "a@example.com"]
    assert window.oldest_first_checkbox.text() == "Oldest First (on)"


def test_toggling_back_off_restores_original_order(window):
    window.current_groups = _three_groups()
    window._update_message_list()

    window.oldest_first_checkbox.setChecked(True)
    window.oldest_first_checkbox.setChecked(False)

    assert _displayed_sender_order(window) == ["a@example.com", "b@example.com", "c@example.com"]
    assert window.oldest_first_checkbox.text() == "Oldest First"


def test_order_toggle_applies_after_other_filters(window):
    # b@example.com has no unread messages, so "Unread Only" drops it before
    # the order toggle ever sees it -- the reversal must only apply to the
    # groups that survive filtering, not the full current_groups list.
    window.current_groups = [
        make_group(1, sender_email="a@example.com", unread_count=1),
        make_group(1, sender_email="b@example.com", unread_count=0),
        make_group(1, sender_email="c@example.com", unread_count=1),
    ]
    window.unread_only_checkbox.setChecked(True)
    window.oldest_first_checkbox.setChecked(True)  # triggers _update_message_list() itself

    assert _displayed_sender_order(window) == ["c@example.com", "a@example.com"]


def test_toggling_order_does_not_disturb_current_selection(window, qtbot):
    # Deliberately not using helpers.select_group() here -- it replaces
    # current_groups with a single-element list, which would defeat the
    # point of this test (checking that a 3-group current_groups survives
    # the toggle with the same group still selected).
    groups = _three_groups()
    window.current_groups = groups
    window.current_group_index = window._find_group_index(groups[1])  # b@example.com
    window.current_message_index = 0
    window._display_current_message()
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)

    window._update_message_list()
    window.oldest_first_checkbox.setChecked(True)

    # Selection is resolved by sender_email (see _find_group_index), not by
    # position in the (now-reversed) display list, so it must be unaffected.
    assert window.current_groups[window.current_group_index].sender_email == "b@example.com"
    assert window.current_message_index == 0
