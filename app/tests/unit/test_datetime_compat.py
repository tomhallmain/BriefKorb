from __future__ import annotations

from datetime import datetime, timedelta, timezone

from email_server.utils.datetime_compat import normalize_received_at_utc


def test_naive_datetime_is_treated_as_utc() -> None:
    naive = datetime(2024, 3, 1, 12, 0, 0)

    result = normalize_received_at_utc(naive)

    assert result.tzinfo == timezone.utc
    assert result == datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_aware_utc_datetime_is_returned_unchanged_in_value() -> None:
    aware = datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    result = normalize_received_at_utc(aware)

    assert result == aware
    assert result.tzinfo == timezone.utc


def test_non_utc_aware_datetime_is_converted_to_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    aware = datetime(2024, 3, 1, 7, 0, 0, tzinfo=eastern)

    result = normalize_received_at_utc(aware)

    assert result.tzinfo == timezone.utc
    assert result == datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
