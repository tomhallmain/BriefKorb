"""Datetime helpers for consistent ordering across email providers."""

from __future__ import annotations

from datetime import datetime, timezone


def normalize_received_at_utc(dt: datetime) -> datetime:
    """Return *dt* as timezone-aware UTC for safe comparisons and sorting.

    Gmail headers may parse as naive; Microsoft Graph uses aware UTC.
    Naive values are treated as UTC (common for RFC 2822 without zone).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
