from __future__ import annotations

import json
import os
import threading
import time
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import Any, Literal

from app.runtime.app_paths import APP_PATHS
from app.core.crash_logging import _redact_known_secrets


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

    def __init__(self, log_path: str | os.PathLike[str] | None = None, *, secrets: tuple[str, ...] = ()) -> None:
        if log_path is None:
            log_path = APP_PATHS.logs_dir / "diagnostics.jsonl"
        self._log_path = Path(log_path)
        self.secrets = secrets
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

        def sanitize(value):
            if isinstance(value, str):
                return _redact_known_secrets(value, secrets=self.secrets)
            if isinstance(value, dict):
                return {key: sanitize(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [sanitize(item) for item in value]
            return value

        with _WRITE_LOCK:
            with self._log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(sanitize(entry), ensure_ascii=False, default=str) + "\n")

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


def record_local_failure(exc: BaseException, *, source: str, logger=None, **fields: Any) -> str:
    """Record a failure without requiring a console or replacing the original error."""
    diagnostic_id = fields.get("operation_id") or uuid.uuid4().hex
    details = dict(fields.pop("details", None) or {})
    details.setdefault("error_type", type(exc).__name__)
    details.update({"diagnostic_id": diagnostic_id,
                    "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))})
    secrets = getattr(logger, "secrets", ())
    for fallback in (False, True):
        try:
            target = logger if not fallback and isinstance(logger, DiagnosticLogger) else DiagnosticLogger(
                Path(tempfile.gettempdir()) / "LoreLoop-diagnostics.jsonl" if fallback else None,
                secrets=secrets,
            )
            target.error(source=source, code="operation_failed", message=str(exc), details=details, **fields)
            return f"诊断编号：{diagnostic_id}；本地记录：{target.log_path}"
        except Exception:
            continue
    return f"诊断记录未能保存（编号：{diagnostic_id}）。请复制当前错误提示并检查磁盘及目录权限。"
