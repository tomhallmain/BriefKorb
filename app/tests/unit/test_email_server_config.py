from __future__ import annotations

from pathlib import Path

from email_server.config import EmailServerConfig, ProviderConfig


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


def test_from_file_resolves_relative_token_storage_path_against_config_parent(tmp_path: Path) -> None:
    email_server_dir = tmp_path / 'email_server'
    email_server_dir.mkdir()
    config_path = email_server_dir / 'config.yaml'
    EmailServerConfig.from_dict({
        **_minimal_config_dict(),
        'token_storage_path': 'tokens',
    }).save(str(config_path))

    loaded = EmailServerConfig.from_file(str(config_path))

    # app/email_server/config.yaml -> token_storage_path resolves relative to app/ (the
    # config file's grandparent), matching the real repo layout (app/email_server/ and
    # app/tokens/ as siblings).
    assert loaded.token_storage_path == str(tmp_path / 'tokens')


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
