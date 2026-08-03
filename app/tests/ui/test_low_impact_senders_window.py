"""Tests for the desktop client's "Low-Impact Senders" window, covering
docs/code-briefkorb-tasks-*.tsv's "Default to separate view for low-impact
(subscription and especially advertising) categories" item (see
docs/low-impact-separate-view-spec.md for the design this implements).

Confirmed low-impact senders (ImpactLevel.LOW_IMPACT, via
SenderCategorizationManager) are excluded from the main message list by
default; LowImpactSendersWindow is the "separate view" they're still
reachable from, with the same mark-read/delete/block actions the main
list's context menu already exposes, plus a "treat as high-impact" action
to move a sender back into the main list.

Web-app parity: messages_view/inbox_view now apply the same default
exclusion, and a new `messages/low-impact` route (low_impact_only=True,
same view+template, see django_app/messages/urls.py) is the web
equivalent of this window -- covered by
tests/django/test_messages_views.py and test_inbox_views.py, not here.
"""

from __future__ import annotations

import pytest

# Both importorskip calls must happen at *module* level -- see the
# tests/ui/conftest.py ``window`` fixture's docstring for why a guard inside
# that fixture's body can't substitute for this.
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from email_client.utils.sender_categorization import ImpactLevel, SenderCategorizationManager  # noqa: E402

from helpers import make_group  # noqa: E402


@pytest.fixture
def window_with_categorization(window):
    """The shared `window` fixture stubs _load_config() to a no-op (see
    tests/ui/conftest.py), so window.sender_categorization is normally None
    -- this feature is entirely gated on it, so tests need a real manager
    wired in. storage_path is ignored in favor of BRIEFKORB_CACHE_DIR (set
    by the autouse isolated_app_state fixture), same as
    tests/unit/test_sender_categorization_inference.py's convention.
    """
    window.sender_categorization = SenderCategorizationManager(storage_path="ignored")
    return window


def _set_impact(window, sender_email: str, impact: ImpactLevel) -> None:
    window.sender_categorization.set_sender_exception(sender_email, impact)


def _main_list_senders(window) -> list:
    return [
        window.message_list.item(i).data(Qt.UserRole).sender_email
        for i in range(window.message_list.count())
    ]


def test_low_impact_group_excluded_from_main_list_by_default(window_with_categorization):
    window = window_with_categorization
    high = make_group(1, sender_email="high@example.com")
    low = make_group(1, sender_email="low@example.com")
    unclassified = make_group(1, sender_email="unclassified@example.com")
    _set_impact(window, "high@example.com", ImpactLevel.HIGH_IMPACT)
    _set_impact(window, "low@example.com", ImpactLevel.LOW_IMPACT)
    window.current_groups = [high, low, unclassified]

    window._update_message_list()

    shown = _main_list_senders(window)
    assert "low@example.com" not in shown
    assert "high@example.com" in shown
    assert "unclassified@example.com" in shown  # unclassified stays -- not confirmed low-impact


def test_open_low_impact_senders_warns_without_categorization(window, qtbot, monkeypatch):
    assert window.sender_categorization is None
    # QMessageBox.warning() would otherwise show a real modal and block
    # waiting for a click that never comes.
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: warnings.append(args) or QMessageBox.Ok),
    )

    window._open_low_impact_senders()

    assert warnings
    assert window.low_impact_senders_window is None


def test_open_low_impact_senders_shows_only_low_impact_groups(window_with_categorization, qtbot):
    window = window_with_categorization
    high = make_group(1, sender_email="high@example.com")
    low = make_group(1, sender_email="low@example.com")
    _set_impact(window, "high@example.com", ImpactLevel.HIGH_IMPACT)
    _set_impact(window, "low@example.com", ImpactLevel.LOW_IMPACT)
    window.current_groups = [high, low]
    window._update_message_list()

    window._open_low_impact_senders()
    qtbot.addWidget(window.low_impact_senders_window)

    assert window.low_impact_senders_window is not None
    assert [g.sender_email for g in window.low_impact_senders_window.groups] == ["low@example.com"]


def test_promoting_low_impact_group_moves_it_to_main_list(window_with_categorization, qtbot):
    window = window_with_categorization
    low = make_group(1, sender_email="low@example.com")
    _set_impact(window, "low@example.com", ImpactLevel.LOW_IMPACT)
    window.current_groups = [low]
    window._update_message_list()
    window._open_low_impact_senders()
    qtbot.addWidget(window.low_impact_senders_window)
    assert [g.sender_email for g in window.low_impact_senders_window.groups] == ["low@example.com"]

    window._promote_group_to_high_impact(low)

    # _update_message_list() (called inside _promote_group_to_high_impact)
    # both refreshes the main list and pushes the new low-impact set to the
    # already-open window -- the sender should now be gone from the
    # low-impact window and present in the main one.
    assert window.low_impact_senders_window.groups == []
    assert _main_list_senders(window) == ["low@example.com"]


def test_deleting_low_impact_group_via_window_removes_it_from_both_lists(window_with_categorization, qtbot, monkeypatch):
    window = window_with_categorization
    low = make_group(1, sender_email="low@example.com")
    _set_impact(window, "low@example.com", ImpactLevel.LOW_IMPACT)
    window.current_groups = [low]
    window._update_message_list()
    window._open_low_impact_senders()
    qtbot.addWidget(window.low_impact_senders_window)
    # Populating the list doesn't select a row -- _delete_selected() reads
    # sender_list.currentItem(), which stays None (and silently no-ops)
    # until something is explicitly selected.
    window.low_impact_senders_window.sender_list.setCurrentRow(0)

    # _delete_group_for_group() confirms via a modal QMessageBox -- answer
    # "Yes" without a real dialog loop, and give it a fake server + auth
    # provider so _do_delete_group() takes its normal success path rather
    # than the "no authenticated provider" branch.
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.Yes))
    # _do_delete_group() now also calls _backfill_messages_if_below_limit(),
    # which would call the real _load_messages() -- unrelated to what this
    # test checks (delete propagating to both lists), and _FakeServer below
    # doesn't implement get_authenticated_providers(), so stub it out.
    monkeypatch.setattr(window, "_load_messages", lambda: None)

    class _FakeAuthProvider:
        user_id = "user1"

    class _FakeServer:
        def delete_user_messages(self, user_id, provider_name, message_ids):
            return True

    window.server = _FakeServer()
    monkeypatch.setattr(window, "_get_auth_provider_for_message", lambda message: _FakeAuthProvider())

    window.low_impact_senders_window._delete_selected()

    assert window.current_groups == []
    assert window.low_impact_senders_window.groups == []
