"""Tests for email_server/utils/logger.py.

get_log_directory() honors BRIEFKORB_LOG_DIR ahead of the real OS-default log
location (see its docstring) -- tests rely on that override, already set by
conftest.py's bootstrap, and never touch a real user log directory.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from email_server.utils import logger as logger_module
from email_server.utils.logger import cleanup_old_logs, get_log_directory, setup_logger


def test_get_log_directory_honors_briefkorb_log_dir_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / 'logs'
    monkeypatch.setenv('BRIEFKORB_LOG_DIR', str(log_dir))

    result = get_log_directory()

    assert result == log_dir
    assert log_dir.is_dir()


def test_setup_logger_returns_configured_logger_with_handlers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BRIEFKORB_LOG_DIR', str(tmp_path))

    logger = setup_logger('test_logger_configured')

    assert logger.level == logging.INFO
    assert logger.propagate is False
    assert len(logger.handlers) == 2
    logger.handlers.clear()  # avoid leaking a logging.getLogger()-cached logger into other tests


def test_setup_logger_does_not_duplicate_handlers_on_repeat_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BRIEFKORB_LOG_DIR', str(tmp_path))

    first = setup_logger('test_logger_no_dupe')
    second = setup_logger('test_logger_no_dupe')

    assert first is second
    assert len(first.handlers) == 2
    first.handlers.clear()


def test_cleanup_old_logs_keeps_only_three_most_recent(tmp_path: Path) -> None:
    log_file = 'email_server.log'
    paths = []
    for i in range(5):
        p = tmp_path / f'{log_file}.{i}'
        p.write_text('log entry')
        paths.append(p)

    cleanup_old_logs(tmp_path, log_file)

    remaining = sorted(tmp_path.glob(f'{log_file}*'))
    assert len(remaining) == 3
