from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest

from email_client.utils.sender_categorization import (
    ImpactInference,
    ImpactLevel,
    SenderCategorizationManager,
)
from email_client.utils.sender_categorization_rules import load_sender_categorization_rules


@pytest.fixture(scope="module", autouse=True)
def _require_bundled_encrypted_sender_rules() -> Iterator[None]:
    """Force decrypt path only: ignore env + local active.json so tests match shipped defaults."""
    mp = pytest.MonkeyPatch()
    try:
        mp.delenv("BRIEFKORB_SENDER_RULES_ACTIVE_JSON", raising=False)
        mp.delenv("BRIEFKORB_SENDER_RULES_JSON", raising=False)
        import email_client.utils.sender_categorization_rules as scr

        mp.setattr(
            scr,
            "_ACTIVE_JSON_PATH",
            Path("/__briefkorb_tests_no_active_json__/sender_rules.active.json"),
        )
        rules = load_sender_categorization_rules()
        if not rules.bulk_domain_markers or not rules.high_security_markers:
            pytest.fail(
                "Bundled encrypted sender rules missing or failed to decrypt. "
                "Build app/email_client/utils/data/sender_categorization_rules_default.enc with:\n"
                "  python app/scripts/encrypt_default_sender_categorization_rules.py\n"
                "(from repo root with app on PYTHONPATH, or from app/ per script docstring.)"
            )
        yield
    finally:
        mp.undo()


@dataclass
class FakeCache:
    data: Dict[str, Any]
    store_count: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any, **kwargs: Any) -> None:
        self.data[key] = value

    def store(self) -> None:
        self.store_count += 1


@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> FakeCache:
    cache = FakeCache(data={})
    monkeypatch.setattr(
        "email_client.utils.sender_categorization.get_app_info_cache",
        lambda storage_path: cache,
    )
    return cache


def test_generic_high_impact_score_without_blocklist(fake_cache: FakeCache) -> None:
    manager = SenderCategorizationManager(storage_path="ignored")

    inference = manager.infer_for_sender(
        "alerts@bank.com",
        ["Security alert: reset your password now"],
    )

    assert inference.impact == ImpactLevel.HIGH_IMPACT
    assert inference.generic_inference_score == pytest.approx(0.8)
    assert inference.blocklist_inference_score == pytest.approx(0.0)


def test_blocked_sender_gets_max_blocklist_score(fake_cache: FakeCache) -> None:
    fake_cache.data["blocked_senders"] = ["spam@ads.example"]
    manager = SenderCategorizationManager(storage_path="ignored")

    inference = manager.infer_for_sender("spam@ads.example", ["Weekly newsletter"])

    assert inference.impact == ImpactLevel.LOW_IMPACT
    assert inference.blocklist_inference_score == pytest.approx(1.0)
    assert "blocklist evidence" in inference.reason


def test_blocked_event_history_can_drive_low_impact(fake_cache: FakeCache) -> None:
    fake_cache.data["blocked_sender_events"] = [
        {"sender": f"sender{i}@promo.example"} for i in range(7)
    ]
    manager = SenderCategorizationManager(storage_path="ignored")

    inference = manager.infer_for_sender("new@promo.example", ["Hello there"])

    assert inference.blocklist_inference_score == pytest.approx(0.7)
    assert inference.impact == ImpactLevel.LOW_IMPACT


def test_manual_exception_prevents_inferred_overwrite(fake_cache: FakeCache) -> None:
    manager = SenderCategorizationManager(storage_path="ignored")
    sender = "ceo@example.com"

    manager.set_sender_exception(sender, ImpactLevel.HIGH_IMPACT)
    manager.set_inferred_sender_impact(
        sender,
        ImpactInference(
            impact=ImpactLevel.LOW_IMPACT,
            reason="test",
            confidence=0.9,
            generic_inference_score=0.9,
            blocklist_inference_score=0.9,
            bot_spam_inference_score=0.0,
            decision_trace=("test",),
        ),
    )

    senders = fake_cache.data.get(SenderCategorizationManager.SENDERS_KEY, {})
    assert sender not in senders
    assert manager.get_sender_impact(sender) == ImpactLevel.HIGH_IMPACT


def test_scores_are_persisted_in_sender_records(fake_cache: FakeCache) -> None:
    manager = SenderCategorizationManager(storage_path="ignored")
    sender = "offers@marketing.example"
    inference = manager.infer_for_sender(sender, ["Special sale offer"])
    manager.set_inferred_sender_impact(sender, inference)

    record = next(r for r in manager.list_sender_records() if r["sender"] == sender)
    assert record["generic_inference_score"] == pytest.approx(inference.generic_inference_score)
    assert record["blocklist_inference_score"] == pytest.approx(inference.blocklist_inference_score)
    assert record["bot_spam_inference_score"] == pytest.approx(inference.bot_spam_inference_score)


def test_bot_spam_randomized_sender_and_unicode_content(fake_cache: FakeCache) -> None:
    manager = SenderCategorizationManager(storage_path="ignored")

    inference = manager.infer_for_sender(
        "x7q9vz3m1n8k4p2r6t5b0c9f@safe-mail.example",
        ["Your account update"],
        display_name="Trusted Payroll Team",
        content_samples=["urgent verify http://example.test 𝕏𝕐𝕫 ⚠️⚠️⚠️"],
    )

    assert inference.bot_spam_inference_score >= 0.65
    assert inference.impact == ImpactLevel.LOW_IMPACT
    assert "mismatch" in inference.reason or "randomized" in inference.reason


def test_substack_domain_classified_low_not_high(fake_cache: FakeCache) -> None:
    manager = SenderCategorizationManager(storage_path="ignored")
    inference = manager.infer_for_sender(
        "author@substack.com",
        ["The weekly essay"],
    )
    assert inference.impact == ImpactLevel.LOW_IMPACT
    assert any("bulk" in t for t in inference.decision_trace)


def test_financial_noreply_still_high_impact(fake_cache: FakeCache) -> None:
    manager = SenderCategorizationManager(storage_path="ignored")
    inference = manager.infer_for_sender(
        "noreply@mybank.example",
        ["Your credit card minimum payment is due"],
    )
    assert inference.impact == ImpactLevel.HIGH_IMPACT


def test_personal_mac_mailbox_inclusion(fake_cache: FakeCache) -> None:
    manager = SenderCategorizationManager(storage_path="ignored")
    inference = manager.infer_for_sender(
        "mom@mac.com",
        ["Re: Sunday dinner"],
    )
    assert inference.impact == ImpactLevel.HIGH_IMPACT
    assert any("personal_mailbox" in t for t in inference.decision_trace)


def test_display_name_mismatch_increases_bot_spam_score(fake_cache: FakeCache) -> None:
    manager = SenderCategorizationManager(storage_path="ignored")

    inference = manager.infer_for_sender(
        "noreply@notifications.example",
        ["Monthly system summary"],
        display_name="Bitcoin Recovery Desk",
    )

    assert inference.bot_spam_inference_score > 0.0


def test_rules_path_uses_only_that_json_not_encrypted_defaults(tmp_path: Path) -> None:
    """When an active rules file is provided, bundled .enc is not consulted."""
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "bulk_domain_markers": ["unique-bulk-test.example"],
                "bulk_subject_markers": [],
                "high_security_markers": [],
                "financial_inclusion_markers": [],
                "personal_mailbox_domains": [],
                "automation_local_markers": [],
                "low_impact_domain_parts": [],
                "low_impact_subject_terms": [],
            }
        ),
        encoding="utf-8",
    )
    rules = load_sender_categorization_rules(rules_path=path)
    assert rules.bulk_domain_markers == ("unique-bulk-test.example",)
    assert rules.high_security_markers == ()
