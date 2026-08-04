"""Tests that redisplaying the already-shown message (e.g. after marking it
read) does not restart the body-loading worker, covering
docs/code-briefkorb-tasks-*.tsv's "Message content reloads on mark as read"
item.
"""

from __future__ import annotations

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why a guard inside
# that fixture's body can't substitute for this.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from helpers import make_group, select_group  # noqa: E402


def test_redisplaying_same_message_does_not_restart_body_worker(window, qtbot):
    group = make_group(1)
    select_group(window, group, qtbot)

    first_worker = window.body_worker_thread
    message = group.messages[0]

    # Mirrors what _mark_as_read()/_mark_group_as_read_for_group() do:
    # mutate the already-displayed message object in place (only is_read
    # changes, not the body), then redisplay it.
    message.is_read = True
    window._display_current_message()

    assert window.body_worker_thread is first_worker
    assert "Status: Read" in window.metadata_label.text()


def test_navigating_to_a_different_message_still_restarts_body_worker(window, qtbot):
    """Contrast case: the same-message guard must not swallow a real
    navigation to different content."""
    group = make_group(2)
    select_group(window, group, qtbot)
    first_worker = window.body_worker_thread

    window._next_message()
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)

    assert window.body_worker_thread is not first_worker
    assert window.current_message_index == 1
