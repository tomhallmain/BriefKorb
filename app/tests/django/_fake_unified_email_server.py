"""Shared UnifiedEmailServer test double for django_app/messages/ view
tests (test_inbox_views.py, test_messages_views.py).

Multi-provider correctness itself (aggregation, dispatch, auth resolution)
is UnifiedEmailServer's own tested concern -- see test_unified_email_server.py.
These view tests only need a controllable stand-in exposing exactly the
methods the views call, patched in wholesale via `_patch_server`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from django_app.messages import views as messages_views_module


@dataclass
class FakeAuthenticatedProvider:
    provider_name: str
    user_id: str


class FakeUnifiedEmailServer:
    def __init__(
        self,
        authenticated_providers: Optional[List[FakeAuthenticatedProvider]] = None,
        messages: Optional[List[Any]] = None,
        digest: Optional[List[Dict[str, Any]]] = None,
        entity_count: int = 0,
        message_by_id: Optional[Dict[str, Any]] = None,
        raise_on_fetch: Optional[Exception] = None,
        raise_on_digest: Optional[Exception] = None,
        mark_as_read_result: bool = True,
        delete_result: bool = True,
        block_result: bool = True,
    ) -> None:
        self._authenticated_providers = authenticated_providers if authenticated_providers is not None else []
        self._messages = messages if messages is not None else []
        self._digest = digest if digest is not None else []
        self._entity_count = entity_count
        self._message_by_id = message_by_id or {}
        self._raise_on_fetch = raise_on_fetch
        self._raise_on_digest = raise_on_digest
        self._mark_as_read_result = mark_as_read_result
        self._delete_result = delete_result
        self._block_result = block_result

        self.get_user_messages_calls: List[Dict[str, Any]] = []
        self.get_message_digest_calls: List[Dict[str, Any]] = []
        self.extract_entities_calls: List[Any] = []
        self.get_message_calls: List[Dict[str, Any]] = []
        self.mark_messages_as_read_calls: List[Dict[str, Any]] = []
        self.delete_user_messages_calls: List[Dict[str, Any]] = []
        self.block_senders_calls: List[Dict[str, Any]] = []

    def get_authenticated_providers(self, provider_name: Optional[str] = None) -> List[FakeAuthenticatedProvider]:
        if provider_name is None:
            return self._authenticated_providers
        return [p for p in self._authenticated_providers if p.provider_name == provider_name]

    def get_user_messages(self, folder: str = 'inbox', unread_only: bool = False, max_messages: int = 100) -> List[Any]:
        self.get_user_messages_calls.append({'folder': folder, 'unread_only': unread_only, 'max_messages': max_messages})
        if self._raise_on_fetch:
            raise self._raise_on_fetch
        return self._messages

    def get_message_digest(self, messages: Optional[List[Any]] = None, **kwargs: Any) -> List[Dict[str, Any]]:
        self.get_message_digest_calls.append({'messages': messages, **kwargs})
        if self._raise_on_digest:
            raise self._raise_on_digest
        return self._digest

    def extract_entities(self, messages: Any) -> int:
        self.extract_entities_calls.append(messages)
        return self._entity_count

    def get_message(self, user_id: str, provider_name: str, message_id: str) -> Any:
        self.get_message_calls.append({'user_id': user_id, 'provider_name': provider_name, 'message_id': message_id})
        return self._message_by_id.get(message_id)

    def mark_messages_as_read(self, user_id: str, provider_name: str, message_ids: List[str]) -> bool:
        self.mark_messages_as_read_calls.append({'user_id': user_id, 'provider_name': provider_name, 'message_ids': message_ids})
        return self._mark_as_read_result

    def delete_user_messages(self, user_id: str, provider_name: str, message_ids: List[str]) -> bool:
        self.delete_user_messages_calls.append({'user_id': user_id, 'provider_name': provider_name, 'message_ids': message_ids})
        return self._delete_result

    def block_senders(self, user_id: str, provider_name: str, sender_names: List[str]) -> bool:
        self.block_senders_calls.append({'user_id': user_id, 'provider_name': provider_name, 'sender_names': sender_names})
        return self._block_result


def patch_server(monkeypatch: pytest.MonkeyPatch, fake_server: FakeUnifiedEmailServer) -> None:
    monkeypatch.setattr(messages_views_module, 'UnifiedEmailServer', lambda config: fake_server)
