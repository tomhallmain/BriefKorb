"""Tests for the desktop client's message-list quick stats label, covering
docs/code-briefkorb-tasks-*.tsv's "Better quick stats on message groupings"
item: totals were previously inclusive of read mail ("Showing 2 groups (5
messages)") with no way to tell how many of those were actually unread; they
should be split out into unread/read counts.

There is no web-app equivalent to extend: the Django ``messages`` app's
inbox template (inbox.html) has no aggregate count label at all -- it lists
sender buckets with a per-message "unread" badge and nothing else -- so
there's no existing "quick stats" summary there to split out.
"""

from __future__ import annotations

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why a guard inside
# that fixture's body can't substitute for this.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from helpers import make_group  # noqa: E402


def test_stats_split_unread_and_read_across_groups(window):
    # 3 unread + 2 read in one 5-message group, 1 unread + 4 read in
    # another: 4 unread, 6 read, 10 messages total, across 2 groups.
    window.current_groups = [
        make_group(5, sender_email="a@example.com", unread_count=3),
        make_group(5, sender_email="b@example.com", unread_count=1),
    ]
    window._update_message_list()

    assert window.message_count_label.text() == (
        "Showing 2 groups (10 messages — 4 unread, 6 read)"
    )


def test_stats_all_unread(window):
    window.current_groups = [make_group(3, unread_count=3)]
    window._update_message_list()

    assert window.message_count_label.text() == (
        "Showing 1 groups (3 messages — 3 unread, 0 read)"
    )


def test_stats_all_read(window):
    window.current_groups = [make_group(3, unread_count=0)]
    window._update_message_list()

    assert window.message_count_label.text() == (
        "Showing 1 groups (3 messages — 0 unread, 3 read)"
    )


def test_stats_reflect_unread_only_filter(window, qtbot):
    # A fully-read group is dropped entirely by the "Unread Only" filter, so
    # both the group count and the read/unread split must reflect only the
    # group that survives it -- not window.current_groups as a whole.
    window.current_groups = [
        make_group(4, sender_email="a@example.com", unread_count=2),
        make_group(3, sender_email="b@example.com", unread_count=0),
    ]

    window.unread_only_checkbox.setChecked(True)
    window._update_message_list()

    assert window.message_count_label.text() == (
        "Showing 1 groups (4 messages — 2 unread, 2 read)"
    )


def test_stats_no_groups(window):
    window.current_groups = []
    window._update_message_list()

    assert window.message_count_label.text() == (
        "Showing 0 groups (0 messages — 0 unread, 0 read)"
    )
