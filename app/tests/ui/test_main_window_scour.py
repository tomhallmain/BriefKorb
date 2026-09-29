"""Desktop "Scour Unread": merging a ScourResult into the loaded list."""

from __future__ import annotations

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from email_client.utils.message_grouping import group_messages_by_sender  # noqa: E402
from email_server import ScourResult  # noqa: E402

from helpers import make_message  # noqa: E402


@pytest.fixture
def loaded(window):
    window.current_messages = [make_message("new1", 50, sender="A <a@example.com>")]
    window._sender_groups = group_messages_by_sender(window.current_messages)
    window._rebuild_current_groups()
    window._update_message_list()
    return window


def test_scour_result_merges_new_messages_into_groups(loaded):
    old_a = make_message("old_a", 1, sender="A <a@example.com>")
    old_b = make_message("old_b", 2, sender="B <b@example.com>")

    loaded._on_scour_complete(ScourResult(messages=[old_a, old_b], scanned=40))

    assert [m.id for m in loaded.current_messages] == ["new1", "old_a", "old_b"]
    counts = {g.sender_email: g.count for g in loaded.current_groups}
    assert counts == {"a@example.com": 2, "b@example.com": 1}
    assert loaded.message_list.count() == 2
    assert "added 2 unread message(s) (40 scanned)" in loaded.statusBar.currentMessage()


def test_scour_result_skips_messages_already_loaded(loaded):
    duplicate = make_message("new1", 50, sender="A <a@example.com>")

    loaded._on_scour_complete(ScourResult(messages=[duplicate], scanned=1))

    assert [m.id for m in loaded.current_messages] == ["new1"]
    assert loaded.current_messages[0] is not duplicate
    assert "added 0 unread message(s)" in loaded.statusBar.currentMessage()


def test_scour_result_reports_cutoff_and_errors(loaded):
    loaded._on_scour_complete(ScourResult(scanned=2000, stopped_early=True, errors=["gmail: HTTP 429"]))

    message = loaded.statusBar.currentMessage()
    assert "Stopped at the scour limit" in message
    assert "gmail: HTTP 429" in message


def test_scour_result_restores_button(loaded):
    loaded.scour_btn.setEnabled(False)
    loaded.scour_btn.setText("Scouring...")

    loaded._on_scour_complete(ScourResult())

    assert loaded.scour_btn.isEnabled()
    assert loaded.scour_btn.text() == "Scour Unread"


def test_scour_keeps_selected_group(loaded, qtbot):
    loaded.current_group_index = 0
    loaded.current_message_index = 0
    loaded._display_current_message()
    qtbot.waitUntil(lambda: not loaded.body_worker_thread.isRunning(), timeout=2000)
    selected = loaded.current_selected_message

    loaded._on_scour_complete(ScourResult(messages=[make_message("zz", 1, sender="Z <z@example.com>")]))
    qtbot.waitUntil(lambda: not loaded.body_worker_thread.isRunning(), timeout=2000)

    assert loaded.current_groups[loaded.current_group_index].sender_email == "a@example.com"
    assert loaded.current_selected_message is selected
