import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.core.diagnostics import (
    DiagnosticLogger,
    NullDiagnosticLogger,
    diagnostic_path_for_trace,
)
from app.runtime.app_paths import APP_PATHS


TRACE_LOG_MAX_BYTES = 50 * 1024 * 1024
_TRACE_WRITE_LOCK = threading.Lock()


class TraceLogger:
    def __init__(
        self,
        log_path: str | None = None,
        *,
        diagnostics: DiagnosticLogger | NullDiagnosticLogger | None = None,
        max_bytes: int = TRACE_LOG_MAX_BYTES,
    ):
        if log_path is None:
            log_path = str(APP_PATHS.logs_dir / "llm_traces.jsonl")
        if max_bytes < 1024:
            raise ValueError("trace max_bytes must be at least 1024")
        self._log_path = str(log_path)
        self._max_bytes = int(max_bytes)
        self._diagnostics = diagnostics or DiagnosticLogger(
            diagnostic_path_for_trace(log_path)
        )
        self._lock = _TRACE_WRITE_LOCK
        os.makedirs(os.path.dirname(self._log_path), exist_ok=True)

    @property
    def log_path(self) -> str:
        return self._log_path

    @property
    def diagnostics(self) -> DiagnosticLogger | NullDiagnosticLogger:
        return self._diagnostics

    def log(
        self,
        trace_id: str,
        task: str,
        model: str,
        prompt_version: str,
        input_data: dict[str, Any],
        raw_output: str,
        parsed_output: Any,
        schema_valid: bool,
        latency_ms: float,
        error: str | None,
        *,
        attempt: int | None = None,
        token_usage: dict | None = None,
        turn_index: int | None = None,
        session_type: str | None = None,
        prompt_fingerprint: str | None = None,
    ) -> None:
        entry = {
            "trace_id": trace_id,
            "task": task,
            "model": model,
            "prompt_version": prompt_version,
            "input_data": input_data,
            "raw_output": raw_output,
            "parsed_output": parsed_output,
            "schema_valid": schema_valid,
            "latency_ms": round(latency_ms, 3),
            "error": error,
            "timestamp": time.time(),
        }
        if attempt is not None:
            entry["attempt"] = attempt
        if token_usage is not None:
            entry["token_usage"] = token_usage
        if turn_index is not None:
            entry["turn_index"] = turn_index
        if session_type is not None:
            entry["session_type"] = session_type
        if prompt_fingerprint is not None:
            entry["prompt_fingerprint"] = prompt_fingerprint

        payload = _bounded_entry_payload(entry, self._max_bytes)
        with self._lock:
            _append_bounded(
                Path(self._log_path),
                payload,
                max_bytes=self._max_bytes,
            )

    def read_traces(self, trace_id: str | None = None) -> list[dict[str, Any]]:
        if not os.path.exists(self._log_path):
            return []

        traces = []
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if trace_id is None or entry.get("trace_id") == trace_id:
                    traces.append(entry)
        return traces


def _encode_entry(entry: dict[str, Any]) -> bytes:
    return (
        json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    ).encode("utf-8")


def _bounded_entry_payload(entry: dict[str, Any], max_bytes: int) -> bytes:
    payload = _encode_entry(entry)
    if len(payload) <= max_bytes:
        return payload
    marker = {
        "trace_id": str(entry.get("trace_id", ""))[:64],
        "task": str(entry.get("task", ""))[:64],
        "model": str(entry.get("model", ""))[:64],
        "prompt_version": str(entry.get("prompt_version", ""))[:64],
        "schema_valid": bool(entry.get("schema_valid", False)),
        "latency_ms": entry.get("latency_ms"),
        "timestamp": entry.get("timestamp"),
        "trace_entry_truncated": True,
        "original_entry_bytes": len(payload),
    }
    return _encode_entry(marker)


def _append_bounded(path: Path, payload: bytes, *, max_bytes: int) -> None:
    try:
        current_size = path.stat().st_size
    except FileNotFoundError:
        current_size = 0
    if current_size + len(payload) <= max_bytes:
        with path.open("ab") as file:
            file.write(payload)
        return

    retained_limit = max_bytes - len(payload)
    retained = b""
    if retained_limit > 0 and current_size > 0:
        with path.open("rb") as source:
            start = max(0, current_size - retained_limit)
            source.seek(start)
            retained = source.read()
        if start > 0:
            boundary = retained.find(b"\n")
            retained = retained[boundary + 1 :] if boundary >= 0 else b""
        if retained and not retained.endswith(b"\n"):
            boundary = retained.rfind(b"\n")
            retained = retained[: boundary + 1] if boundary >= 0 else b""
        while len(retained) > retained_limit:
            boundary = retained.find(b"\n")
            retained = retained[boundary + 1 :] if boundary >= 0 else b""

    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with temporary.open("wb") as file:
            file.write(retained)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class NullTraceLogger:
    def __init__(self) -> None:
        self._diagnostics = NullDiagnosticLogger()

    @property
    def log_path(self) -> str:
        return ""

    @property
    def diagnostics(self) -> NullDiagnosticLogger:
        return self._diagnostics

    def log(
        self,
        trace_id: str,
        task: str,
        model: str,
        prompt_version: str,
        input_data: dict[str, Any],
        raw_output: str,
        parsed_output: Any,
        schema_valid: bool,
        latency_ms: float,
        error: str | None,
        *,
        attempt: int | None = None,
        token_usage: dict | None = None,
        turn_index: int | None = None,
        session_type: str | None = None,
        prompt_fingerprint: str | None = None,
    ) -> None:
        return None

    def read_traces(self, trace_id: str | None = None) -> list[dict[str, Any]]:
        return []
