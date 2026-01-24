"""
Message grouping utilities for bundling messages by sender
"""

from typing import List, Dict
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
import re

from email_server import EmailMessage
from .content_type import ContentType


@dataclass
class MessageGroup:
    """Represents a group of messages from the same sender"""
    sender_email: str
    sender_domain: str
    messages: List[EmailMessage]
    content_type: ContentType = ContentType.UNCLASSIFIED
    
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
