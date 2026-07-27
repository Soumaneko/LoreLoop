from __future__ import annotations

import sys
from collections.abc import Callable

from dotenv import load_dotenv


def load_source_dotenv(
    *,
    frozen: bool | None = None,
    loader: Callable[[], bool] = load_dotenv,
) -> bool:
    """Load developer .env files only when running from source."""
    is_frozen = (
        bool(getattr(sys, "frozen", False))
        if frozen is None
        else bool(frozen)
    )
    if is_frozen:
        return False
    return bool(loader())
