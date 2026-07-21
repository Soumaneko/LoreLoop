from __future__ import annotations

import concurrent.futures
import copy
import threading
import time
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from app.runtime.operation_models import (
    TERMINAL_OPERATION_STATUSES,
    OperationEvent,
    OperationEventBatch,
    OperationEventBuffer,
    OperationSnapshot,
    OperationStatus,
    operation_timestamp,
)


@dataclass
class _OperationRecord:
    operation_id: str
    campaign_id: str
    operation_type: str
    status: OperationStatus
    submitted_at: str
    event_buffer: OperationEventBuffer
    started_at: str | None = None
    finished_at: str | None = None
    acknowledged_at: str | None = None
    superseded_by_operation_id: str | None = None
    current_step: str | None = None
    request_id: str | None = None
    requires_ack: bool = False
    supersedes_recoverable_types: tuple[str, ...] = ()
    exclusive_key: str | None = None
    result: Any = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> OperationSnapshot:
        return OperationSnapshot(
            operation_id=self.operation_id,
            campaign_id=self.campaign_id,
            operation_type=self.operation_type,
            status=self.status,
            submitted_at=self.submitted_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            acknowledged_at=self.acknowledged_at,
            superseded_by_operation_id=self.superseded_by_operation_id,
            current_step=self.current_step,
            request_id=self.request_id,
            requires_ack=self.requires_ack,
            supersedes_recoverable_types=self.supersedes_recoverable_types,
            result=copy.deepcopy(self.result),
            error_type=self.error_type,
            error_message=self.error_message,
            metadata=copy.deepcopy(self.metadata),
        )


class OperationManager:
    """Own long-running pipeline execution independently from HTTP clients."""

    def __init__(
        self,
        campaign_id: str,
        *,
        max_workers: int = 2,
        max_history: int = 200,
        max_events_per_operation: int = 500,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if max_history < 1:
            raise ValueError("max_history must be >= 1")
        if max_events_per_operation < 1:
            raise ValueError("max_events_per_operation must be >= 1")
        self.campaign_id = campaign_id
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._max_history = max_history
        self._max_events_per_operation = max_events_per_operation
        self._guard = threading.RLock()
        self._condition = threading.Condition(self._guard)
        self._records: OrderedDict[str, _OperationRecord] = OrderedDict()
        self._futures: dict[str, concurrent.futures.Future] = {}
        self._request_index: dict[str, str] = {}
        self._exclusive_index: dict[str, str] = {}
        self._shutdown = False

    def submit_stream(
        self,
        operation_type: str,
        stream_factory: Callable,
        /,
        *args,
        request_id: str | None = None,
        exclusive_key: str | None = None,
        requires_ack: bool = False,
        supersedes_recoverable_types: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> str:
        clean_type = str(operation_type).strip()
        if not clean_type:
            raise ValueError("operation_type cannot be empty")
        clean_request_id = str(request_id).strip() if request_id is not None else None
        if request_id is not None and not clean_request_id:
            raise ValueError("request_id cannot be empty")
        clean_exclusive_key = (
            str(exclusive_key).strip() if exclusive_key is not None else None
        )
        if exclusive_key is not None and not clean_exclusive_key:
            raise ValueError("exclusive_key cannot be empty")
        clean_supersedes = tuple(
            str(candidate).strip()
            for candidate in supersedes_recoverable_types
            if str(candidate).strip()
        )

        with self._condition:
            if self._shutdown:
                raise RuntimeError("operation manager is shut down")
            if clean_request_id is not None:
                existing_id = self._request_index.get(clean_request_id)
                if existing_id is not None:
                    existing = self._records[existing_id]
                    if existing.operation_type != clean_type:
                        raise ValueError(
                            "request_id is already used by a different operation type"
                        )
                    return existing_id
            if clean_exclusive_key is not None:
                active_id = self._exclusive_index.get(clean_exclusive_key)
                if active_id is not None:
                    raise RuntimeError(
                        "another operation is already active for this exclusive key"
                    )

            operation_id = uuid.uuid4().hex
            record = _OperationRecord(
                operation_id=operation_id,
                campaign_id=self.campaign_id,
                operation_type=clean_type,
                status="queued",
                submitted_at=operation_timestamp(),
                request_id=clean_request_id,
                exclusive_key=clean_exclusive_key,
                requires_ack=bool(requires_ack),
                supersedes_recoverable_types=clean_supersedes,
                metadata=copy.deepcopy(metadata or {}),
                event_buffer=OperationEventBuffer(
                    operation_id,
                    max_events=self._max_events_per_operation,
                ),
            )
            self._records[operation_id] = record
            if clean_request_id is not None:
                self._request_index[clean_request_id] = operation_id
            if clean_exclusive_key is not None:
                self._exclusive_index[clean_exclusive_key] = operation_id
            try:
                future = self.executor.submit(
                    self._run_stream,
                    operation_id,
                    stream_factory,
                    args,
                    kwargs,
                )
            except BaseException:
                self._remove_record_locked(operation_id)
                raise
            self._futures[operation_id] = future
            self._condition.notify_all()
            return operation_id

    def submit_callable(
        self,
        operation_type: str,
        fn: Callable,
        /,
        *args,
        request_id: str | None = None,
        exclusive_key: str | None = None,
        requires_ack: bool = False,
        supersedes_recoverable_types: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> str:
        def callable_stream():
            result = fn(*args, **kwargs)
            if False:
                yield None
            return result

        return self.submit_stream(
            operation_type,
            callable_stream,
            request_id=request_id,
            exclusive_key=exclusive_key,
            requires_ack=requires_ack,
            supersedes_recoverable_types=supersedes_recoverable_types,
            metadata=metadata,
        )

    def future(self, operation_id: str) -> concurrent.futures.Future:
        with self._guard:
            self._require_record_locked(operation_id)
            future = self._futures.get(operation_id)
            if future is None:
                raise RuntimeError("operation future is no longer available")
            return future

    def emit(
        self,
        operation_id: str,
        step: str,
        data: dict[str, Any] | None = None,
    ) -> OperationEvent:
        with self._condition:
            record = self._require_record_locked(operation_id)
            if record.status in TERMINAL_OPERATION_STATUSES:
                raise RuntimeError("cannot append events to a terminal operation")
            event = record.event_buffer.append(step, data)
            record.current_step = event.step
            self._condition.notify_all()
            return event

    def get(self, operation_id: str) -> OperationSnapshot | None:
        with self._guard:
            record = self._records.get(operation_id)
            return record.snapshot() if record is not None else None

    def list(
        self,
        *,
        status: OperationStatus | None = None,
        operation_type: str | None = None,
        active_only: bool = False,
        recoverable_only: bool = False,
        limit: int | None = 100,
    ) -> list[OperationSnapshot]:
        if limit is not None and limit < 1:
            return []
        with self._guard:
            snapshots = [
                record.snapshot()
                for record in reversed(self._records.values())
                if (status is None or record.status == status)
                and (operation_type is None or record.operation_type == operation_type)
                and (
                    not active_only
                    or record.status not in TERMINAL_OPERATION_STATUSES
                )
                and (
                    not recoverable_only
                    or record.status not in TERMINAL_OPERATION_STATUSES
                    or (
                        record.requires_ack
                        and record.acknowledged_at is None
                    )
                )
            ]
        return snapshots if limit is None else snapshots[:limit]

    def acknowledge(self, operation_id: str) -> OperationSnapshot:
        with self._condition:
            record = self._require_record_locked(operation_id)
            if record.status not in TERMINAL_OPERATION_STATUSES:
                raise RuntimeError("cannot acknowledge an active operation")
            if record.acknowledged_at is None:
                record.acknowledged_at = operation_timestamp()
                self._trim_history_locked()
                self._condition.notify_all()
            return record.snapshot()

    def status_counts(self) -> dict[str, int]:
        with self._guard:
            return dict(Counter(record.status for record in self._records.values()))

    def read_events(
        self,
        operation_id: str,
        *,
        after_sequence: int = 0,
        wait_timeout: float | None = None,
    ) -> OperationEventBatch:
        if wait_timeout is not None and wait_timeout < 0:
            raise ValueError("wait_timeout must be >= 0")
        deadline = None if wait_timeout is None else time.monotonic() + wait_timeout
        with self._condition:
            while True:
                record = self._require_record_locked(operation_id)
                window = record.event_buffer.read(after_sequence=after_sequence)
                if (
                    window.events
                    or record.status in TERMINAL_OPERATION_STATUSES
                    or wait_timeout is None
                ):
                    return self._event_batch(record, window)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._event_batch(record, window)
                self._condition.wait(remaining)

    def wait(
        self,
        operation_id: str,
        *,
        timeout: float | None = None,
    ) -> OperationSnapshot:
        with self._guard:
            record = self._require_record_locked(operation_id)
            future = self._futures.get(operation_id)
            if future is None:
                return record.snapshot()
        try:
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise
        except Exception:
            pass
        snapshot = self.get(operation_id)
        if snapshot is None:
            raise KeyError(f"operation not found: {operation_id}")
        return snapshot

    def wait_for_idle(self) -> None:
        while True:
            with self._guard:
                futures = [future for future in self._futures.values() if not future.done()]
            if not futures:
                return
            concurrent.futures.wait(futures)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._condition:
            self._shutdown = True
            self._condition.notify_all()
        self.executor.shutdown(wait=wait)

    def _run_stream(
        self,
        operation_id: str,
        stream_factory: Callable,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        with self._condition:
            record = self._require_record_locked(operation_id)
            record.status = "running"
            record.started_at = operation_timestamp()
            self._condition.notify_all()
        try:
            stream = iter(stream_factory(*args, **kwargs))
            while True:
                try:
                    raw_event = next(stream)
                except StopIteration as stop:
                    result = stop.value
                    break
                if not isinstance(raw_event, dict):
                    raise TypeError("operation stream events must be dicts")
                step = raw_event.get("step")
                data = raw_event.get("data", {})
                self.emit(operation_id, step, data)
        except BaseException as exc:
            self._finish(
                operation_id,
                status="failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        self._finish(operation_id, status="succeeded", result=result)
        return result

    def _finish(
        self,
        operation_id: str,
        *,
        status: OperationStatus,
        result: Any = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._condition:
            record = self._records.get(operation_id)
            if record is None:
                return
            record.status = status
            record.finished_at = operation_timestamp()
            record.result = copy.deepcopy(result)
            record.error_type = error_type
            record.error_message = error_message
            if status == "succeeded":
                self._supersede_recoverable_locked(record)
            if record.exclusive_key is not None:
                active_id = self._exclusive_index.get(record.exclusive_key)
                if active_id == operation_id:
                    self._exclusive_index.pop(record.exclusive_key, None)
            self._trim_history_locked()
            self._condition.notify_all()

    def _trim_history_locked(self) -> None:
        while len(self._records) > self._max_history:
            removable_id = next(
                (
                    operation_id
                    for operation_id, record in self._records.items()
                    if record.status in TERMINAL_OPERATION_STATUSES
                    and (
                        not record.requires_ack
                        or record.acknowledged_at is not None
                    )
                ),
                None,
            )
            if removable_id is None:
                return
            self._remove_record_locked(removable_id)

    def _supersede_recoverable_locked(self, completed: _OperationRecord) -> None:
        if not completed.supersedes_recoverable_types:
            return
        acknowledged_at = operation_timestamp()
        for record in self._records.values():
            if record.operation_id == completed.operation_id:
                continue
            if record.operation_type not in completed.supersedes_recoverable_types:
                continue
            if record.status not in TERMINAL_OPERATION_STATUSES:
                continue
            if not record.requires_ack or record.acknowledged_at is not None:
                continue
            record.acknowledged_at = acknowledged_at
            record.superseded_by_operation_id = completed.operation_id

    def _remove_record_locked(self, operation_id: str) -> None:
        record = self._records.pop(operation_id, None)
        self._futures.pop(operation_id, None)
        if record is not None and record.request_id is not None:
            self._request_index.pop(record.request_id, None)
        if record is not None and record.exclusive_key is not None:
            active_id = self._exclusive_index.get(record.exclusive_key)
            if active_id == operation_id:
                self._exclusive_index.pop(record.exclusive_key, None)

    def _require_record_locked(self, operation_id: str) -> _OperationRecord:
        record = self._records.get(operation_id)
        if record is None:
            raise KeyError(f"operation not found: {operation_id}")
        return record

    @staticmethod
    def _event_batch(
        record: _OperationRecord,
        window: OperationEventWindow,
    ) -> OperationEventBatch:
        return OperationEventBatch(
            operation_id=record.operation_id,
            status=record.status,
            current_step=record.current_step,
            events=window.events,
            first_available_sequence=window.first_available_sequence,
            last_sequence=window.last_sequence,
            truncated=window.truncated,
        )
