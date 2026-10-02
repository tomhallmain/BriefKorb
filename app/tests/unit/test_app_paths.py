from __future__ import annotations

from pathlib import Path

from email_server.utils.app_paths import get_app_data_dir, portable_data_path, resolve_data_path


def test_data_dir_override_is_created(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / 'nested' / 'data'
    monkeypatch.setenv('BRIEFKORB_DATA_DIR', str(data_dir))

    assert get_app_data_dir() == data_dir
    assert data_dir.is_dir()


def test_resolve_data_path_joins_relative_and_keeps_absolute(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('BRIEFKORB_DATA_DIR', str(tmp_path / 'data'))
    elsewhere = tmp_path / 'elsewhere' / 'creds.json'

    assert resolve_data_path('tokens') == tmp_path / 'data' / 'tokens'
    assert resolve_data_path(elsewhere) == elsewhere


def test_portable_data_path_relativizes_only_inside_data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('BRIEFKORB_DATA_DIR', str(tmp_path / 'data'))
    elsewhere = tmp_path / 'elsewhere' / 'creds.json'

    assert portable_data_path(tmp_path / 'data' / 'tokens') == 'tokens'
    assert portable_data_path(elsewhere) == str(elsewhere)
    assert portable_data_path('tokens') == 'tokens'
