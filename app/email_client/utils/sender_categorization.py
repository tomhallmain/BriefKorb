"""
Sender/group impact categorization framework backed by encrypted app cache.

TODO: Parse the user's junk folder to add a second-opinion spam signal from
provider-classified junk mail before finalizing bot/spam inference decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from email.utils import parseaddr
import re
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
    generic_inference_score: float = 0.0
    blocklist_inference_score: float = 0.0
    bot_spam_inference_score: float = 0.0


class SenderCategorizationManager:
    """Stores inferred + explicit sender impact categories in encrypted cache."""

    SENDERS_KEY = "sender_impact_by_sender"
    GROUPS_KEY = "sender_group_impact_by_domain"
    EXCEPTIONS_KEY = "sender_impact_exceptions"
    BLOCKED_SENDERS_KEY = "blocked_senders"
    BLOCKED_EVENTS_KEY = "blocked_sender_events"

    def __init__(self, storage_path: str):
        self._cache = get_app_info_cache(storage_path)

    def _get_dict(self, key: str) -> Dict[str, Dict[str, Any]]:
        value = self._cache.get(key, {})
        return value if isinstance(value, dict) else {}

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def has_sender_exception(self, sender_email: str) -> bool:
        sender = sender_email.lower().strip()
        exceptions = self._get_dict(self.EXCEPTIONS_KEY)
        return sender in exceptions

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
            "generic_inference_score": inference.generic_inference_score,
            "blocklist_inference_score": inference.blocklist_inference_score,
            "bot_spam_inference_score": inference.bot_spam_inference_score,
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
            "generic_inference_score": inference.generic_inference_score,
            "blocklist_inference_score": inference.blocklist_inference_score,
            "bot_spam_inference_score": inference.bot_spam_inference_score,
            "source": "inferred",
            "updated_at": self._now(),
        }
        self._cache.set(self.GROUPS_KEY, groups)
        self._cache.store()

    def infer_for_group(self, group: MessageGroup) -> ImpactInference:
        sender = group.sender_email.lower()
        domain = group.sender_domain.lower()
        display_name, _ = parseaddr(group.messages[0].sender) if group.messages else ("", "")
        subjects = [((m.subject or "").lower()) for m in group.messages[:5]]
        bodies = [((m.body or "").lower()) for m in group.messages[:3]]
        haystack = " ".join(subjects + bodies)
        return self._infer_from_sender_data(sender, domain, haystack, display_name)

    def infer_for_sender(
        self,
        sender_email: str,
        subjects: List[str],
        display_name: str = "",
        content_samples: List[str] | None = None,
    ) -> ImpactInference:
        sender = sender_email.lower().strip()
        domain = sender.split("@")[1].lower() if "@" in sender else sender
        content_samples = content_samples or []
        haystack = " ".join((s or "").lower() for s in (subjects[:5] + content_samples[:3]))
        return self._infer_from_sender_data(sender, domain, haystack, display_name)

    def _infer_from_sender_data(self, sender: str, domain: str, haystack: str, display_name: str = "") -> ImpactInference:
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
        high_generic_score = 0.0
        low_generic_score = 0.0
        generic_reason = "insufficient confidence"

        if any(term in haystack for term in high_impact_terms):
            high_generic_score = 0.8
            generic_reason = "contains account/security action terms"
        elif any(term in haystack for term in low_impact_terms):
            low_generic_score = 0.75
            generic_reason = "contains promotional/subscription terms"
        elif any(part in domain for part in low_impact_domains):
            low_generic_score = 0.65
            generic_reason = "sender domain resembles marketing/bulk sender"
        elif "noreply" in sender or "no-reply" in sender:
            low_generic_score = 0.6
            generic_reason = "automated no-reply sender"

        generic_score = max(high_generic_score, low_generic_score)
        blocklist_score = self._score_blocklist_evidence(sender, domain)
        bot_spam_score, bot_reason = self._score_bot_spam_evidence(sender, domain, haystack, display_name)

        if blocklist_score >= 0.7:
            confidence = max(0.75, blocklist_score)
            return ImpactInference(
                ImpactLevel.LOW_IMPACT,
                "historical blocklist evidence indicates low-impact sender",
                confidence,
                generic_score,
                blocklist_score,
                bot_spam_score,
            )
        if bot_spam_score >= 0.65:
            confidence = max(0.7, bot_spam_score)
            return ImpactInference(
                ImpactLevel.LOW_IMPACT,
                bot_reason,
                confidence,
                generic_score,
                blocklist_score,
                bot_spam_score,
            )
        if high_generic_score >= 0.7 and high_generic_score > low_generic_score:
            return ImpactInference(
                ImpactLevel.HIGH_IMPACT,
                generic_reason,
                high_generic_score,
                generic_score,
                blocklist_score,
                bot_spam_score,
            )
        if low_generic_score >= 0.6:
            return ImpactInference(
                ImpactLevel.LOW_IMPACT,
                generic_reason,
                low_generic_score,
                generic_score,
                blocklist_score,
                bot_spam_score,
            )
        return ImpactInference(
            ImpactLevel.UNCLASSIFIED,
            "insufficient confidence",
            0.0,
            generic_score,
            blocklist_score,
            bot_spam_score,
        )

    def _score_bot_spam_evidence(self, sender: str, domain: str, haystack: str, display_name: str) -> tuple[float, str]:
        score = 0.0
        reasons: List[str] = []

        local_part = sender.split("@", 1)[0] if "@" in sender else sender
        compact_local = re.sub(r"[^a-z0-9]", "", local_part.lower())
        if len(compact_local) >= 22:
            score += 0.35
            reasons.append("very long randomized local-part")
        digit_ratio = (sum(1 for ch in compact_local if ch.isdigit()) / len(compact_local)) if compact_local else 0.0
        if digit_ratio >= 0.25:
            score += 0.25
            reasons.append("high digit ratio in sender local-part")
        unique_ratio = (len(set(compact_local)) / len(compact_local)) if compact_local else 0.0
        if len(compact_local) >= 14 and unique_ratio >= 0.75:
            score += 0.2
            reasons.append("high character randomness in sender local-part")

        if display_name:
            display_tokens = set(re.findall(r"[a-z]{3,}", display_name.lower()))
            sender_tokens = set(re.findall(r"[a-z]{3,}", f"{local_part}.{domain}"))
            meaningful_display_tokens = {
                token for token in display_tokens
                if token not in {"team", "support", "service", "mail", "notice", "notifications"}
            }
            if meaningful_display_tokens and meaningful_display_tokens.isdisjoint(sender_tokens):
                score += 0.3
                reasons.append("display-name and sender address mismatch")

        non_ascii_chars = sum(1 for ch in haystack if ord(ch) > 127)
        total_chars = len(haystack)
        non_ascii_ratio = (non_ascii_chars / total_chars) if total_chars else 0.0
        if non_ascii_ratio >= 0.12 and non_ascii_chars >= 6:
            score += 0.25
            reasons.append("unexpected unicode ratio in message content")

        if "http://" in haystack and "verify" in haystack:
            score += 0.15
            reasons.append("suspicious verification URL pattern")

        score = min(1.0, score)
        if reasons:
            return score, "; ".join(reasons)
        return score, "insufficient bot/spam evidence"

    def _score_blocklist_evidence(self, sender: str, domain: str) -> float:
        blocked_senders = self._cache.get(self.BLOCKED_SENDERS_KEY, [])
        if not isinstance(blocked_senders, list):
            blocked_senders = []
        blocked_set = {str(entry).strip().lower() for entry in blocked_senders if str(entry).strip()}
        if sender in blocked_set:
            return 1.0

        blocked_domains = {
            entry.split("@", 1)[1]
            for entry in blocked_set
            if "@" in entry
        }
        score = 0.85 if domain in blocked_domains else 0.0

        blocked_events = self._cache.get(self.BLOCKED_EVENTS_KEY, [])
        if not isinstance(blocked_events, list):
            blocked_events = []

        sender_event_count = 0
        domain_event_count = 0
        for event in blocked_events:
            if not isinstance(event, dict):
                continue
            event_sender = str(event.get("sender", "")).strip().lower()
            if not event_sender:
                continue
            event_domain = event_sender.split("@", 1)[1] if "@" in event_sender else ""
            if event_sender == sender:
                sender_event_count += 1
            if event_domain and event_domain == domain:
                domain_event_count += 1

        sender_event_score = min(0.95, sender_event_count * 0.25)
        domain_event_score = min(0.75, domain_event_count * 0.1)
        return max(score, sender_event_score, domain_event_score)

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
                    "generic_inference_score": inferred.get("generic_inference_score", 0.0),
                    "blocklist_inference_score": inferred.get("blocklist_inference_score", 0.0),
                    "bot_spam_inference_score": inferred.get("bot_spam_inference_score", 0.0),
                    "has_exception": sender in exceptions,
                    "inferred_impact": inferred.get("impact", ImpactLevel.UNCLASSIFIED.value),
                }
            )
        return records
