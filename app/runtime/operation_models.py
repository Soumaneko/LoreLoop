from __future__ import annotations

import copy
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


OperationStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]

TERMINAL_OPERATION_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def operation_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class OperationEvent:
    operation_id: str
    sequence: int
    step: str
    data: dict[str, Any]
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperationEventWindow:
    events: tuple[OperationEvent, ...]
    first_available_sequence: int
    last_sequence: int
    truncated: bool


class OperationEventBuffer:
    """Thread-safe, operation-scoped, bounded event history."""

    def __init__(self, operation_id: str, *, max_events: int = 500) -> None:
        if max_events < 1:
            raise ValueError("max_events must be >= 1")
        self.operation_id = operation_id
        self._events: deque[OperationEvent] = deque(maxlen=max_events)
        self._next_sequence = 1
        self._guard = threading.RLock()

    def append(self, step: str, data: dict[str, Any] | None = None) -> OperationEvent:
        if not isinstance(step, str):
            raise TypeError("operation event step must be a string")
        clean_step = step.strip()
        if not clean_step:
            raise ValueError("operation event step cannot be empty")
        if data is not None and not isinstance(data, dict):
            raise TypeError("operation event data must be a dict")
        with self._guard:
            event = OperationEvent(
                operation_id=self.operation_id,
                sequence=self._next_sequence,
                step=clean_step,
                data=copy.deepcopy(data or {}),
                created_at=operation_timestamp(),
            )
            self._next_sequence += 1
            self._events.append(event)
            return event

    def read(self, *, after_sequence: int = 0) -> OperationEventWindow:
        if after_sequence < 0:
            raise ValueError("after_sequence must be >= 0")
        with self._guard:
            first_available = (
                self._events[0].sequence
                if self._events
                else self._next_sequence
            )
            last_sequence = self._next_sequence - 1
            events = tuple(
                copy.deepcopy(event)
                for event in self._events
                if event.sequence > after_sequence
            )
            return OperationEventWindow(
                events=events,
                first_available_sequence=first_available,
                last_sequence=last_sequence,
                truncated=(after_sequence < first_available - 1),
            )


@dataclass(frozen=True)
class OperationSnapshot:
    operation_id: str
    campaign_id: str
    operation_type: str
    status: OperationStatus
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    acknowledged_at: str | None = None
    superseded_by_operation_id: str | None = None
    current_step: str | None = None
    request_id: str | None = None
    requires_ack: bool = False
    supersedes_recoverable_types: tuple[str, ...] = ()
    result: Any = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperationEventBatch:
    operation_id: str
    status: OperationStatus
    current_step: str | None
    events: tuple[OperationEvent, ...]
    first_available_sequence: int
    last_sequence: int
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
