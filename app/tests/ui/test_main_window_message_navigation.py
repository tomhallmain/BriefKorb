"""Tests for the desktop client's within-group message navigation buttons
(First / Previous / Next / Last), covering docs/code-briefkorb-tasks-*.tsv's
"Navigate to end/start of grouping buttons" item.

There is no web-app equivalent to extend: the Django ``messages`` app lists
every message in a sender bucket at once (see inbox.html's <details> markup)
rather than paging through them one at a time, so "jump to start/end of the
grouping" only has meaning in the desktop client's single-message viewer.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest

# main_window.py resolves its sibling imports (``widgets.*``, ``ui.*``) as
# top-level packages, the way they resolve when email_client/main.py is run
# directly (its own directory lands on sys.path as script dir). Mirror that
# here so ``ui.main_window`` imports cleanly under pytest. (This file lives
# in tests/ui/, so email_client/ is parents[2], not parents[1].)
EMAIL_CLIENT_DIR = Path(__file__).resolve().parents[2] / "email_client"
if str(EMAIL_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(EMAIL_CLIENT_DIR))

from email_client.utils.message_grouping import MessageGroup  # noqa: E402
from email_server import EmailMessage  # noqa: E402

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def _make_message(msg_id: str, minute: int) -> EmailMessage:
    return EmailMessage(
        id=msg_id,
        subject=f"Subject {msg_id}",
        sender="Sender <sender@example.com>",
        recipients=["me@example.com"],
        received_date=datetime(2026, 8, 1, 12, minute, tzinfo=timezone.utc),
        body="Plain text body -- no HTML, so no remote image fetch is triggered.",
        is_read=False,
        provider="microsoft",
    )


def _make_group(count: int) -> MessageGroup:
    messages: List[EmailMessage] = [_make_message(str(i), i) for i in range(count)]
    return MessageGroup(
        sender_email="sender@example.com",
        sender_domain="example.com",
        messages=messages,
    )


@pytest.fixture
def window(qtbot, monkeypatch, isolated_app_state):
    """Construct a real MainWindow with every singleton it can reach redirected
    away from this repo's real files.

    MainWindow() normally touches two categories of persistent/global state:

    1. Config/auth singletons (EmailServerConfig, UnifiedEmailServer,
       SenderCategorizationManager, TokenManager) -- all created inside
       _load_config() from a real config.yaml. Stubbing _load_config() to a
       no-op means none of these are ever constructed in the first place, so
       there is nothing to isolate for them; self.config/self.server/
       self.sender_categorization simply stay None, which the navigation
       code under test doesn't touch. (This also sidesteps _load_config()'s
       real behavior of popping a blocking modal QMessageBox when
       config.yaml is missing but config.example.yaml is present -- true in
       this checkout -- which would otherwise hang the test on user input.)
    2. AppInfoCache (the module-level ``app_info_cache`` lazy singleton in
       email_server/utils/app_info_cache.py), reached via
       SmartMainWindow._post_init() -> restore_window_geometry() (fired by
       the QTimer.singleShot(0, ...) in MainWindow.__init__ once qtbot pumps
       the event loop, e.g. in qtbot.waitUntil()) and again via
       closeEvent() -> set_display_position()/set_virtual_screen_info()/
       store() (fired when qtbot.addWidget()'s automatic teardown closes
       this window). Both read/write through the *same* singleton instance
       cache (email_server.utils.app_info_cache._cache_instances), which is
       what the explicitly-requested ``isolated_app_state`` fixture redirects
       to a fresh per-test tmp_path directory and clears before and after
       every test. Depending on it here directly (rather than only relying
       on it being autouse) pins the fixture ordering pytest needs -- this
       fixture's isolation must be active for the *entire* lifetime of
       ``win``, including qtbot's post-test close() -- instead of leaving it
       to autouse-vs-explicit resolution order.
    """
    monkeypatch.setattr(MainWindow, "_load_config", lambda self: None)

    win = MainWindow()
    qtbot.addWidget(win)

    yield win

    # _display_current_message() starts a real MessageBodyWorkerThread; make
    # sure it's finished before the window (and its QThread child) is torn
    # down, or Qt warns/crashes about destroying a running thread.
    body_thread = getattr(win, "body_worker_thread", None)
    if body_thread is not None:
        qtbot.waitUntil(lambda: not body_thread.isRunning(), timeout=2000)


def _select_group(win, group: MessageGroup, qtbot):
    win.current_groups = [group]
    win.current_group_index = 0
    win.current_message_index = 0
    win._display_current_message()
    qtbot.waitUntil(lambda: not win.body_worker_thread.isRunning(), timeout=2000)


def test_no_group_selected_disables_all_nav_buttons(window):
    assert window.current_group_index is None
    assert not window.first_msg_btn.isEnabled()
    assert not window.prev_msg_btn.isEnabled()
    assert not window.next_msg_btn.isEnabled()
    assert not window.last_msg_btn.isEnabled()


def test_selecting_group_starts_at_first_message(window, qtbot):
    group = _make_group(4)
    _select_group(window, group, qtbot)

    assert window.current_message_index == 0
    assert window.message_nav_label.text() == "Message 1 of 4"
    assert not window.first_msg_btn.isEnabled()
    assert not window.prev_msg_btn.isEnabled()
    assert window.next_msg_btn.isEnabled()
    assert window.last_msg_btn.isEnabled()


def test_last_button_jumps_to_final_message(window, qtbot):
    group = _make_group(4)
    _select_group(window, group, qtbot)

    qtbot.mouseClick(window.last_msg_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)

    assert window.current_message_index == 3
    assert window.message_nav_label.text() == "Message 4 of 4"
    assert window.first_msg_btn.isEnabled()
    assert window.prev_msg_btn.isEnabled()
    assert not window.next_msg_btn.isEnabled()
    assert not window.last_msg_btn.isEnabled()


def test_first_button_jumps_back_to_start(window, qtbot):
    group = _make_group(4)
    _select_group(window, group, qtbot)

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
    group = _make_group(3)
    _select_group(window, group, qtbot)

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
    group = _make_group(1)
    _select_group(window, group, qtbot)

    assert window.message_nav_label.text() == "Message 1 of 1"
    assert not window.first_msg_btn.isEnabled()
    assert not window.prev_msg_btn.isEnabled()
    assert not window.next_msg_btn.isEnabled()
    assert not window.last_msg_btn.isEnabled()
