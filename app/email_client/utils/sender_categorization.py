"""
Sender/group impact categorization framework backed by encrypted app cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List

from email_server.utils.app_info_cache import get_app_info_cache
from email_client.utils.message_grouping import MessageGroup


class ImpactLevel(str, Enum):
    HIGH_IMPACT = "high-impact"
    LOW_IMPACT = "low-impact"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class ImpactInference:
    impact: ImpactLevel
    reason: str
    confidence: float


class SenderCategorizationManager:
    """Stores inferred + explicit sender impact categories in encrypted cache."""

    SENDERS_KEY = "sender_impact_by_sender"
    GROUPS_KEY = "sender_group_impact_by_domain"
    EXCEPTIONS_KEY = "sender_impact_exceptions"

    def __init__(self, storage_path: str):
        self._cache = get_app_info_cache(storage_path)

    def _get_dict(self, key: str) -> Dict[str, Dict[str, Any]]:
        value = self._cache.get(key, {})
        return value if isinstance(value, dict) else {}

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_sender_impact(self, sender_email: str) -> ImpactLevel:
        sender = sender_email.lower().strip()
        exceptions = self._get_dict(self.EXCEPTIONS_KEY)
        if sender in exceptions and "impact" in exceptions[sender]:
            return ImpactLevel(exceptions[sender]["impact"])

        senders = self._get_dict(self.SENDERS_KEY)
        return ImpactLevel(senders.get(sender, {}).get("impact", ImpactLevel.UNCLASSIFIED.value))

    def is_high_impact_sender(self, sender_email: str) -> bool:
        return self.get_sender_impact(sender_email) == ImpactLevel.HIGH_IMPACT

    def is_high_impact_group(self, group: MessageGroup) -> bool:
        return self.is_high_impact_sender(group.sender_email)

    def set_sender_exception(self, sender_email: str, impact: ImpactLevel, source: str = "manual_exception") -> None:
        sender = sender_email.lower().strip()
        exceptions = self._get_dict(self.EXCEPTIONS_KEY)
        exceptions[sender] = {
            "impact": impact.value,
            "source": source,
            "updated_at": self._now(),
        }
        self._cache.set(self.EXCEPTIONS_KEY, exceptions)
        self._cache.store()

    def clear_sender_exception(self, sender_email: str) -> None:
        sender = sender_email.lower().strip()
        exceptions = self._get_dict(self.EXCEPTIONS_KEY)
        if sender in exceptions:
            del exceptions[sender]
            self._cache.set(self.EXCEPTIONS_KEY, exceptions)
            self._cache.store()

    def set_inferred_sender_impact(self, sender_email: str, inference: ImpactInference) -> None:
        sender = sender_email.lower().strip()
        exceptions = self._get_dict(self.EXCEPTIONS_KEY)
        if sender in exceptions:
            return

        senders = self._get_dict(self.SENDERS_KEY)
        senders[sender] = {
            "impact": inference.impact.value,
            "reason": inference.reason,
            "confidence": inference.confidence,
            "source": "inferred",
            "updated_at": self._now(),
        }
        self._cache.set(self.SENDERS_KEY, senders)
        self._cache.store()

    def set_inferred_group_impact(self, sender_domain: str, inference: ImpactInference) -> None:
        domain = sender_domain.lower().strip()
        groups = self._get_dict(self.GROUPS_KEY)
        groups[domain] = {
            "impact": inference.impact.value,
            "reason": inference.reason,
            "confidence": inference.confidence,
            "source": "inferred",
            "updated_at": self._now(),
        }
        self._cache.set(self.GROUPS_KEY, groups)
        self._cache.store()

    def infer_for_group(self, group: MessageGroup) -> ImpactInference:
        sender = group.sender_email.lower()
        domain = group.sender_domain.lower()
        subjects = [((m.subject or "").lower()) for m in group.messages[:5]]
        haystack = " ".join(subjects)

        low_impact_domains = ("news", "mailer", "marketing", "promotions", "updates")
        low_impact_terms = ("unsubscribe", "sale", "offer", "sponsored", "promo")
        high_impact_terms = (
            "verify",
            "security alert",
            "password reset",
            "reset your password",
            "2fa",
            "one-time code",
            "account action",
            "invoice",
        )

        if any(term in haystack for term in high_impact_terms):
            return ImpactInference(ImpactLevel.HIGH_IMPACT, "contains account/security action terms", 0.8)
        if any(term in haystack for term in low_impact_terms):
            return ImpactInference(ImpactLevel.LOW_IMPACT, "contains promotional/subscription terms", 0.75)
        if any(part in domain for part in low_impact_domains):
            return ImpactInference(ImpactLevel.LOW_IMPACT, "sender domain resembles marketing/bulk sender", 0.65)
        if "noreply" in sender or "no-reply" in sender:
            return ImpactInference(ImpactLevel.LOW_IMPACT, "automated no-reply sender", 0.6)
        return ImpactInference(ImpactLevel.UNCLASSIFIED, "insufficient confidence", 0.0)

    def infer_and_store_groups(self, groups: Iterable[MessageGroup]) -> None:
        for group in groups:
            inference = self.infer_for_group(group)
            self.set_inferred_group_impact(group.sender_domain, inference)
            self.set_inferred_sender_impact(group.sender_email, inference)

    def list_sender_records(self) -> List[Dict[str, Any]]:
        senders = self._get_dict(self.SENDERS_KEY)
        exceptions = self._get_dict(self.EXCEPTIONS_KEY)

        all_senders = sorted(set(senders.keys()) | set(exceptions.keys()))
        records: List[Dict[str, Any]] = []
        for sender in all_senders:
            inferred = senders.get(sender, {})
            override = exceptions.get(sender, {})
            effective = override if override else inferred
            records.append(
                {
                    "sender": sender,
                    "domain": sender.split("@")[1] if "@" in sender else "",
                    "impact": effective.get("impact", ImpactLevel.UNCLASSIFIED.value),
                    "source": effective.get("source", "unknown"),
                    "reason": effective.get("reason", ""),
                    "confidence": effective.get("confidence"),
                    "has_exception": sender in exceptions,
                    "inferred_impact": inferred.get("impact", ImpactLevel.UNCLASSIFIED.value),
                }
            )
        return records
