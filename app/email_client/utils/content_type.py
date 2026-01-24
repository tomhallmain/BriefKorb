"""
Content type classification for email messages
"""

from enum import Enum


class ContentType(Enum):
    """Enumeration of email content types for classification"""
    MALICIOUS_SPAM = "malicious_spam"
    GENERIC_AD = "generic_ad"
    NUANCED_AD = "nuanced_ad"
    GENUINE_COMMUNICATION = "genuine_communication"
    UNCLASSIFIED = "unclassified"  # Default until content analysis is implemented
