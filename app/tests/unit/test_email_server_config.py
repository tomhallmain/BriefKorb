from __future__ import annotations

from pathlib import Path

import yaml

from email_server.config import (
    EmailServerConfig,
    ExternalApiConfig,
    ExternalApiToken,
    ProviderConfig,
)


def _minimal_config_dict() -> dict:
    return {
        'microsoft': {'enabled': False},
        'gmail': {'enabled': False},
    }


def test_provider_config_defaults_additional_settings_to_empty_dict() -> None:
    provider = ProviderConfig()

    assert provider.enabled is True
    assert provider.additional_settings == {}


def test_provider_config_preserves_explicit_additional_settings() -> None:
    provider = ProviderConfig(additional_settings={'timeout': 30})

    assert provider.additional_settings == {'timeout': 30}


def test_from_dict_applies_defaults_for_missing_top_level_keys() -> None:
    config = EmailServerConfig.from_dict(_minimal_config_dict())

    assert config.microsoft.enabled is False
    assert config.gmail.enabled is False
    assert config.token_storage_path == 'tokens'
    assert config.log_level == 'INFO'


def test_from_dict_reads_provider_fields() -> None:
    config_dict = {
        'microsoft': {
            'enabled': True,
            'client_id': 'ms-client',
            'client_secret': 'ms-secret',
            'tenant_id': 'ms-tenant',
            'redirect_uri': 'http://localhost/callback',
            'scopes': ['Mail.Read'],
        },
        'gmail': {'enabled': False},
        'token_storage_path': 'custom_tokens',
        'log_level': 'DEBUG',
    }

    config = EmailServerConfig.from_dict(config_dict)

    assert config.microsoft.enabled is True
    assert config.microsoft.client_id == 'ms-client'
    assert config.microsoft.scopes == ['Mail.Read']
    assert config.token_storage_path == 'custom_tokens'
    assert config.log_level == 'DEBUG'


def test_from_dict_defaults_max_messages_to_200() -> None:
    config = EmailServerConfig.from_dict(_minimal_config_dict())

    assert config.max_messages == 200


def test_from_dict_reads_max_messages() -> None:
    config = EmailServerConfig.from_dict({**_minimal_config_dict(), 'max_messages': 500})

    assert config.max_messages == 500


def test_to_dict_round_trips_max_messages() -> None:
    original = EmailServerConfig.from_dict({**_minimal_config_dict(), 'max_messages': 42})

    round_tripped = EmailServerConfig.from_dict(original.to_dict())

    assert round_tripped.max_messages == 42


def test_save_and_from_file_round_trips_max_messages(tmp_path: Path) -> None:
    config_path = tmp_path / 'config.yaml'
    original = EmailServerConfig.from_dict({**_minimal_config_dict(), 'max_messages': 75})

    original.save(str(config_path))
    loaded = EmailServerConfig.from_file(str(config_path))

    assert loaded.max_messages == 75


def test_to_dict_round_trips_through_from_dict() -> None:
    original = EmailServerConfig.from_dict({
        'microsoft': {'enabled': True, 'client_id': 'ms-client'},
        'gmail': {'enabled': True, 'credentials_path': '/tmp/creds.json'},
        'token_storage_path': 'custom_tokens',
        'log_level': 'WARNING',
    })

    round_tripped = EmailServerConfig.from_dict(original.to_dict())

    assert round_tripped.microsoft.enabled == original.microsoft.enabled
    assert round_tripped.microsoft.client_id == original.microsoft.client_id
    assert round_tripped.gmail.credentials_path == original.gmail.credentials_path
    assert round_tripped.token_storage_path == original.token_storage_path
    assert round_tripped.log_level == original.log_level


def test_save_and_from_file_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / 'config.yaml'
    original = EmailServerConfig.from_dict({
        'microsoft': {'enabled': True, 'client_id': 'ms-client', 'redirect_uri': 'http://x/callback'},
        'gmail': {'enabled': False},
        'token_storage_path': 'tokens',
        'log_level': 'INFO',
    })

    original.save(str(config_path))
    loaded = EmailServerConfig.from_file(str(config_path))

    assert loaded.microsoft.client_id == 'ms-client'
    assert loaded.microsoft.redirect_uri == 'http://x/callback'


def test_from_file_resolves_relative_token_storage_path_against_app_data_dir(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / 'data'
    monkeypatch.setenv('BRIEFKORB_DATA_DIR', str(data_dir))
    email_server_dir = tmp_path / 'email_server'
    email_server_dir.mkdir()
    config_path = email_server_dir / 'config.yaml'
    EmailServerConfig.from_dict({
        **_minimal_config_dict(),
        'token_storage_path': 'tokens',
    }).save(str(config_path))

    loaded = EmailServerConfig.from_file(str(config_path))

    assert loaded.token_storage_path == str(data_dir / 'tokens')


def test_save_writes_token_storage_path_inside_app_data_dir_as_relative(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / 'data'
    monkeypatch.setenv('BRIEFKORB_DATA_DIR', str(data_dir))
    config_path = tmp_path / 'config.yaml'
    EmailServerConfig.from_dict({
        **_minimal_config_dict(),
        'token_storage_path': str(data_dir / 'tokens'),
    }).save(str(config_path))

    assert yaml.safe_load(config_path.read_text())['token_storage_path'] == 'tokens'
    assert EmailServerConfig.from_file(str(config_path)).token_storage_path == str(data_dir / 'tokens')


def test_from_file_leaves_absolute_token_storage_path_untouched(tmp_path: Path) -> None:
    email_server_dir = tmp_path / 'email_server'
    email_server_dir.mkdir()
    config_path = email_server_dir / 'config.yaml'
    absolute_tokens_path = str(tmp_path / 'elsewhere' / 'tokens')
    EmailServerConfig.from_dict({
        **_minimal_config_dict(),
        'token_storage_path': absolute_tokens_path,
    }).save(str(config_path))

    loaded = EmailServerConfig.from_file(str(config_path))

    assert loaded.token_storage_path == absolute_tokens_path


def test_validate_raises_when_no_provider_enabled() -> None:
    config = EmailServerConfig(microsoft=ProviderConfig(enabled=False), gmail=ProviderConfig(enabled=False))

    try:
        config.validate()
        assert False, "expected ValueError"
    except ValueError as e:
        assert "At least one provider must be enabled" in str(e)


def test_validate_raises_when_microsoft_enabled_but_missing_fields() -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=True),  # missing client_id/secret/redirect_uri/tenant_id
        gmail=ProviderConfig(enabled=False),
    )

    try:
        config.validate()
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Microsoft provider requires" in str(e)


def test_validate_raises_when_gmail_enabled_but_missing_fields() -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(enabled=False),
        gmail=ProviderConfig(enabled=True),  # missing credentials_path/redirect_uri
    )

    try:
        config.validate()
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Gmail provider requires" in str(e)


def test_validate_raises_when_max_messages_below_one() -> None:
    config = EmailServerConfig(
        microsoft=ProviderConfig(
            enabled=True, client_id='id', client_secret='secret',
            redirect_uri='http://x/callback', tenant_id='tenant',
        ),
        gmail=ProviderConfig(enabled=False),
        max_messages=0,
    )

    try:
        config.validate()
        assert False, "expected ValueError"
    except ValueError as e:
        assert "max_messages must be at least 1" in str(e)


def test_validate_creates_token_storage_directory(tmp_path: Path) -> None:
    token_dir = tmp_path / 'new_tokens_dir'
    config = EmailServerConfig(
        microsoft=ProviderConfig(
            enabled=True,
            client_id='id', client_secret='secret',
            redirect_uri='http://x/callback', tenant_id='tenant',
        ),
        gmail=ProviderConfig(enabled=False),
        token_storage_path=str(token_dir),
    )

    assert config.validate() is True
    assert token_dir.is_dir()


def test_masked_token_hides_the_middle_of_a_secret() -> None:
    token = ExternalApiToken(token='abcdefghijklmnop', label='tagesform')

    assert token.masked_token() == 'abcdef...mnop'
    assert token.display_label() == 'tagesform'


def test_masked_token_for_short_secret_is_fully_redacted() -> None:
    assert ExternalApiToken(token='short', label='').masked_token() == '****'
    assert ExternalApiToken(token='short').display_label() == '(unlabeled)'


def test_generate_token_stores_stripped_label_and_enables_api() -> None:
    external_api = ExternalApiConfig(enabled=False)

    plaintext = external_api.generate_token(label='  tagesform  ')

    assert external_api.enabled is True
    assert len(external_api.tokens) == 1
    assert external_api.tokens[0].label == 'tagesform'
    assert external_api.tokens[0].token == plaintext
    assert len(plaintext) >= 32


def test_generate_token_can_leave_api_disabled() -> None:
    external_api = ExternalApiConfig(enabled=False)

    external_api.generate_token(label='offline', enable=False)

    assert external_api.enabled is False
    assert len(external_api.tokens) == 1


def test_generate_token_retries_on_collision(monkeypatch) -> None:
    external_api = ExternalApiConfig(
        tokens=[ExternalApiToken(token='aaaaaaaaaaaaaaaa')],
    )
    calls = {'n': 0}

    def fake_token_urlsafe(nbytes: int) -> str:
        calls['n'] += 1
        return 'aaaaaaaaaaaaaaaa' if calls['n'] == 1 else 'bbbbbbbbbbbbbbbb'

    monkeypatch.setattr('email_server.config.secrets.token_urlsafe', fake_token_urlsafe)

    plaintext = external_api.generate_token(enable=False)

    assert plaintext == 'bbbbbbbbbbbbbbbb'
    assert calls['n'] == 2
    assert [t.token for t in external_api.tokens] == ['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb']


def test_revoke_token_at_removes_the_indexed_entry() -> None:
    first = ExternalApiToken(token='token-one', label='one')
    second = ExternalApiToken(token='token-two', label='two')
    external_api = ExternalApiConfig(tokens=[first, second])

    removed = external_api.revoke_token_at(0)

    assert removed == first
    assert external_api.tokens == [second]
    assert external_api.revoke_token_at(5) is None
    assert external_api.tokens == [second]
