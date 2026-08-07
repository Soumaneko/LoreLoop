"""Shared timestamp helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_datetime_now() -> datetime:
    return datetime.now(timezone.utc)
