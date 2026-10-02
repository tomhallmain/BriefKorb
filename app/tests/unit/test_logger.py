"""Tests for email_server/utils/logger.py.

get_log_directory() honors BRIEFKORB_LOG_DIR ahead of the real OS-default log
location (see its docstring) -- tests rely on that override, already set by
conftest.py's bootstrap, and never touch a real user log directory.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from email_server.utils import logger as logger_module
from email_server.utils.logger import DailyFileHandler, get_log_directory, setup_logger


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


def test_loggers_share_one_file_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BRIEFKORB_LOG_DIR', str(tmp_path))

    first = setup_logger('test_logger_shared_a')
    second = setup_logger('test_logger_shared_b')

    file_handlers = {h for h in first.handlers + second.handlers if isinstance(h, logging.FileHandler)}
    assert len(file_handlers) == 1
    for handler in file_handlers:
        handler.close()
    first.handlers.clear()
    second.handlers.clear()


def _record_at(when: datetime) -> logging.LogRecord:
    record = logging.LogRecord('test', logging.INFO, __file__, 0, 'message', None, None)
    record.created = when.timestamp()
    return record


def test_daily_file_handler_writes_to_dated_file_and_switches_on_new_day(tmp_path: Path) -> None:
    handler = DailyFileHandler(tmp_path, 'email_server.log')
    today = date.today()
    tomorrow = today + timedelta(days=1)
    try:
        handler.emit(_record_at(datetime.now()))
        handler.emit(_record_at(datetime.combine(tomorrow, datetime.min.time())))
    finally:
        handler.close()

    assert (tmp_path / f'email_server.{today.isoformat()}.log').read_text(encoding='utf-8').strip() == 'message'
    assert (tmp_path / f'email_server.{tomorrow.isoformat()}.log').read_text(encoding='utf-8').strip() == 'message'


def test_daily_file_handler_prunes_only_dated_files_past_retention(tmp_path: Path) -> None:
    today = date.today()
    old = tmp_path / f'email_server.{(today - timedelta(days=4)).isoformat()}.log'
    kept = tmp_path / f'email_server.{(today - timedelta(days=3)).isoformat()}.log'
    unrelated = tmp_path / 'email_server.log.2020-01-01'
    for p in (old, kept, unrelated):
        p.write_text('log entry')

    DailyFileHandler(tmp_path, 'email_server.log', retention_days=3).close()

    assert not old.exists()
    assert kept.exists()
    assert unrelated.exists()
