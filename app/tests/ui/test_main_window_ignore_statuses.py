"""Desktop client handling of the "Seen in session" / "Marked ignored"
message statuses: hidden by default, shown dimmed with "Show Ignored",
settable per message or per group, and reversible.
"""

from __future__ import annotations

from typing import List

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from email_client.utils.message_grouping import MessageGroup  # noqa: E402
from email_server import EmailMessage  # noqa: E402
from email_server.message_ignore_statuses import IgnoreStatus, MessageIgnoreStore  # noqa: E402

from helpers import make_message  # noqa: E402


class _IgnoreOnlyServer:
    """Stand-in for UnifiedEmailServer exposing only its ignore-status
    methods, backed by a real MessageIgnoreStore (isolated per test by the
    autouse isolated_app_state fixture)."""

    def __init__(self) -> None:
        self.store = MessageIgnoreStore("ignored")

    def mark_messages_seen_in_session(self, provider_name: str, message_ids: List[str]) -> None:
        self.store.mark_seen_in_session(provider_name, message_ids)

    def mark_messages_ignored(self, provider_name: str, message_ids: List[str]) -> None:
        self.store.mark_ignored(provider_name, message_ids)

    def unignore_messages(self, provider_name: str, message_ids: List[str]) -> None:
        self.store.unignore(provider_name, message_ids)

    def annotate_ignore_statuses(self, messages: List[EmailMessage]) -> None:
        statuses = self.store.get_statuses((m.provider, m.id) for m in messages)
        for m in messages:
            m.ignore_status = statuses.get((m.provider, m.id))


def _group(sender_email: str, count: int) -> MessageGroup:
    prefix = sender_email.split("@", 1)[0]
    return MessageGroup(
        sender_email=sender_email,
        sender_domain=sender_email.split("@", 1)[1],
        messages=[
            make_message(f"{prefix}{i}", 59 - i, sender=f"Sender <{sender_email}>")
            for i in range(count)
        ],
    )


@pytest.fixture
def loaded(window):
    """Window with a stub server and two groups loaded the way
    _on_messages_loaded() leaves them."""
    window.server = _IgnoreOnlyServer()
    window._sender_groups = [_group("a@example.com", 3), _group("b@example.com", 1)]
    window._rebuild_current_groups()
    window._update_message_list()
    return window


def _select(window, qtbot, group_index: int, message_index: int = 0) -> None:
    window.current_group_index = group_index
    window.current_message_index = message_index
    window._display_current_message()
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)


def _wait_for_body(window, qtbot) -> None:
    qtbot.waitUntil(lambda: not window.body_worker_thread.isRunning(), timeout=2000)


def test_ignoring_a_group_hides_it(loaded):
    group_b = loaded.current_groups[1]

    loaded._apply_ignore_action(group_b.messages, IgnoreStatus.IGNORED)

    assert [g.sender_email for g in loaded.current_groups] == ["a@example.com"]
    assert loaded.message_list.count() == 1


def test_show_ignored_brings_ignored_groups_back_marked(loaded):
    loaded._apply_ignore_action(loaded.current_groups[1].messages, IgnoreStatus.SEEN_IN_SESSION)

    loaded.show_ignored_checkbox.setChecked(True)

    assert loaded.message_list.count() == 2
    assert "[1 ignored]" in loaded.message_list.item(1).text()
    assert loaded.show_ignored_checkbox.text() == "Show Ignored (on)"


def test_partially_ignored_group_shows_only_remaining_messages(loaded):
    group_a = loaded.current_groups[0]

    loaded._apply_ignore_action(group_a.messages[:1], IgnoreStatus.IGNORED)

    assert loaded.current_groups[0].count == 2
    assert loaded._sender_groups[0].count == 3  # inference still sees everything


def test_skip_moves_to_next_message_in_group(loaded, qtbot):
    _select(loaded, qtbot, 0, 0)
    second = loaded.current_groups[0].messages[1]

    loaded._skip_message()
    _wait_for_body(loaded, qtbot)

    assert loaded.current_selected_message is second
    assert loaded.message_nav_label.text() == "Message 1 of 2"


def test_ignoring_last_visible_message_clears_detail_panel(loaded, qtbot):
    _select(loaded, qtbot, 1, 0)

    loaded._toggle_message_ignored()

    assert loaded.current_group_index is None
    assert loaded.current_selected_message is None
    assert not loaded.ignore_btn.isEnabled()
    assert not loaded.skip_btn.isEnabled()


def test_ignored_message_can_be_unignored_when_shown(loaded, qtbot):
    message = loaded.current_groups[1].messages[0]
    loaded._apply_ignore_action([message], IgnoreStatus.IGNORED)
    loaded.show_ignored_checkbox.setChecked(True)
    _select(loaded, qtbot, 1, 0)

    assert loaded.ignore_btn.text() == "Unignore"
    assert not loaded.skip_btn.isEnabled()
    assert "Ignored" in loaded.metadata_label.text()

    loaded._toggle_message_ignored()

    assert message.ignore_status is None
    assert loaded.ignore_btn.text() == "Ignore"


def test_skip_does_not_downgrade_an_ignored_message(loaded):
    message = loaded.current_groups[1].messages[0]
    loaded._apply_ignore_action([message], IgnoreStatus.IGNORED)

    loaded._apply_ignore_action([message], IgnoreStatus.SEEN_IN_SESSION)

    assert message.ignore_status is IgnoreStatus.IGNORED


def test_reselecting_after_clear_reloads_body(loaded, qtbot):
    # Ignoring clears the panel; showing ignored and selecting the same
    # message again must start a fresh body load, not treat it as unchanged.
    _select(loaded, qtbot, 1, 0)
    loaded._toggle_message_ignored()
    loaded.show_ignored_checkbox.setChecked(True)
    previous_worker = loaded.body_worker_thread

    _select(loaded, qtbot, 1, 0)

    assert loaded.body_worker_thread is not previous_worker


def test_context_menu_unignore_enabled_only_with_ignored_messages(loaded):
    group_a = loaded.current_groups[0]
    _, actions = loaded._build_group_context_menu(group_a)
    assert not actions["unignore"].isEnabled()

    loaded._apply_ignore_action(group_a.messages[:1], IgnoreStatus.IGNORED)
    loaded.show_ignored_checkbox.setChecked(True)
    _, actions = loaded._build_group_context_menu(loaded.current_groups[0])

    assert actions["unignore"].isEnabled()


def test_context_menu_dispatch_ignores_whole_group(loaded):
    group_a = loaded.current_groups[0]
    _, actions = loaded._build_group_context_menu(group_a)

    loaded._dispatch_group_context_menu_action(group_a, actions["ignore"], actions)

    assert all(m.ignore_status is IgnoreStatus.IGNORED for m in group_a.messages)
    assert [g.sender_email for g in loaded.current_groups] == ["b@example.com"]
