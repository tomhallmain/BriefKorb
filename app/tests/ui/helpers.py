"""Shared, non-fixture test support for tests/ui/.

Not named test_*.py so pytest doesn't try to collect it as a test module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from email_client.utils.message_grouping import MessageGroup
from email_server import EmailMessage


def make_message(msg_id: str, minute: int, is_read: bool = False, sender: str = "Sender <sender@example.com>") -> EmailMessage:
    return EmailMessage(
        id=msg_id,
        subject=f"Subject {msg_id}",
        sender=sender,
        recipients=["me@example.com"],
        received_date=datetime(2026, 8, 1, 12, minute, tzinfo=timezone.utc),
        body="Plain text body -- no HTML, so no remote image fetch is triggered.",
        is_read=is_read,
        provider="microsoft",
    )


def make_group(
    count: int,
    sender_email: str = "sender@example.com",
    unread_count: int | None = None,
) -> MessageGroup:
    """Build a MessageGroup of ``count`` messages.

    ``unread_count`` (default: all unread, matching prior test behavior)
    picks how many of the messages are unread; the rest are marked read.
    Unread messages are placed first so callers relying on message order
    (e.g. navigation tests) see the same fixture shape as before.
    """
    if unread_count is None:
        unread_count = count
    messages: List[EmailMessage] = [
        make_message(str(i), i, is_read=(i >= unread_count), sender=f"Sender <{sender_email}>")
        for i in range(count)
    ]
    return MessageGroup(
        sender_email=sender_email,
        sender_domain=sender_email.split("@", 1)[1],
        messages=messages,
    )


def select_group(win, group: MessageGroup, qtbot) -> None:
    """Select ``group`` as the sole current group, bypassing the message-list UI."""
    win.current_groups = [group]
    win.current_group_index = 0
    win.current_message_index = 0
    win._display_current_message()
    qtbot.waitUntil(lambda: not win.body_worker_thread.isRunning(), timeout=2000)
