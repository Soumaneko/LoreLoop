"""Shared identifier generators."""

from __future__ import annotations

import uuid


def new_hex_id(prefix: str | None = None, length: int = 32) -> str:
    """Generate a random hex identifier, optionally namespaced by a prefix.

    A full ``uuid4`` hex string is 32 characters; callers may shorten it and
    prefix it (e.g. ``director_event_`` + 12 hex chars) without changing the
    underlying randomness.
    """
    value = uuid.uuid4().hex[:length]
    return f"{prefix}_{value}" if prefix else value
