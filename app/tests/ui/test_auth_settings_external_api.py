"""Tests for inbound BriefKorb API token management in AuthSettingsDialog.

These are bearer tokens other applications send to BriefKorb (e.g. GET
/api/messages), not OAuth tokens BriefKorb uses to call Microsoft/Gmail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from email_server.config import (  # noqa: E402
    EmailServerConfig,
    ExternalApiConfig,
    ExternalApiToken,
    ProviderConfig,
)
from ui.auth_settings_dialog import AuthSettingsDialog  # noqa: E402


def _valid_providers() -> dict:
    return {
        'microsoft': ProviderConfig(
            enabled=True,
            client_id='id',
            client_secret='secret',
            tenant_id='tenant',
            redirect_uri='http://localhost/ms',
        ),
        'gmail': ProviderConfig(enabled=False),
    }


def _make_dialog(qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **config_kwargs) -> AuthSettingsDialog:
    monkeypatch.setattr(AuthSettingsDialog, '_update_auth_status', lambda self: None)
    monkeypatch.setattr(QMessageBox, 'information', staticmethod(lambda *args, **kwargs: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *args, **kwargs: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, 'critical', staticmethod(lambda *args, **kwargs: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, 'question', staticmethod(lambda *args, **kwargs: QMessageBox.Yes))

    config_path = tmp_path / 'config.yaml'
    config = EmailServerConfig(
        **_valid_providers(),
        token_storage_path=str(tmp_path / 'tokens'),
        **config_kwargs,
    )
    config.save(str(config_path))
    dialog = AuthSettingsDialog(config, str(config_path))
    qtbot.addWidget(dialog)
    return dialog


def test_external_api_tab_lists_masked_tokens_not_full_secret(qtbot, tmp_path, monkeypatch):
    dialog = _make_dialog(
        qtbot,
        tmp_path,
        monkeypatch,
        external_api=ExternalApiConfig(
            enabled=True,
            tokens=[ExternalApiToken(token='abcdefghijklmnop', label='tagesform')],
        ),
    )

    assert dialog.tabs.tabText(dialog.tabs.count() - 1) == 'External API'
    assert dialog.external_api_enabled.isChecked() is True
    assert dialog.api_token_list.count() == 1
    listed = dialog.api_token_list.item(0).text()
    assert 'tagesform' in listed
    assert 'abcdef...mnop' in listed
    assert 'abcdefghijklmnop' not in listed


def test_generate_api_token_persists_immediately_and_enables_api(qtbot, tmp_path, monkeypatch):
    shown = []

    def capture_information(*args, **kwargs):
        shown.append(args)
        return QMessageBox.Ok

    dialog = _make_dialog(qtbot, tmp_path, monkeypatch)
    monkeypatch.setattr(QMessageBox, 'information', staticmethod(capture_information))

    dialog.api_token_label.setText('tagesform')
    dialog._generate_api_token()

    saved = EmailServerConfig.from_file(dialog.config_path)
    assert saved.external_api.enabled is True
    assert len(saved.external_api.tokens) == 1
    assert saved.external_api.tokens[0].label == 'tagesform'
    plaintext = saved.external_api.tokens[0].token

    assert dialog.external_api_enabled.isChecked() is True
    assert dialog.api_token_list.count() == 1
    assert plaintext not in dialog.api_token_list.item(0).text()
    assert any(plaintext in str(arg) for args in shown for arg in args)
    assert dialog.api_token_label.text() == ''


def test_revoke_selected_api_token_persists_immediately(qtbot, tmp_path, monkeypatch):
    dialog = _make_dialog(
        qtbot,
        tmp_path,
        monkeypatch,
        external_api=ExternalApiConfig(
            enabled=True,
            tokens=[
                ExternalApiToken(token='token-one', label='one'),
                ExternalApiToken(token='token-two', label='two'),
            ],
        ),
    )

    dialog.api_token_list.setCurrentRow(0)
    dialog._revoke_selected_api_token()

    saved = EmailServerConfig.from_file(dialog.config_path)
    assert [t.label for t in saved.external_api.tokens] == ['two']
    assert dialog.api_token_list.count() == 1
    assert 'two' in dialog.api_token_list.item(0).text()


def test_save_persists_external_api_enabled_without_dropping_tokens(qtbot, tmp_path, monkeypatch):
    dialog = _make_dialog(
        qtbot,
        tmp_path,
        monkeypatch,
        external_api=ExternalApiConfig(
            enabled=False,
            tokens=[ExternalApiToken(token='keep-this-token', label='existing')],
        ),
    )

    dialog.external_api_enabled.setChecked(True)
    dialog._save_config()

    saved = EmailServerConfig.from_file(dialog.config_path)
    assert saved.external_api.enabled is True
    assert [t.token for t in saved.external_api.tokens] == ['keep-this-token']
