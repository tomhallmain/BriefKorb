"""
Sender/group impact categorization framework backed by encrypted app cache.

Each inference stores a ``decision_trace`` list on the sender record (see
``list_sender_records`` / ``get_sender_decision_trace``). A rolling audit log
of recent runs lives in the encrypted cache under ``INFERENCE_AUDIT_KEY``;
inspect it with ``get_inference_audit_tail()`` from scripts or a REPL.

TODO: Parse the user's junk folder to add a second-opinion spam signal from
provider-classified junk mail before finalizing bot/spam inference decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from email.utils import parseaddr
import re
from typing import Any, Dict, Iterable, List, Tuple

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
    decision_trace: Tuple[str, ...] = ()


class SenderCategorizationManager:
    """Stores inferred + explicit sender impact categories in encrypted cache."""

    SENDERS_KEY = "sender_impact_by_sender"
    GROUPS_KEY = "sender_group_impact_by_domain"
    EXCEPTIONS_KEY = "sender_impact_exceptions"
    BLOCKED_SENDERS_KEY = "blocked_senders"
    BLOCKED_EVENTS_KEY = "blocked_sender_events"
    INFERENCE_AUDIT_KEY = "sender_impact_inference_audit_log"
    _AUDIT_MAX = 2000

    # Domains / paths typical of newsletters, retail promos, or bulk civic mail (not account-critical).
    _BULK_DOMAIN_MARKERS: Tuple[str, ...] = (
        "substack.com",
        "beehiiv.com",
        "microcenter.com",
        "flickr.com",
        "tubitv.com",
        "mail.house.gov",
    )
    _BULK_SUBJECT_MARKERS: Tuple[str, ...] = (
        "here is what you need to know",
        "weekly digest",
    )
    _HIGH_SECURITY_MARKERS: Tuple[str, ...] = (
        "security alert",
        "password reset",
        "reset your password",
        "two-factor",
        "2fa",
        "one-time code",
        "authentication code",
        "verification code",
        "sign-in attempt",
        "sign in attempt",
        "unusual activity",
        "suspicious activity",
        "fraud alert",
        "account locked",
        "account action required",
    )
    _FINANCIAL_INCLUSION_MARKERS: Tuple[str, ...] = (
        "payment due",
        "minimum payment",
        "payment received",
        "payment posted",
        "statement is available",
        "statement available",
        "credit card",
        "autopay",
        "account balance",
        "overdraft",
        "routing number",
        "direct deposit",
        "ach transfer",
        "wire transfer",
        "loan payment",
        "mortgage payment",
        "debit card",
        "credit union",
    )
    _PERSONAL_MAILBOX_DOMAINS: Tuple[str, ...] = ("mac.com", "icloud.com", "me.com")
    _AUTOMATION_LOCAL_MARKERS: Tuple[str, ...] = (
        "noreply",
        "no-reply",
        "donotreply",
        "mailer",
        "bounces",
        "newsletter",
        "promo",
        "notifications",
    )

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

    def clear_all_inferred_categories(self) -> None:
        """Remove all inferred sender/domain impact data (manual exceptions unchanged)."""
        self._cache.set(self.SENDERS_KEY, {}, force=True)
        self._cache.set(self.GROUPS_KEY, {}, force=True)
        self._cache.store()

    def get_sender_decision_trace(self, sender_email: str) -> List[str]:
        sender = sender_email.lower().strip()
        inferred = self._get_dict(self.SENDERS_KEY).get(sender, {})
        raw = inferred.get("decision_trace", [])
        return list(raw) if isinstance(raw, list) else []

    def get_inference_audit_tail(self, limit: int = 200) -> List[Dict[str, Any]]:
        log = self._cache.get(self.INFERENCE_AUDIT_KEY, [])
        if not isinstance(log, list):
            return []
        return log[-limit:]

    def set_inferred_sender_impact(self, sender_email: str, inference: ImpactInference) -> None:
        sender = sender_email.lower().strip()
        exceptions = self._get_dict(self.EXCEPTIONS_KEY)
        if sender in exceptions:
            return

        senders = self._get_dict(self.SENDERS_KEY)
        senders[sender] = self._inference_record(inference)
        self._cache.set(self.SENDERS_KEY, senders)
        self._cache.store()

    def set_inferred_group_impact(self, sender_domain: str, inference: ImpactInference) -> None:
        domain = sender_domain.lower().strip()
        groups = self._get_dict(self.GROUPS_KEY)
        groups[domain] = self._inference_record(inference)
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

    def _bulk_newsletter_exclusion(self, sender: str, domain: str, haystack: str) -> Tuple[bool, str]:
        domain = domain.lower()
        hl = haystack.lower()
        for marker in self._BULK_DOMAIN_MARKERS:
            if marker in domain:
                return True, f"bulk/newsletter domain marker ({marker})"
        for marker in self._BULK_SUBJECT_MARKERS:
            if marker in hl:
                return True, f"bulk/newsletter subject marker ({marker})"
        return False, ""

    def _financial_inclusion(self, haystack: str) -> Tuple[bool, str]:
        hl = haystack.lower()
        hits = [m for m in self._FINANCIAL_INCLUSION_MARKERS if m in hl]
        if hits:
            return True, f"financial/payment signals ({hits[0]})"
        return False, ""

    def _personal_mailbox_inclusion(self, sender: str, domain: str) -> Tuple[bool, str]:
        domain = domain.lower()
        if domain not in self._PERSONAL_MAILBOX_DOMAINS:
            return False, ""
        local = sender.split("@", 1)[0].lower() if "@" in sender else sender.lower()
        if any(m in local for m in self._AUTOMATION_LOCAL_MARKERS):
            return False, ""
        return True, f"likely personal mailbox ({domain})"

    def _infer_from_sender_data(self, sender: str, domain: str, haystack: str, display_name: str = "") -> ImpactInference:
        trace: List[str] = []
        hl = haystack.lower()
        domain_l = domain.lower()
        sender_l = sender.lower()

        blocklist_score = self._score_blocklist_evidence(sender_l, domain_l)
        trace.append(f"blocklist_score={blocklist_score:.2f}")

        generic_score = 0.0
        if blocklist_score >= 0.7:
            trace.append("decision:blocklist_forces_low")
            return ImpactInference(
                ImpactLevel.LOW_IMPACT,
                "historical blocklist evidence indicates low-impact sender",
                max(0.75, blocklist_score),
                generic_score,
                blocklist_score,
                0.0,
                tuple(trace),
            )

        bulk_hit, bulk_reason = self._bulk_newsletter_exclusion(sender_l, domain_l, hl)
        if bulk_hit:
            trace.append(f"bulk_exclusion:{bulk_reason}")
            trace.append("decision:bulk_newsletter_low")
            return ImpactInference(
                ImpactLevel.LOW_IMPACT,
                bulk_reason,
                0.82,
                0.15,
                blocklist_score,
                0.0,
                tuple(trace),
            )

        bot_spam_score, bot_reason = self._score_bot_spam_evidence(sender_l, domain_l, haystack, display_name)
        trace.append(f"bot_spam_score={bot_spam_score:.2f}")
        if bot_spam_score >= 0.65:
            trace.append("decision:bot_spam_low")
            generic_score = 0.0
            return ImpactInference(
                ImpactLevel.LOW_IMPACT,
                bot_reason,
                max(0.7, bot_spam_score),
                generic_score,
                blocklist_score,
                bot_spam_score,
                tuple(trace),
            )

        low_impact_domains = ("news", "mailer", "marketing", "promotions", "updates")
        low_impact_terms = ("unsubscribe", "sale", "offer", "sponsored", "promo", "limited time")
        high_generic_score = 0.0
        low_generic_score = 0.0
        generic_reason = "insufficient confidence"

        if any(term in hl for term in self._HIGH_SECURITY_MARKERS):
            high_generic_score = 0.8
            generic_reason = "contains account/security markers"
            trace.append("signal:high_security_markers")

        fin_hit, fin_reason = self._financial_inclusion(hl)
        if fin_hit:
            high_generic_score = max(high_generic_score, 0.78)
            trace.append(f"inclusion:financial:{fin_reason}")

        pers_hit, pers_reason = self._personal_mailbox_inclusion(sender_l, domain_l)
        if pers_hit:
            high_generic_score = max(high_generic_score, 0.72)
            trace.append(f"inclusion:personal_mailbox:{pers_reason}")

        if any(term in hl for term in low_impact_terms):
            low_generic_score = max(low_generic_score, 0.72)
            generic_reason = "contains promotional terms"
            trace.append("signal:promotional_terms")
        elif any(part in domain_l for part in low_impact_domains):
            low_generic_score = max(low_generic_score, 0.65)
            generic_reason = "sender domain resembles marketing/bulk sender"
            trace.append("signal:marketing_domain_shape")
        elif any(m in sender_l for m in self._AUTOMATION_LOCAL_MARKERS) and high_generic_score < 0.65 and not fin_hit:
            low_generic_score = max(low_generic_score, 0.6)
            generic_reason = "automated sender address without strong account signals"
            trace.append("signal:automation_local_part")

        generic_score = max(high_generic_score, low_generic_score)
        trace.append(
            f"phase2_scores high={high_generic_score:.2f} low={low_generic_score:.2f} generic_max={generic_score:.2f}"
        )

        if high_generic_score >= 0.7 and high_generic_score >= low_generic_score:
            trace.append("decision:high_impact")
            return ImpactInference(
                ImpactLevel.HIGH_IMPACT,
                generic_reason,
                high_generic_score,
                generic_score,
                blocklist_score,
                bot_spam_score,
                tuple(trace),
            )
        if low_generic_score >= 0.6 and low_generic_score > high_generic_score:
            trace.append("decision:low_impact_promotional")
            return ImpactInference(
                ImpactLevel.LOW_IMPACT,
                generic_reason,
                low_generic_score,
                generic_score,
                blocklist_score,
                bot_spam_score,
                tuple(trace),
            )
        trace.append("decision:unclassified")
        return ImpactInference(
            ImpactLevel.UNCLASSIFIED,
            "insufficient confidence",
            0.0,
            generic_score,
            blocklist_score,
            bot_spam_score,
            tuple(trace),
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

    def _inference_record(self, inference: ImpactInference) -> Dict[str, Any]:
        return {
            "impact": inference.impact.value,
            "reason": inference.reason,
            "confidence": inference.confidence,
            "generic_inference_score": inference.generic_inference_score,
            "blocklist_inference_score": inference.blocklist_inference_score,
            "bot_spam_inference_score": inference.bot_spam_inference_score,
            "decision_trace": list(inference.decision_trace),
            "source": "inferred",
            "updated_at": self._now(),
        }

    def infer_and_store_groups(self, groups: Iterable[MessageGroup]) -> None:
        """Persist inferred impact for all groups with a single cache write.

        Per-group ``store()`` calls were very slow (full encrypt + disk each time)
        after a refresh with many senders; batching keeps behavior equivalent.
        """
        groups_list = list(groups)
        if not groups_list:
            return

        senders = {k: dict(v) for k, v in self._get_dict(self.SENDERS_KEY).items()}
        groups_by_domain = {k: dict(v) for k, v in self._get_dict(self.GROUPS_KEY).items()}
        exceptions = self._get_dict(self.EXCEPTIONS_KEY)
        audit_entries: List[Dict[str, Any]] = []

        for group in groups_list:
            inference = self.infer_for_group(group)
            record = self._inference_record(inference)
            domain = group.sender_domain.lower().strip()
            groups_by_domain[domain] = dict(record)

            sender = group.sender_email.lower().strip()
            if sender not in exceptions:
                senders[sender] = dict(record)

            audit_entries.append(
                {
                    "updated_at": self._now(),
                    "sender": sender,
                    "domain": domain,
                    "impact": inference.impact.value,
                    "reason": inference.reason,
                    "trace": list(inference.decision_trace),
                }
            )

        audit_log = self._cache.get(self.INFERENCE_AUDIT_KEY, [])
        if not isinstance(audit_log, list):
            audit_log = []
        audit_log.extend(audit_entries)
        if len(audit_log) > self._AUDIT_MAX:
            audit_log = audit_log[-self._AUDIT_MAX :]

        self._cache.set(self.SENDERS_KEY, senders, force=True)
        self._cache.set(self.GROUPS_KEY, groups_by_domain, force=True)
        self._cache.set(self.INFERENCE_AUDIT_KEY, audit_log, force=True)
        self._cache.store()

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
                    "decision_trace": inferred.get("decision_trace", []),
                    "has_exception": sender in exceptions,
                    "inferred_impact": inferred.get("impact", ImpactLevel.UNCLASSIFIED.value),
                }
            )
        return records
