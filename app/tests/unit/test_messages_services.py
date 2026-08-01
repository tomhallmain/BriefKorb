"""Tests for django_app/messages/services.py's MessagesService.

MessagesService.__init__ does a lot of real work (loads email_server/config.yaml
from a fixed on-disk path, constructs TokenManager/MicrosoftOAuth/provider/
SenderCategorizationManager/entity graph). That constructor path isn't
exercised here -- these tests build bare instances via __new__ and set only
the attributes each method under test actually touches, which is enough to
exercise the real business logic (aggregation, filtering, retry/threading,
error handling) without needing a real config.yaml or live Graph credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from django_app.messages import services as services_module
from django_app.messages.services import MessagesService
from email_client.utils.sender_categorization import ImpactInference, ImpactLevel


def _bare_service() -> MessagesService:
    """A MessagesService instance with __init__ skipped -- see module docstring."""
    return MessagesService.__new__(MessagesService)


# --- aggregate_messages_by_sender (pure) ------------------------------------

def test_aggregate_groups_by_sender_name_and_counts() -> None:
    service = _bare_service()
    messages = [
        {'from': {'emailAddress': {'name': 'Alice', 'address': 'alice@example.com'}},
         'subject': 'Hi', 'receivedDateTime': '2024-01-01T00:00:00Z'},
        {'from': {'emailAddress': {'name': 'Alice', 'address': 'alice@example.com'}},
         'subject': 'Hi again', 'receivedDateTime': '2024-01-02T00:00:00Z'},
        {'from': {'emailAddress': {'name': 'Bob', 'address': 'bob@example.com'}},
         'subject': 'Yo', 'receivedDateTime': '2024-01-01T00:00:00Z'},
    ]

    result = service.aggregate_messages_by_sender(messages)

    by_name = {m['fromName']: m for m in result}
    assert by_name['Alice']['count'] == 2
    assert by_name['Bob']['count'] == 1
    # First-seen subject/date is kept, not overwritten by later messages from the same sender.
    assert by_name['Alice']['subject'] == 'Hi'


def test_aggregate_sorts_by_count_descending_then_name() -> None:
    service = _bare_service()
    messages = [
        {'from': {'emailAddress': {'name': 'Zed', 'address': 'z@example.com'}}, 'subject': 's', 'receivedDateTime': 'd'},
        {'from': {'emailAddress': {'name': 'Amy', 'address': 'a@example.com'}}, 'subject': 's', 'receivedDateTime': 'd'},
        {'from': {'emailAddress': {'name': 'Amy', 'address': 'a@example.com'}}, 'subject': 's', 'receivedDateTime': 'd'},
    ]

    result = service.aggregate_messages_by_sender(messages)

    assert [m['fromName'] for m in result] == ['Amy', 'Zed']


def test_aggregate_defaults_missing_fields() -> None:
    service = _bare_service()
    result = service.aggregate_messages_by_sender([{}])

    assert result[0]['fromName'] == 'Unknown'
    assert result[0]['fromAddress'] == ''
    assert result[0]['subject'] == '(No subject)'
    assert result[0]['lastReceivedDateTime'] == ''


def test_aggregate_skips_malformed_message_entries() -> None:
    service = _bare_service()
    # 'from' is a list here instead of a dict -- .get('emailAddress') would raise AttributeError.
    messages = [
        {'from': ['not', 'a', 'dict'], 'subject': 'broken'},
        {'from': {'emailAddress': {'name': 'Ok', 'address': 'ok@example.com'}}, 'subject': 'fine', 'receivedDateTime': 'd'},
    ]

    result = service.aggregate_messages_by_sender(messages)

    assert len(result) == 1
    assert result[0]['fromName'] == 'Ok'


def test_aggregate_empty_input_returns_empty_list() -> None:
    service = _bare_service()
    assert service.aggregate_messages_by_sender([]) == []


# --- annotate_sender_impact -------------------------------------------------

@dataclass
class FakeSenderCategorizationManager:
    """Double for SenderCategorizationManager -- records calls and returns
    canned inferences/impacts, matching the real interface used by
    annotate_sender_impact / set_sender_impact_exception."""
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

    def set_sender_exception(self, sender_email: str, impact: ImpactLevel, source: str = "manual") -> None:
        self.exceptions[sender_email] = impact

    def clear_sender_exception(self, sender_email: str) -> None:
        self.exceptions.pop(sender_email, None)


def test_annotate_sender_impact_adds_impact_and_scores() -> None:
    service = _bare_service()
    fake_manager = FakeSenderCategorizationManager(impacts={'alice@example.com': ImpactLevel.HIGH_IMPACT})
    service.sender_categorization = fake_manager

    result = service.annotate_sender_impact([
        {'fromAddress': 'Alice@Example.com', 'subject': 'Hi'},
    ])

    assert result[0]['impact'] == ImpactLevel.HIGH_IMPACT.value
    assert result[0]['genericInferenceScore'] == 0.1
    assert result[0]['blocklistInferenceScore'] == 0.2
    assert result[0]['botSpamInferenceScore'] == 0.3
    # Lookup happens on the normalized (lowercased/stripped) address.
    assert fake_manager.inferred_calls == ['alice@example.com']


def test_annotate_sender_impact_skips_entries_with_no_address() -> None:
    service = _bare_service()
    fake_manager = FakeSenderCategorizationManager()
    service.sender_categorization = fake_manager

    result = service.annotate_sender_impact([{'fromAddress': '', 'subject': 'Hi'}])

    assert 'impact' not in result[0]
    assert fake_manager.inferred_calls == []


# --- set_sender_impact_exception --------------------------------------------

def test_set_sender_impact_exception_sets_exception_for_valid_impact() -> None:
    service = _bare_service()
    fake_manager = FakeSenderCategorizationManager()
    service.sender_categorization = fake_manager

    service.set_sender_impact_exception('Spam@Example.com', 'high-impact')

    assert fake_manager.exceptions['spam@example.com'] == ImpactLevel.HIGH_IMPACT


def test_set_sender_impact_exception_clears_when_impact_is_none() -> None:
    service = _bare_service()
    fake_manager = FakeSenderCategorizationManager(exceptions={'spam@example.com': ImpactLevel.HIGH_IMPACT})
    service.sender_categorization = fake_manager

    service.set_sender_impact_exception('spam@example.com', None)

    assert 'spam@example.com' not in fake_manager.exceptions


def test_set_sender_impact_exception_no_ops_on_blank_sender() -> None:
    service = _bare_service()
    fake_manager = FakeSenderCategorizationManager()
    service.sender_categorization = fake_manager

    service.set_sender_impact_exception('   ', 'high-impact')

    assert fake_manager.exceptions == {}


# --- get_messages (requests.get mocked) -------------------------------------

@dataclass
class FakeResponse:
    payload: Dict[str, Any]
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Dict[str, Any]:
        return self.payload


def _service_for_get_messages(monkeypatch: pytest.MonkeyPatch, responses: List[FakeResponse]) -> MessagesService:
    service = _bare_service()
    service.base_url = 'https://graph.microsoft.com/v1.0'
    monkeypatch.setattr(service, '_get_headers', lambda timezone=None: {'Authorization': 'Bearer test'})

    call_log: List[Dict[str, Any]] = []
    responses_iter = iter(responses)

    def fake_get(url, headers=None, params=None):
        call_log.append({'url': url, 'headers': headers, 'params': params})
        return next(responses_iter)

    monkeypatch.setattr(services_module.requests, 'get', fake_get)
    service._test_call_log = call_log  # type: ignore[attr-defined]
    return service


def test_get_messages_returns_value_from_single_page(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service_for_get_messages(monkeypatch, [
        FakeResponse({'value': [{'subject': 'a'}, {'subject': 'b'}]}),
    ])

    messages = service.get_messages(mailbox='inbox', exclude_read=True, max_messages=1000)

    assert messages == [{'subject': 'a'}, {'subject': 'b'}]


def test_get_messages_includes_unread_filter_when_exclude_read_true(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service_for_get_messages(monkeypatch, [FakeResponse({'value': []})])

    service.get_messages(exclude_read=True)

    params = service._test_call_log[0]['params']  # type: ignore[attr-defined]
    assert 'isRead eq false' in params['$filter']


def test_get_messages_omits_filter_when_exclude_read_false(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service_for_get_messages(monkeypatch, [FakeResponse({'value': []})])

    service.get_messages(exclude_read=False)

    params = service._test_call_log[0]['params']  # type: ignore[attr-defined]
    assert '$filter' not in params


def test_get_messages_follows_next_link_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service_for_get_messages(monkeypatch, [
        FakeResponse({'value': [{'subject': 'a'}], '@odata.nextLink': 'https://graph.microsoft.com/v1.0/next'}),
        FakeResponse({'value': [{'subject': 'b'}]}),
    ])

    messages = service.get_messages(max_messages=1000)

    assert [m['subject'] for m in messages] == ['a', 'b']
    assert service._test_call_log[1]['url'] == 'https://graph.microsoft.com/v1.0/next'  # type: ignore[attr-defined]


def test_get_messages_stops_paging_once_max_messages_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service_for_get_messages(monkeypatch, [
        FakeResponse({'value': [{'subject': 'a'}, {'subject': 'b'}], '@odata.nextLink': 'https://x/next'}),
    ])

    messages = service.get_messages(max_messages=1)

    assert messages == [{'subject': 'a'}]
    assert len(service._test_call_log) == 1  # type: ignore[attr-defined] -- never followed nextLink


def test_get_messages_stops_when_response_has_no_value_key(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service_for_get_messages(monkeypatch, [FakeResponse({'unexpected': 'shape'})])

    messages = service.get_messages()

    assert messages == []


# --- mark_messages_as_read / delete_messages --------------------------------

def _service_with_messages_and_provider(monkeypatch: pytest.MonkeyPatch, messages: List[Dict[str, Any]]):
    service = _bare_service()
    monkeypatch.setattr(service, 'get_messages', lambda **kwargs: messages)

    @dataclass
    class FakeProvider:
        mark_as_read_calls: List[List[str]] = field(default_factory=list)
        delete_calls: List[List[str]] = field(default_factory=list)
        result: bool = True

        def mark_as_read(self, user_id, message_ids):
            self.mark_as_read_calls.append(message_ids)
            return self.result

        def delete_messages(self, user_id, message_ids):
            self.delete_calls.append(message_ids)
            return self.result

    provider = FakeProvider()
    service.provider = provider
    service.user_id = 'user1'
    return service, provider


def _msg(sender_name: str, message_id: str) -> Dict[str, Any]:
    return {'from': {'emailAddress': {'name': sender_name}}, 'id': message_id}


def test_mark_messages_as_read_filters_by_sender_and_calls_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [_msg('Alice', 'id1'), _msg('Bob', 'id2'), _msg('Alice', 'id3')]
    service, provider = _service_with_messages_and_provider(monkeypatch, messages)

    result = service.mark_messages_as_read(['Alice'])

    assert result is True
    assert provider.mark_as_read_calls == [['id1', 'id3']]


def test_mark_messages_as_read_returns_true_without_calling_provider_when_no_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [_msg('Bob', 'id2')]
    service, provider = _service_with_messages_and_provider(monkeypatch, messages)

    result = service.mark_messages_as_read(['Alice'])

    assert result is True
    assert provider.mark_as_read_calls == []


def test_mark_messages_as_read_returns_false_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _bare_service()

    def raise_error(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, 'get_messages', raise_error)

    assert service.mark_messages_as_read(['Alice']) is False


def test_delete_messages_filters_by_sender_and_calls_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [_msg('Alice', 'id1'), _msg('Bob', 'id2')]
    service, provider = _service_with_messages_and_provider(monkeypatch, messages)

    result = service.delete_messages(['Bob'])

    assert result is True
    assert provider.delete_calls == [['id2']]


def test_delete_messages_returns_false_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _bare_service()

    def raise_error(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, 'get_messages', raise_error)

    assert service.delete_messages(['Alice']) is False


# --- block_senders -----------------------------------------------------------

def _service_for_block_senders(monkeypatch: pytest.MonkeyPatch):
    service = _bare_service()
    service.base_url = 'https://graph.microsoft.com/v1.0'
    monkeypatch.setattr(service, '_get_headers', lambda timezone=None: {'Authorization': 'Bearer test'})
    monkeypatch.setattr(services_module.time, 'sleep', lambda seconds: None)  # skip real retry delays

    @dataclass
    class FakeTracker:
        recorded: List[str] = field(default_factory=list)

        def record(self, event):
            self.recorded.append(event.sender)

    tracker = FakeTracker()
    service.blocked_sender_tracker = tracker
    return service, tracker


def test_block_senders_returns_true_immediately_for_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    service, tracker = _service_for_block_senders(monkeypatch)
    calls = []
    monkeypatch.setattr(services_module.requests, 'post', lambda *a, **k: calls.append(1))

    assert service.block_senders([]) is True
    assert calls == []


def test_block_senders_records_success_for_each_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    service, tracker = _service_for_block_senders(monkeypatch)
    monkeypatch.setattr(services_module.requests, 'post', lambda *a, **k: FakeResponse({}, status_code=201))

    result = service.block_senders(['Alice', 'Bob'])

    assert result is True
    assert sorted(tracker.recorded) == ['Alice', 'Bob']


def test_block_senders_returns_false_when_a_rule_fails_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    service, tracker = _service_for_block_senders(monkeypatch)

    def fake_post(url, headers=None, json=None):
        sender = json['conditions']['senderContains'][0]
        if sender == 'Bad':
            return FakeResponse({}, status_code=500)
        return FakeResponse({}, status_code=201)

    monkeypatch.setattr(services_module.requests, 'post', fake_post)

    result = service.block_senders(['Good', 'Bad'])

    assert result is False
    # Only the successful sender's block event is recorded.
    assert tracker.recorded == ['Good']


def test_block_senders_returns_false_on_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _bare_service()

    def raise_error(timezone=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, '_get_headers', raise_error)

    assert service.block_senders(['Alice']) is False
