from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import threading
from typing import Any


TokenUsageSink = Callable[[dict[str, Any]], None]
_PLAYER_SESSION_TYPES = frozenset({"generation", "confirmation", "init"})


@dataclass
class _TokenUsageBinding:
    sink: TokenUsageSink
    call_indexes: dict[str, int] = field(default_factory=dict)
    task_counts: dict[str, int] = field(default_factory=dict)

    def call_index(self, call_id: str, task: str) -> int:
        return _next_call_index(
            self.call_indexes,
            self.task_counts,
            call_id=call_id,
            task=task,
        )


_CURRENT_BINDING: ContextVar[_TokenUsageBinding | None] = ContextVar(
    "operation_token_usage_binding",
    default=None,
)
_TRACE_SINKS: dict[str, TokenUsageSink] = {}
_TRACE_TOTALS: dict[str, dict[str, Any]] = {}
_TRACE_SINKS_LOCK = threading.Lock()


def bind_token_usage_sink(sink: TokenUsageSink) -> Token:
    return _CURRENT_BINDING.set(_TokenUsageBinding(sink=sink))


def reset_token_usage_sink(context_token: Token) -> None:
    _CURRENT_BINDING.reset(context_token)


def bind_token_usage_trace(trace_id: str) -> bool:
    """Make the current operation sink visible to worker threads for a trace."""
    clean_trace_id = str(trace_id).strip()
    binding = _CURRENT_BINDING.get()
    if not clean_trace_id or binding is None:
        return False
    with _TRACE_SINKS_LOCK:
        _TRACE_SINKS[clean_trace_id] = binding.sink
        _TRACE_TOTALS.setdefault(
            clean_trace_id,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "has_input": False,
                "has_output": False,
                "calls": [],
                "call_indexes": {},
                "task_counts": {},
            },
        )
    return True


def reset_token_usage_traces(trace_ids: set[str]) -> None:
    with _TRACE_SINKS_LOCK:
        for trace_id in trace_ids:
            _TRACE_SINKS.pop(trace_id, None)
            _TRACE_TOTALS.pop(trace_id, None)


def get_trace_token_usage(trace_id: str) -> dict[str, Any] | None:
    clean_trace_id = str(trace_id).strip()
    if not clean_trace_id:
        return None
    with _TRACE_SINKS_LOCK:
        totals = _TRACE_TOTALS.get(clean_trace_id)
        if totals is None:
            return None
        has_input = totals["has_input"] is True
        has_output = totals["has_output"] is True
        if not has_input and not has_output:
            return None
        input_tokens = int(totals["input_tokens"]) if has_input else None
        output_tokens = int(totals["output_tokens"]) if has_output else None
        calls = [dict(call) for call in totals["calls"]]
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        ),
        "calls": calls,
    }


def publish_token_usage(
    *,
    trace_id: str | None = None,
    call_id: str,
    task: str,
    attempt: int | None,
    token_usage: dict | None,
    session_type: str | None,
) -> None:
    """Publish provider-reported counts to the active player operation.

    Maintenance is intentionally trace-only. Usage reporting must never turn
    a successful model call into a failed narrative operation.
    """
    if session_type not in _PLAYER_SESSION_TYPES:
        return
    binding = _CURRENT_BINDING.get()
    sink = binding.sink if binding is not None else None
    if sink is None and trace_id:
        with _TRACE_SINKS_LOCK:
            sink = _TRACE_SINKS.get(str(trace_id).strip())
    if sink is None:
        return
    normalized = normalize_token_usage(token_usage)
    clean_trace_id = str(trace_id or "").strip()
    call_index: int | None = None
    if clean_trace_id:
        with _TRACE_SINKS_LOCK:
            totals = _TRACE_TOTALS.get(clean_trace_id)
            if totals is not None:
                input_tokens = normalized["input_tokens"]
                output_tokens = normalized["output_tokens"]
                if input_tokens is not None:
                    totals["input_tokens"] = int(totals["input_tokens"]) + input_tokens
                    totals["has_input"] = True
                if output_tokens is not None:
                    totals["output_tokens"] = (
                        int(totals["output_tokens"]) + output_tokens
                    )
                    totals["has_output"] = True
                call_indexes = totals["call_indexes"]
                task_counts = totals["task_counts"]
                call_index = _next_call_index(
                    call_indexes,
                    task_counts,
                    call_id=call_id,
                    task=str(task),
                )
                totals["calls"].append({
                    "task": str(task),
                    "call_index": call_index,
                    "attempt": max(1, int(attempt or 1)),
                    **normalized,
                })
    if call_index is None and binding is not None:
        call_index = binding.call_index(call_id, str(task))
    if call_index is None:
        return
    try:
        sink({
            "_call_id": call_id,
            "task": str(task),
            "call_index": call_index,
            "attempt": max(1, int(attempt or 1)),
            **normalized,
        })
    except Exception:
        pass


def normalize_token_usage(token_usage: object) -> dict[str, Any]:
    usage = token_usage if isinstance(token_usage, dict) else {}
    input_tokens = _count(usage, "input_tokens", "prompt_tokens")
    output_tokens = _count(usage, "output_tokens", "completion_tokens")
    total_tokens = _count(usage, "total_tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "reported": any(
            value is not None
            for value in (input_tokens, output_tokens, total_tokens)
        ),
    }


def _next_call_index(
    call_indexes: dict[str, int],
    task_counts: dict[str, int],
    *,
    call_id: str,
    task: str,
) -> int:
    call_index = call_indexes.get(call_id)
    if call_index is not None:
        return call_index
    call_index = task_counts.get(task, 0) + 1
    task_counts[task] = call_index
    if call_id:
        call_indexes[call_id] = call_index
    return call_index


def _count(usage: dict, *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None
