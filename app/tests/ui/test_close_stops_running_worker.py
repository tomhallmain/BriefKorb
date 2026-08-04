"""Tests that closing the main window while a background worker thread is
still running stops that worker instead of leaving it orphaned, covering
docs/code-briefkorb-tasks-*.tsv's "Message content loads in main thread bug"
item ("Should be able to close the application without the worker thread
interrupting.").
"""

from __future__ import annotations

import time

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why a guard inside
# that fixture's body can't substitute for this.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from helpers import make_group  # noqa: E402


def test_close_stops_a_still_running_body_worker(window, monkeypatch):
    from email_client.utils.workers import MessageBodyWorkerThread

    # Slow the worker down just enough that it's still running when
    # window.close() is called right after starting it, without the test
    # itself needing a real sleep-free way to catch it mid-flight.
    original_run = MessageBodyWorkerThread.run

    def slow_run(self):
        time.sleep(0.3)
        original_run(self)

    monkeypatch.setattr(MessageBodyWorkerThread, "run", slow_run)

    group = make_group(1)
    window.current_groups = [group]
    window.current_group_index = 0
    window.current_message_index = 0
    window._display_current_message()

    worker = window.body_worker_thread
    assert worker.isRunning()

    # closeEvent() must quit()/wait() (or, if necessary, terminate()) any
    # running worker before the window actually closes -- if it didn't, this
    # would either hang past the 3s bound closeEvent enforces or leave the
    # thread reported as still running afterwards.
    window.close()

    assert not worker.isRunning()
