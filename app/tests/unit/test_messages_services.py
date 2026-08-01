"""Tests for django_app/messages/services.py's annotate_sender_impact.

This is the only thing left in services.py after MessagesService (a
Microsoft-only class that duplicated MicrosoftGraphProvider's Graph calls)
was retired in favor of UnifiedEmailServer -- see services.py's module
docstring. Its former methods now have coverage under their new homes:
aggregation -> test_unified_email_server.py (get_message_digest), fetching
-> test_microsoft_provider.py/test_gmail_provider.py (get_messages/
get_message), mark-as-read/delete/block -> test_unified_email_server.py +
the provider test files (mark_as_read/delete_messages/block_senders).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from django_app.messages.services import annotate_sender_impact
from email_client.utils.sender_categorization import ImpactInference, ImpactLevel


@dataclass
class FakeSenderCategorizationManager:
    """Double for SenderCategorizationManager -- records calls and returns
    canned inferences/impacts, matching the real interface annotate_sender_impact uses."""
    impacts: Dict[str, ImpactLevel] = field(default_factory=dict)
    exceptions: Dict[str, ImpactLevel] = field(default_factory=dict)
    inferred_calls: List[str] = field(default_factory=list)

    def infer_for_sender(self, sender_email: str, subjects: List[str]) -> ImpactInference:
        self.inferred_calls.append(sender_email)
        return ImpactInference(
            impact=self.impacts.get(sender_email, ImpactLevel.UNCLASSIFIED),
            reason='fake reason',
            confidence=0.5,
            generic_inference_score=0.1,
            blocklist_inference_score=0.2,
            bot_spam_inference_score=0.3,
        )

    def set_inferred_sender_impact(self, sender_email: str, inference: ImpactInference) -> None:
        self.impacts[sender_email] = inference.impact

    def get_sender_impact(self, sender_email: str) -> ImpactLevel:
        return self.impacts.get(sender_email, ImpactLevel.UNCLASSIFIED)

    def has_sender_exception(self, sender_email: str) -> bool:
        return sender_email in self.exceptions


def test_annotate_sender_impact_adds_impact_and_scores() -> None:
    fake_manager = FakeSenderCategorizationManager(impacts={'alice@example.com': ImpactLevel.HIGH_IMPACT})

    result = annotate_sender_impact([
        {'fromAddress': 'Alice@Example.com', 'subject': 'Hi'},
    ], fake_manager)

    assert result[0]['impact'] == ImpactLevel.HIGH_IMPACT.value
    assert result[0]['genericInferenceScore'] == 0.1
    assert result[0]['blocklistInferenceScore'] == 0.2
    assert result[0]['botSpamInferenceScore'] == 0.3
    # Lookup happens on the normalized (lowercased/stripped) address.
    assert fake_manager.inferred_calls == ['alice@example.com']


def test_annotate_sender_impact_skips_entries_with_no_address() -> None:
    fake_manager = FakeSenderCategorizationManager()

    result = annotate_sender_impact([{'fromAddress': '', 'subject': 'Hi'}], fake_manager)

    assert 'impact' not in result[0]
    assert fake_manager.inferred_calls == []


def test_annotate_sender_impact_marks_has_exception_when_overridden() -> None:
    fake_manager = FakeSenderCategorizationManager(exceptions={'alice@example.com': ImpactLevel.HIGH_IMPACT})

    result = annotate_sender_impact([{'fromAddress': 'alice@example.com', 'subject': 'Hi'}], fake_manager)

    assert result[0]['hasImpactException'] is True


def test_annotate_sender_impact_returns_the_same_list_object() -> None:
    fake_manager = FakeSenderCategorizationManager()
    message_data = [{'fromAddress': 'alice@example.com', 'subject': 'Hi'}]

    result = annotate_sender_impact(message_data, fake_manager)

    assert result is message_data
