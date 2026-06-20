from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from email_server import EmailMessage


@dataclass
class ExtractedJobPosting:
    title: Optional[str]
    hiring_org: Optional[str]
    location: Optional[str]
    apply_url: Optional[str]
    salary: Optional[str]
    confidence: float
    signals: Tuple[str, ...] = field(default_factory=tuple)


class BaseExtractor(ABC):
    @abstractmethod
    def extract(self, message: "EmailMessage") -> List[ExtractedJobPosting]:
        """Return zero or more job postings found in the message."""
