from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Literal

from app.runtime.app_paths import APP_PATHS


DiagnosticSeverity = Literal["warning", "error"]
_WRITE_LOCK = threading.Lock()


def diagnostics_from_state(state):
    """Resolve the diagnostic logger from a graph state mapping.

    Graph states carry ``runtime_services`` whose ``diagnostics`` attribute may
    be absent or None (e.g. minimal test states); fall back to a null logger.
    """
    services = state.get("runtime_services")
    return (
        getattr(services, "diagnostics", None)
        if services is not None
        else None
    ) or NullDiagnosticLogger()


def diagnostic_path_for_trace(log_path: str | os.PathLike[str]) -> Path:
    path = Path(log_path)
    if path.name == "llm_traces.jsonl":
        return path.with_name("diagnostics.jsonl")
    return path.with_name(f"{path.stem}.diagnostics.jsonl")


class DiagnosticLogger:
    """Append-only warning/error log for locally actionable pipeline problems."""

    def __init__(self, log_path: str | os.PathLike[str] | None = None) -> None:
        if log_path is None:
            log_path = APP_PATHS.logs_dir / "diagnostics.jsonl"
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> str:
        return str(self._log_path)

    def warning(self, *, source: str, code: str, message: str, **fields: Any) -> None:
        self.log(
            severity="warning",
            source=source,
            code=code,
            message=message,
            **fields,
        )

    def error(self, *, source: str, code: str, message: str, **fields: Any) -> None:
        self.log(
            severity="error",
            source=source,
            code=code,
            message=message,
            **fields,
        )

    def log(
        self,
        *,
        severity: DiagnosticSeverity,
        source: str,
        code: str,
        message: str,
        trace_id: str | None = None,
        operation_id: str | None = None,
        campaign_id: str | None = None,
        operation_type: str | None = None,
        task: str | None = None,
        attempt: int | None = None,
        turn_index: int | None = None,
        session_type: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": time.time(),
            "severity": severity,
            "source": str(source),
            "code": str(code),
            "message": str(message),
        }
        optional = {
            "trace_id": trace_id,
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "operation_type": operation_type,
            "task": task,
            "attempt": attempt,
            "turn_index": turn_index,
            "session_type": session_type,
        }
        entry.update({key: value for key, value in optional.items() if value is not None})
        if details:
            entry["details"] = details

        with _WRITE_LOCK:
            with self._log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def read(
        self,
        *,
        severity: DiagnosticSeverity | None = None,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self._log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self._log_path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if severity is not None and entry.get("severity") != severity:
                    continue
                if trace_id is not None and entry.get("trace_id") != trace_id:
                    continue
                entries.append(entry)
        return entries


class NullDiagnosticLogger:
    @property
    def log_path(self) -> str:
        return ""

    def warning(self, **_fields: Any) -> None:
        return None

    def error(self, **_fields: Any) -> None:
        return None

    def log(self, **_fields: Any) -> None:
        return None

    def read(
        self,
        *,
        severity: DiagnosticSeverity | None = None,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        del severity, trace_id
        return []
