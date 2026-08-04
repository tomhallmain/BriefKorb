"""Tests for the desktop client's message-list backfill on delete/block,
covering docs/code-briefkorb-tasks-*.tsv's "Backfill up to the limit on
delete/block" item.

Confirmed scope (see conversation, not a design doc -- this one was small
enough not to need one): messages are fetched once, capped at
DEFAULT_MAX_MESSAGES (email_client/utils/workers.py). Deleting or blocking
messages previously only spliced them out of the already-loaded in-memory
list, so the visible count could shrink permanently below the limit even
though the mailbox might still have more mail available. The fix reuses the
existing full-refresh path (_load_messages()) to top the list back up
whenever it's below the limit after a delete/block -- no new provider-side
"fetch more" capability needed.

No web-app equivalent needed: messages_view/inbox_view re-fetch fresh from
the server on every request already (no persistent in-memory state between
requests), so there's nothing there that could get permanently "stuck"
below the limit the way the desktop's long-lived in-memory list can.
"""

from __future__ import annotations

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why a guard inside
# that fixture's body can't substitute for this.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from email_client.utils.workers import DEFAULT_MAX_MESSAGES  # noqa: E402
from email_server.config import EmailServerConfig, ProviderConfig  # noqa: E402

from helpers import make_group  # noqa: E402


def test_effective_max_messages_falls_back_to_default_when_config_not_loaded(window):
    assert window.config is None  # the `window` fixture stubs _load_config() to a no-op
    assert window._effective_max_messages() == DEFAULT_MAX_MESSAGES


def test_effective_max_messages_reads_configured_value(window):
    window.config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=True), gmail=ProviderConfig(enabled=False),
        max_messages=42,
    )

    assert window._effective_max_messages() == 42


def test_backfill_uses_configured_max_messages_instead_of_default(window, monkeypatch):
    """The same scenario as test_backfill_does_not_reload_when_at_limit, but
    with a configured cap well below DEFAULT_MAX_MESSAGES -- the backfill
    threshold must track the configured value, not the hardcoded constant."""
    window.config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=True), gmail=ProviderConfig(enabled=False),
        max_messages=5,
    )
    window.current_messages = [object()] * 5
    calls = []
    monkeypatch.setattr(window, "_load_messages", lambda: calls.append(1))

    window._backfill_messages_if_below_limit()

    assert calls == []

    window.current_messages = [object()] * 4
    window._backfill_messages_if_below_limit()

    assert calls == [1]


def test_backfill_triggers_reload_when_below_limit(window, monkeypatch):
    window.current_messages = [object()] * 3
    calls = []
    monkeypatch.setattr(window, "_load_messages", lambda: calls.append(1))

    window._backfill_messages_if_below_limit()

    assert calls == [1]


def test_backfill_does_not_reload_when_at_limit(window, monkeypatch):
    window.current_messages = [object()] * DEFAULT_MAX_MESSAGES
    calls = []
    monkeypatch.setattr(window, "_load_messages", lambda: calls.append(1))

    window._backfill_messages_if_below_limit()

    assert calls == []


class _FakeAuthProvider:
    user_id = "user1"


class _FakeServer:
    def __init__(self):
        self.deleted_message_ids = []

    def delete_user_messages(self, user_id, provider_name, message_ids):
        self.deleted_message_ids.extend(message_ids)
        return True

    def block_senders(self, user_id, provider_name, sender_names, source="", sender_details=None):
        return True


@pytest.fixture
def window_ready_to_delete(window, monkeypatch):
    """A window with just enough wired up (server, auth resolution, and a
    stubbed confirmation dialog) to exercise the real delete/block action
    methods without touching the network."""
    window.server = _FakeServer()
    monkeypatch.setattr(window, "_get_auth_provider_for_message", lambda message: _FakeAuthProvider())
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.Yes))
    return window


def test_deleting_group_below_limit_triggers_backfill(window_ready_to_delete, monkeypatch):
    window = window_ready_to_delete
    group = make_group(2)
    window.current_groups = [group]
    window.current_messages = list(group.messages)  # well below DEFAULT_MAX_MESSAGES
    calls = []
    monkeypatch.setattr(window, "_load_messages", lambda: calls.append(1))

    window._delete_group_for_group(group)

    assert calls == [1]


def test_blocking_sender_below_limit_triggers_backfill(window_ready_to_delete, monkeypatch):
    window = window_ready_to_delete
    group = make_group(2)
    window.current_groups = [group]
    window.current_messages = list(group.messages)
    calls = []
    monkeypatch.setattr(window, "_load_messages", lambda: calls.append(1))

    window._block_sender_for_group(group)

    assert calls == [1]


def test_deleting_single_message_below_limit_triggers_backfill(window_ready_to_delete, monkeypatch):
    window = window_ready_to_delete
    group = make_group(2)
    window.current_groups = [group]
    window.current_group_index = 0
    window.current_messages = list(group.messages)
    window.current_selected_message = group.messages[0]
    calls = []
    monkeypatch.setattr(window, "_load_messages", lambda: calls.append(1))

    window._delete_message()

    assert calls == [1]


class _FillerMessage:
    """Stands in for other, unrelated already-loaded messages -- needs a
    distinct `.id` (not one of the deleted group's) so it survives
    _do_delete_group()'s `m.id not in deleted_ids` filter and a `.provider`
    it never touches otherwise, since it's never in `group.messages`."""

    def __init__(self, i: int):
        self.id = f"filler-{i}"


def test_deleting_group_at_limit_does_not_trigger_backfill(window_ready_to_delete, monkeypatch):
    """If the loaded set is still at the fetch cap even after this delete
    (e.g. other groups still fill it out), there's nothing to top up."""
    window = window_ready_to_delete
    group = make_group(2)
    window.current_groups = [group]
    # Simulate DEFAULT_MAX_MESSAGES other, still-loaded messages surviving
    # this group's deletion -- current_messages only drops by group.messages,
    # which _do_delete_group filters out of it, so pad it well above the cap.
    window.current_messages = list(group.messages) + [_FillerMessage(i) for i in range(DEFAULT_MAX_MESSAGES)]
    calls = []
    monkeypatch.setattr(window, "_load_messages", lambda: calls.append(1))

    window._delete_group_for_group(group)

    assert calls == []
