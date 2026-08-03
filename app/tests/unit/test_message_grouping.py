"""Tests for email_client/utils/message_grouping.py's domain-groups
support, covering docs/code-briefkorb-tasks-*.tsv's "Domain groups" item:
MessageGroup.sender_emails and merge_groups_by_domain().

group_messages_by_sender() itself (the pre-existing per-sender grouping) is
exercised indirectly throughout the rest of the suite via
tests/ui/helpers.py's make_group() and isn't re-tested here; these tests
are only for what's new.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from email_client.utils.message_grouping import MessageGroup, merge_groups_by_domain
from email_server import EmailMessage


def _message(msg_id: str, sender: str) -> EmailMessage:
    return EmailMessage(
        id=msg_id,
        subject=f"Subject {msg_id}",
        sender=sender,
        recipients=["me@example.com"],
        received_date=datetime(2024, 1, int(msg_id) % 28 + 1, tzinfo=timezone.utc),
        body="",
        is_read=False,
        provider="microsoft",
    )


def _sender_group(sender_email: str, domain: str, message_ids: List[str]) -> MessageGroup:
    return MessageGroup(
        sender_email=sender_email,
        sender_domain=domain,
        messages=[_message(mid, f"Test <{sender_email}>") for mid in message_ids],
    )


def _no_personal_domains(domain: str) -> bool:
    return False


# --- MessageGroup.sender_emails / display_name ---------------------------------

def test_sender_emails_defaults_to_one_tuple_of_sender_email() -> None:
    group = _sender_group("alice@acme.com", "acme.com", ["1"])

    assert group.sender_emails == ("alice@acme.com",)


def test_sender_emails_explicit_value_is_kept() -> None:
    group = MessageGroup(
        sender_email="acme.com",
        sender_domain="acme.com",
        messages=[_message("1", "Test <alice@acme.com>")],
        sender_emails=("alice@acme.com", "bob@acme.com"),
    )

    assert group.sender_emails == ("alice@acme.com", "bob@acme.com")


def test_display_name_unchanged_for_single_sender_group() -> None:
    group = MessageGroup(
        sender_email="alice@acme.com",
        sender_domain="acme.com",
        messages=[_message("1", "Alice Smith <alice@acme.com>")],
    )

    assert group.display_name == "Alice Smith (alice@acme.com)"


def test_display_name_for_multi_sender_group() -> None:
    group = MessageGroup(
        sender_email="acme.com",
        sender_domain="acme.com",
        messages=[_message("1", "Alice Smith <alice@acme.com>")],
        sender_emails=("alice@acme.com", "bob@acme.com", "carol@acme.com"),
    )

    assert group.display_name == "acme.com (3 senders)"


# --- merge_groups_by_domain -----------------------------------------------------

def test_merges_multiple_senders_sharing_a_non_personal_domain() -> None:
    groups = [
        _sender_group("alice@acme.com", "acme.com", ["1"]),
        _sender_group("bob@acme.com", "acme.com", ["2"]),
    ]

    merged = merge_groups_by_domain(groups, _no_personal_domains)

    assert len(merged) == 1
    result = merged[0]
    assert result.sender_email == "acme.com"
    assert result.sender_domain == "acme.com"
    assert result.sender_emails == ("alice@acme.com", "bob@acme.com")
    assert {m.id for m in result.messages} == {"1", "2"}


def test_single_sender_group_at_a_domain_passes_through_unchanged() -> None:
    """A domain with only one contributing sender_group is exactly the
    "already behaves like today" case -- passed through as-is, not rebuilt,
    and its sender_emails is a 1-tuple like any ordinary group."""
    solo = _sender_group("alice@acme.com", "acme.com", ["1"])

    merged = merge_groups_by_domain([solo], _no_personal_domains)

    assert merged == [solo]
    assert merged[0].sender_emails == ("alice@acme.com",)


def test_personal_mailbox_domains_are_never_merged() -> None:
    groups = [
        _sender_group("alice@gmail.com", "gmail.com", ["1"]),
        _sender_group("bob@gmail.com", "gmail.com", ["2"]),
    ]

    merged = merge_groups_by_domain(groups, lambda domain: domain == "gmail.com")

    assert len(merged) == 2
    assert {g.sender_email for g in merged} == {"alice@gmail.com", "bob@gmail.com"}
    assert all(len(g.sender_emails) == 1 for g in merged)


def test_mixed_personal_and_organization_domains() -> None:
    groups = [
        _sender_group("alice@acme.com", "acme.com", ["1"]),
        _sender_group("bob@acme.com", "acme.com", ["2"]),
        _sender_group("carol@gmail.com", "gmail.com", ["3"]),
    ]

    merged = merge_groups_by_domain(groups, lambda domain: domain == "gmail.com")

    by_key = {g.sender_email: g for g in merged}
    assert set(by_key) == {"acme.com", "carol@gmail.com"}
    assert by_key["acme.com"].sender_emails == ("alice@acme.com", "bob@acme.com")
    assert by_key["carol@gmail.com"].sender_emails == ("carol@gmail.com",)


def test_merged_messages_are_sorted_most_recent_first() -> None:
    groups = [
        _sender_group("alice@acme.com", "acme.com", ["1"]),  # 2024-01-02
        _sender_group("bob@acme.com", "acme.com", ["5"]),    # 2024-01-06
    ]

    merged = merge_groups_by_domain(groups, _no_personal_domains)

    assert [m.id for m in merged[0].messages] == ["5", "1"]


def test_result_groups_sorted_by_latest_date_most_recent_first() -> None:
    groups = [
        _sender_group("alice@acme.com", "acme.com", ["1"]),   # older
        _sender_group("carol@other.com", "other.com", ["9"]),  # newer, solo
    ]

    merged = merge_groups_by_domain(groups, _no_personal_domains)

    assert [g.sender_domain for g in merged] == ["other.com", "acme.com"]
