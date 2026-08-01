from __future__ import annotations

import json
from pathlib import Path

import pytest

from email_server.auth import GmailToken, MicrosoftToken, TokenManager


# --- MicrosoftToken / GmailToken.verify_for_provider_type -----------------

def test_microsoft_token_recognizes_access_token_only() -> None:
    assert MicrosoftToken.verify_for_provider_type({'access_token': 'abc'}) is True


def test_microsoft_token_rejects_google_structure_even_with_access_token() -> None:
    token = {'access_token': 'abc', 'token': 'x', 'token_uri': 'https://oauth2.googleapis.com/token'}
    assert MicrosoftToken.verify_for_provider_type(token) is False


def test_microsoft_token_rejects_non_dict_input() -> None:
    assert MicrosoftToken.verify_for_provider_type("not a dict") is False  # type: ignore[arg-type]


def test_microsoft_token_rejects_empty_dict() -> None:
    assert MicrosoftToken.verify_for_provider_type({}) is False


def test_gmail_token_recognizes_token_and_token_uri() -> None:
    token = {'token': 'abc', 'token_uri': 'https://oauth2.googleapis.com/token'}
    assert GmailToken.verify_for_provider_type(token) is True


def test_gmail_token_rejects_microsoft_structure() -> None:
    token = {
        'token': 'abc',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'access_token': 'x',
        'msal_cache': 'y',
    }
    assert GmailToken.verify_for_provider_type(token) is False


def test_gmail_token_tolerates_stray_access_token_without_msal_cache() -> None:
    # Ambiguous case: has Gmail structure AND an access_token key, but no msal_cache --
    # not enough to count as "Microsoft structure", so this still reads as a Gmail token.
    token = {'token': 'abc', 'token_uri': 'https://oauth2.googleapis.com/token', 'access_token': 'x'}
    assert GmailToken.verify_for_provider_type(token) is True


def test_gmail_token_rejects_non_dict_input() -> None:
    assert GmailToken.verify_for_provider_type(None) is False  # type: ignore[arg-type]


def test_gmail_token_rejects_missing_token_uri() -> None:
    assert GmailToken.verify_for_provider_type({'token': 'abc'}) is False


# --- TokenManager.get_valid_token (pure, in-memory) ------------------------

def test_get_valid_token_returns_none_for_falsy_input() -> None:
    manager = TokenManager.__new__(TokenManager)  # doesn't touch disk; method under test is pure
    assert manager.get_valid_token({}) is None
    assert manager.get_valid_token(None) is None  # type: ignore[arg-type]


def test_get_valid_token_prefers_access_token_over_token() -> None:
    manager = TokenManager.__new__(TokenManager)
    result = manager.get_valid_token({'access_token': 'ms-token', 'token': 'gmail-token'})
    assert result == 'ms-token'


def test_get_valid_token_falls_back_to_token_key() -> None:
    manager = TokenManager.__new__(TokenManager)
    result = manager.get_valid_token({'token': 'gmail-token'})
    assert result == 'gmail-token'


def test_get_valid_token_does_not_check_expiration() -> None:
    # Documents current (arguably misleading) behavior: despite the docstring
    # mentioning expiration, no expires_at/acquired_at check happens here at all.
    manager = TokenManager.__new__(TokenManager)
    stale_token = {'access_token': 'still-returned', 'expires_at': 0}
    assert manager.get_valid_token(stale_token) == 'still-returned'


# --- TokenManager disk persistence (tmp_path, no live OAuth needed) -------

def test_init_creates_storage_directory(tmp_path: Path) -> None:
    storage_path = tmp_path / 'tokens'
    assert not storage_path.exists()

    TokenManager(storage_path=str(storage_path))

    assert storage_path.is_dir()


def test_init_with_no_existing_files_starts_empty(tmp_path: Path) -> None:
    manager = TokenManager(storage_path=str(tmp_path))
    assert manager.get_all_user_ids() == []


def test_store_and_get_token_round_trips_in_memory(tmp_path: Path) -> None:
    manager = TokenManager(storage_path=str(tmp_path))

    manager.store_token('user1', {'access_token': 'abc'})

    assert manager.get_token('user1') == {'access_token': 'abc'}
    assert manager.has_token('user1') is True


def test_store_token_persists_to_disk_as_json(tmp_path: Path) -> None:
    manager = TokenManager(storage_path=str(tmp_path))

    manager.store_token('user1', {'access_token': 'abc'})

    tokens_file = tmp_path / 'tokens.json'
    assert tokens_file.exists()
    assert json.loads(tokens_file.read_text()) == {'user1': {'access_token': 'abc'}}


def test_store_user_info_persists_to_disk_as_json(tmp_path: Path) -> None:
    manager = TokenManager(storage_path=str(tmp_path))

    manager.store_user_info('user1', {'email': 'user1@example.com'})

    user_info_file = tmp_path / 'user_info.json'
    assert json.loads(user_info_file.read_text()) == {'user1': {'email': 'user1@example.com'}}
    assert manager.get_user_info('user1') == {'email': 'user1@example.com'}


def test_a_fresh_token_manager_reloads_persisted_state(tmp_path: Path) -> None:
    first = TokenManager(storage_path=str(tmp_path))
    first.store_token('user1', {'access_token': 'abc'})
    first.store_user_info('user1', {'email': 'user1@example.com'})

    second = TokenManager(storage_path=str(tmp_path))

    assert second.get_token('user1') == {'access_token': 'abc'}
    assert second.get_user_info('user1') == {'email': 'user1@example.com'}
    assert second.get_all_user_ids() == ['user1']


def test_load_from_disk_recovers_to_empty_state_on_malformed_json(tmp_path: Path) -> None:
    (tmp_path / 'tokens.json').write_text("{not valid json")

    manager = TokenManager(storage_path=str(tmp_path))

    assert manager.get_all_user_ids() == []


def test_load_from_disk_filters_out_non_dict_values(tmp_path: Path) -> None:
    (tmp_path / 'tokens.json').write_text(json.dumps({'user1': {'access_token': 'abc'}, 'user2': 'not a dict'}))

    manager = TokenManager(storage_path=str(tmp_path))

    assert manager.get_all_user_ids() == ['user1']


def test_clear_user_data_removes_token_and_user_info(tmp_path: Path) -> None:
    manager = TokenManager(storage_path=str(tmp_path))
    manager.store_token('user1', {'access_token': 'abc'})
    manager.store_user_info('user1', {'email': 'user1@example.com'})

    manager.clear_user_data('user1')

    assert manager.get_token('user1') is None
    assert manager.get_user_info('user1') is None
    assert manager.has_token('user1') is False


def test_clear_user_data_is_a_no_op_for_unknown_user(tmp_path: Path) -> None:
    manager = TokenManager(storage_path=str(tmp_path))
    manager.clear_user_data('never-seen')  # should not raise
    assert manager.get_all_user_ids() == []


def test_get_token_returns_none_and_does_not_raise_for_non_string_user_id(tmp_path: Path) -> None:
    manager = TokenManager(storage_path=str(tmp_path))
    assert manager.get_token(123) is None  # type: ignore[arg-type]


def test_has_token_returns_false_when_stored_value_is_none(tmp_path: Path) -> None:
    manager = TokenManager(storage_path=str(tmp_path))
    manager._tokens['user1'] = None  # simulate a corrupted/explicitly-cleared entry
    assert manager.has_token('user1') is False


def test_get_all_user_ids_filters_non_string_keys(tmp_path: Path) -> None:
    manager = TokenManager(storage_path=str(tmp_path))
    manager._tokens = {'user1': {'access_token': 'a'}, 2: {'access_token': 'b'}}
    assert manager.get_all_user_ids() == ['user1']
