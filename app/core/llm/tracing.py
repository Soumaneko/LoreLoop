import json
import os
import threading
import time
from typing import Any

from app.core.diagnostics import (
    DiagnosticLogger,
    NullDiagnosticLogger,
    diagnostic_path_for_trace,
)


class TraceLogger:
    def __init__(
        self,
        log_path: str | None = None,
        *,
        diagnostics: DiagnosticLogger | NullDiagnosticLogger | None = None,
    ):
        if log_path is None:
            log_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "logs", "llm_traces.jsonl"
            )
        self._log_path = log_path
        self._diagnostics = diagnostics or DiagnosticLogger(
            diagnostic_path_for_trace(log_path)
        )
        self._lock = threading.Lock()
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

        with self._lock:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

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
