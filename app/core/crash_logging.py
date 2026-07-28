from __future__ import annotations

import os
import platform
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from app.version import APP_VERSION


def write_desktop_crash(
    error: BaseException,
    *,
    phase: str,
    logs_dir: str | Path | None = None,
) -> Path:
    """Append a startup-level desktop failure without leaking known secrets."""
    preferred = Path(logs_dir) if logs_dir is not None else None
    destination = (
        preferred / "desktop-crash.log"
        if preferred is not None
        else Path(tempfile.gettempdir()) / "LoreLoop-desktop-crash.log"
    )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _append_crash(destination, error, phase=phase)
        return destination
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "LoreLoop-desktop-crash.log"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        _append_crash(fallback, error, phase=phase)
        return fallback


def _append_crash(path: Path, error: BaseException, *, phase: str) -> None:
    rendered = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    rendered = _redact_known_secrets(rendered)
    header = (
        f"[{datetime.now(timezone.utc).isoformat()}] phase={phase}\n"
        f"app_version={APP_VERSION}\n"
        f"platform={platform.platform()}\n"
        f"python={sys.version.split()[0]}\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(header)
        handle.write(rendered)
        if not rendered.endswith("\n"):
            handle.write("\n")
        handle.write("\n")


def _redact_known_secrets(text: str) -> str:
    redacted = text
    for name, value in os.environ.items():
        upper = name.upper()
        if (
            value
            and len(value) >= 6
            and (
                upper.endswith("_KEY")
                or upper.endswith("_TOKEN")
                or upper.endswith("_SECRET")
                or upper == "OPENAI_API_KEY"
            )
        ):
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted
