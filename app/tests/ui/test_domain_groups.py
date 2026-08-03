"""Tests for the desktop client's "Group by Domain" mode, covering
docs/code-briefkorb-tasks-*.tsv's "Domain groups" item: combine senders
sharing an organization's domain into one group, while never merging large
consumer webmail domains, and keeping categorization/inference running
against real per-sender data regardless of which mode is displayed.

Web-app parity for this one was explicitly deferred (materially more
plumbing needed there -- see conversation) -- not covered here.
"""

from __future__ import annotations

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why a guard inside
# that fixture's body can't substitute for this.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from helpers import make_group  # noqa: E402


def _acme_groups():
    return [
        make_group(1, sender_email="alice@acme.com"),
        make_group(1, sender_email="bob@acme.com"),
    ]


def test_group_by_domain_off_by_default(window):
    assert not window.group_by_domain_checkbox.isChecked()


def test_toggling_on_merges_senders_sharing_a_domain(window_with_categorization, qtbot):
    window = window_with_categorization
    window._sender_groups = _acme_groups()
    window._rebuild_current_groups()
    assert len(window.current_groups) == 2  # per-sender, mode off

    window.group_by_domain_checkbox.setChecked(True)

    assert len(window.current_groups) == 1
    merged = window.current_groups[0]
    assert merged.sender_domain == "acme.com"
    assert merged.sender_emails == ("alice@acme.com", "bob@acme.com")
    assert merged.count == 2


def test_toggling_off_restores_per_sender_groups(window_with_categorization, qtbot):
    window = window_with_categorization
    window._sender_groups = _acme_groups()
    window.group_by_domain_checkbox.setChecked(True)
    assert len(window.current_groups) == 1

    window.group_by_domain_checkbox.setChecked(False)

    assert len(window.current_groups) == 2
    assert {g.sender_email for g in window.current_groups} == {"alice@acme.com", "bob@acme.com"}


def test_toggling_resets_selection(window_with_categorization, qtbot):
    window = window_with_categorization
    window._sender_groups = _acme_groups()
    window._rebuild_current_groups()
    window.current_group_index = 0
    window.current_message_index = 0

    window.group_by_domain_checkbox.setChecked(True)

    assert window.current_group_index is None
    assert window.message_nav_label.text() == "No messages"


def test_toggle_is_a_noop_without_sender_categorization(window):
    """window (not window_with_categorization) has sender_categorization ==
    None -- domain mode requires it (for is_personal_mailbox_domain), so
    toggling must not crash and must leave groups exactly as they were."""
    window._sender_groups = _acme_groups()
    window._rebuild_current_groups()

    window.group_by_domain_checkbox.setChecked(True)

    assert len(window.current_groups) == 2  # unchanged -- merge never applied


def test_single_sender_at_a_domain_is_indistinguishable_from_normal_mode(window_with_categorization, qtbot):
    window = window_with_categorization
    window._sender_groups = [make_group(1, sender_email="solo@example.org")]

    window.group_by_domain_checkbox.setChecked(True)

    assert len(window.current_groups) == 1
    assert window.current_groups[0].sender_emails == ("solo@example.org",)
    assert window.current_groups[0].sender_email == "solo@example.org"


def test_deleting_merged_group_removes_all_its_senders_from_sender_groups(window_with_categorization, qtbot, monkeypatch):
    window = window_with_categorization
    window._sender_groups = _acme_groups()
    window.group_by_domain_checkbox.setChecked(True)
    merged_group = window.current_groups[0]

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.Yes))
    monkeypatch.setattr(window, "_load_messages", lambda: None)  # backfill would need a real server

    class _FakeAuthProvider:
        user_id = "user1"

    class _FakeServer:
        def delete_user_messages(self, user_id, provider_name, message_ids):
            return True

    window.server = _FakeServer()
    monkeypatch.setattr(window, "_get_auth_provider_for_message", lambda message: _FakeAuthProvider())

    window._delete_group_for_group(merged_group)

    # Both real senders must be gone from _sender_groups, not just from the
    # displayed (merged) current_groups -- otherwise switching back to
    # per-sender mode would resurrect their (already-deleted) messages.
    assert window._sender_groups == []
    window.group_by_domain_checkbox.setChecked(False)
    assert window.current_groups == []


def test_blocking_multi_sender_group_shows_broadened_confirmation_and_blocks_all(window_with_categorization, qtbot, monkeypatch):
    window = window_with_categorization
    window._sender_groups = _acme_groups()
    window.group_by_domain_checkbox.setChecked(True)
    merged_group = window.current_groups[0]

    confirmation_texts = []

    def _fake_question(self_, title, text, *args, **kwargs):
        confirmation_texts.append(text)
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_fake_question))
    monkeypatch.setattr(window, "_load_messages", lambda: None)

    class _FakeAuthProvider:
        user_id = "user1"

    class _FakeServer:
        def __init__(self):
            self.block_senders_calls = []

        def block_senders(self, user_id, provider_name, sender_names, source="", sender_details=None):
            self.block_senders_calls.append(sorted(sender_names))
            return True

        def delete_user_messages(self, user_id, provider_name, message_ids):
            return True

    fake_server = _FakeServer()
    window.server = fake_server
    monkeypatch.setattr(window, "_get_auth_provider_for_message", lambda message: _FakeAuthProvider())

    window._block_sender_for_group(merged_group)

    assert "alice@acme.com" in confirmation_texts[0]
    assert "bob@acme.com" in confirmation_texts[0]
    assert "2" in confirmation_texts[0]  # sender count called out explicitly
    assert fake_server.block_senders_calls == [["alice@acme.com", "bob@acme.com"]]


def test_blocking_single_sender_group_keeps_original_confirmation_wording(window_with_categorization, qtbot, monkeypatch):
    """A domain group that happens to contain only one real sender must be
    indistinguishable from a normal group -- same confirmation text as
    before domain groups existed."""
    window = window_with_categorization
    group = make_group(1, sender_email="solo@example.org")
    window.current_groups = [group]

    confirmation_texts = []

    def _fake_question(self_, title, text, *args, **kwargs):
        confirmation_texts.append(text)
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_fake_question))
    monkeypatch.setattr(window, "_load_messages", lambda: None)

    class _FakeAuthProvider:
        user_id = "user1"

    class _FakeServer:
        def block_senders(self, user_id, provider_name, sender_names, source="", sender_details=None):
            return True

        def delete_user_messages(self, user_id, provider_name, message_ids):
            return True

    window.server = _FakeServer()
    monkeypatch.setattr(window, "_get_auth_provider_for_message", lambda message: _FakeAuthProvider())

    window._block_sender_for_group(group)

    assert confirmation_texts == ["Block solo@example.org and delete all their messages?"]


def test_impact_override_actions_disabled_for_multi_sender_group(window_with_categorization, qtbot):
    # Calls _build_group_context_menu() directly -- the menu-construction
    # half of _show_group_context_menu, split out specifically so this
    # never has to touch QMenu.exec()'s real modal loop (see
    # _build_group_context_menu's docstring: monkeypatching that C++-backed
    # native call isn't reliable, and a real exec() with no display input
    # coming can hang in a way even Ctrl+C can't interrupt).
    window = window_with_categorization
    window._sender_groups = _acme_groups()
    window.group_by_domain_checkbox.setChecked(True)
    merged_group = window.current_groups[0]

    menu, actions = window._build_group_context_menu(merged_group)

    for key in ("high_impact", "low_impact", "clear_impact"):
        assert not actions[key].isEnabled()
        assert actions[key].toolTip()


def test_impact_override_actions_enabled_for_single_sender_group(window_with_categorization, qtbot):
    window = window_with_categorization
    group = make_group(1, sender_email="solo@example.org")
    window.current_groups = [group]

    menu, actions = window._build_group_context_menu(group)

    for key in ("high_impact", "low_impact", "clear_impact"):
        assert actions[key].isEnabled()
