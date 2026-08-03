"""
Message grouping utilities for bundling messages by sender
"""

from typing import Callable, List, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
import re

from email_server import EmailMessage
from email_server.utils.datetime_compat import normalize_received_at_utc
from .content_type import ContentType


@dataclass
class MessageGroup:
    """Represents a group of messages from the same sender, or -- when
    built via merge_groups_by_domain() -- from several senders sharing a
    non-personal-mailbox domain."""
    sender_email: str
    sender_domain: str
    messages: List[EmailMessage]
    content_type: ContentType = ContentType.UNCLASSIFIED
    # The real sender addresses this group represents. For a normal
    # per-sender group this is just (sender_email,); merge_groups_by_domain()
    # populates it explicitly with every contributing address. Left empty
    # here and filled in by __post_init__ so every existing call site that
    # builds a MessageGroup without knowing about this field still gets a
    # correct value with no changes required.
    sender_emails: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.sender_emails:
            self.sender_emails = (self.sender_email,)

    @property
    def count(self) -> int:
        """Number of messages in this group"""
        return len(self.messages)

    @property
    def latest_date(self) -> datetime:
        """Date of the most recent message"""
        return max(msg.received_date for msg in self.messages)

    @property
    def unread_count(self) -> int:
        """Number of unread messages in this group"""
        return sum(1 for msg in self.messages if not msg.is_read)

    @property
    def display_name(self) -> str:
        """Display name for the sender (extracted from email or use email)"""
        if len(self.sender_emails) > 1:
            return f"{self.sender_domain} ({len(self.sender_emails)} senders)"
        if self.messages:
            # Try to extract name from first message's sender field
            first_sender = self.messages[0].sender
            name, email = parseaddr(first_sender)
            if name:
                return f"{name} ({self.sender_email})"
        return self.sender_email


def extract_sender_email(sender: str) -> str:
    """Extract email address from sender string (e.g., 'Name <email@domain.com>')"""
    name, email = parseaddr(sender)
    return email.lower() if email else sender.lower()


def extract_domain(email: str) -> str:
    """Extract domain from email address"""
    if '@' in email:
        return email.split('@')[1].lower()
    return email.lower()


def group_messages_by_sender(messages: List[EmailMessage]) -> List[MessageGroup]:
    """Group messages by sender email address
    
    Args:
        messages: List of EmailMessage objects
        
    Returns:
        List of MessageGroup objects, sorted by latest message date (most recent first)
    """
    groups_dict: Dict[str, List[EmailMessage]] = {}
    
    for message in messages:
        message.received_date = normalize_received_at_utc(message.received_date)
        sender_email = extract_sender_email(message.sender)
        if sender_email not in groups_dict:
            groups_dict[sender_email] = []
        groups_dict[sender_email].append(message)
    
    # Create MessageGroup objects
    groups = []
    for sender_email, msg_list in groups_dict.items():
        # Sort messages within group by date (most recent first)
        msg_list.sort(key=lambda m: m.received_date, reverse=True)
        
        sender_domain = extract_domain(sender_email)
        group = MessageGroup(
            sender_email=sender_email,
            sender_domain=sender_domain,
            messages=msg_list,
            content_type=ContentType.UNCLASSIFIED  # TODO: Implement content analysis
        )
        groups.append(group)
    
    # Sort groups by latest message date (most recent first)
    groups.sort(key=lambda g: g.latest_date, reverse=True)

    return groups


def merge_groups_by_domain(
    sender_groups: List[MessageGroup],
    is_personal_mailbox_domain: Callable[[str], bool],
) -> List[MessageGroup]:
    """Merge per-sender groups sharing a non-personal-mailbox domain into
    one MessageGroup each, for the desktop client's opt-in "Group by
    Domain" display mode.

    Takes already-computed per-sender groups (e.g. group_messages_by_sender()'s
    output), not raw messages -- this is a pure display-layer merge, kept
    decoupled from SenderCategorizationManager (the source of
    is_personal_mailbox_domain) by taking that check as a plain callable
    rather than importing it. Categorization/inference must keep running
    against the real per-sender groups regardless of which mode is
    displayed -- see SenderCategorizationManager.infer_and_store_groups(),
    which would corrupt its per-sender inference storage if ever handed a
    domain-merged group directly (it keys real per-sender data off
    group.sender_email/group.messages[0].sender, neither of which
    represents a single real sender once merged).

    Personal/consumer webmail domains (gmail.com, yahoo.com, outlook.com,
    etc.) are left as individual per-sender groups -- merging every
    gmail.com sender into one group would be meaningless noise, not an
    organization. A non-personal domain contributed by only one
    sender_group is passed through unchanged rather than pointlessly
    rebuilt -- this is also what makes a domain "group" that happens to
    contain only one real sender indistinguishable in shape from a normal
    per-sender group (its sender_emails is a 1-tuple either way).
    """
    by_domain: Dict[str, List[MessageGroup]] = {}
    solo: List[MessageGroup] = []
    for group in sender_groups:
        domain = group.sender_domain.lower()
        if is_personal_mailbox_domain(domain):
            solo.append(group)
        else:
            by_domain.setdefault(domain, []).append(group)

    merged: List[MessageGroup] = list(solo)
    for domain, groups_for_domain in by_domain.items():
        if len(groups_for_domain) == 1:
            merged.append(groups_for_domain[0])
            continue

        all_messages = [m for g in groups_for_domain for m in g.messages]
        all_messages.sort(key=lambda m: m.received_date, reverse=True)
        merged.append(MessageGroup(
            sender_email=domain,
            sender_domain=domain,
            messages=all_messages,
            sender_emails=tuple(sorted(g.sender_email for g in groups_for_domain)),
        ))

    merged.sort(key=lambda g: g.latest_date, reverse=True)
    return merged
