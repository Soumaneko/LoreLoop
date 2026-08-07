from __future__ import annotations

import concurrent.futures
import threading
import uuid
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal

from app.core.time import utc_now
from app.runtime.operations import OperationManager


JobStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "superseded",
]


def _utc_now() -> str:
    return utc_now()


@dataclass(frozen=True)
class MaintenanceJobSnapshot:
    job_id: str
    job_type: str
    status: JobStatus
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    trace_id: str | None = None
    turn_index: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    rerun_of: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _MaintenanceJobRecord:
    job_id: str
    job_type: str
    status: JobStatus
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    trace_id: str | None = None
    turn_index: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    rerun_of: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> MaintenanceJobSnapshot:
        return MaintenanceJobSnapshot(
            job_id=self.job_id,
            job_type=self.job_type,
            status=self.status,
            submitted_at=self.submitted_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            trace_id=self.trace_id,
            turn_index=self.turn_index,
            error_type=self.error_type,
            error_message=self.error_message,
            rerun_of=self.rerun_of,
            metadata=dict(self.metadata),
        )


class MaintenanceJobCoordinator:
    """Run maintenance work and retain bounded, queryable job state."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_history: int = 200,
        operation_manager: OperationManager | None = None,
    ) -> None:
        if max_history < 1:
            raise ValueError("max_history must be >= 1")
        self.operation_manager = operation_manager
        self.executor = (
            None
            if operation_manager is not None
            else concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        )
        self._max_history = max_history
        self._guard = threading.RLock()
        self._records: OrderedDict[str, _MaintenanceJobRecord] = OrderedDict()
        self._active_futures: set[concurrent.futures.Future] = set()

    def submit(self, fn, /, *args, **kwargs) -> concurrent.futures.Future:
        job_id = self._create_record(
            getattr(fn, "__name__", "maintenance"),
        )
        return self._submit_record(job_id, fn, args, kwargs)

    def submit_job(
        self,
        job_type: str,
        fn,
        /,
        *args,
        trace_id: str | None = None,
        turn_index: int | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> concurrent.futures.Future:
        job_id = self._create_record(
            job_type,
            trace_id=trace_id,
            turn_index=turn_index,
            metadata=metadata,
        )
        return self._submit_record(job_id, fn, args, kwargs)

    def get(self, job_id: str) -> MaintenanceJobSnapshot | None:
        with self._guard:
            record = self._records.get(job_id)
            return record.snapshot() if record is not None else None

    def list(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        limit: int = 100,
    ) -> list[MaintenanceJobSnapshot]:
        if limit < 1:
            return []
        with self._guard:
            records = reversed(self._records.values())
            snapshots = [
                record.snapshot()
                for record in records
                if (status is None or record.status == status)
                and (job_type is None or record.job_type == job_type)
            ]
        return snapshots[:limit]

    def status_counts(self) -> dict[str, int]:
        with self._guard:
            return dict(Counter(record.status for record in self._records.values()))

    def shutdown(self, *, wait: bool = True) -> None:
        if self.executor is not None:
            self.executor.shutdown(wait=wait)
        elif wait:
            self.wait_for_idle()

    def wait_for_idle(self) -> None:
        """Wait until current work and any queued single-flight rerun finish."""
        while True:
            with self._guard:
                futures = list(self._active_futures)
            if not futures:
                return
            concurrent.futures.wait(futures)

    def _create_record(
        self,
        job_type: str,
        *,
        trace_id: str | None = None,
        turn_index: int | None = None,
        metadata: dict[str, Any] | None = None,
        rerun_of: str | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        with self._guard:
            self._records[job_id] = _MaintenanceJobRecord(
                job_id=job_id,
                job_type=job_type,
                status="queued",
                submitted_at=_utc_now(),
                trace_id=trace_id,
                turn_index=turn_index,
                rerun_of=rerun_of,
                metadata=dict(metadata or {}),
            )
            self._trim_history()
        return job_id

    def _submit_record(self, job_id, fn, args, kwargs):
        if self.operation_manager is None:
            future = self.executor.submit(
                self._run_record,
                job_id,
                fn,
                args,
                kwargs,
            )
        else:
            with self._guard:
                record = self._records[job_id]
                operation_type = f"maintenance_{record.job_type}"
                metadata = {
                    **record.metadata,
                    "maintenance_job_id": job_id,
                    "trace_id": record.trace_id,
                    "turn_index": record.turn_index,
                }
            operation_id = self.operation_manager.submit_callable(
                operation_type,
                self._run_record,
                job_id,
                fn,
                args,
                kwargs,
                metadata=metadata,
            )
            future = self.operation_manager.future(operation_id)
            setattr(future, "operation_id", operation_id)
        with self._guard:
            self._active_futures.add(future)
        setattr(future, "job_id", job_id)
        future.add_done_callback(
            lambda completed: self._mark_cancelled(job_id) if completed.cancelled() else None
        )
        future.add_done_callback(self._forget_future)
        return future

    def _forget_future(self, future) -> None:
        with self._guard:
            self._active_futures.discard(future)

    def _run_record(self, job_id, fn, args, kwargs):
        with self._guard:
            record = self._records[job_id]
            record.status = "running"
            record.started_at = _utc_now()
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            self._finish_record(
                job_id,
                status="failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        self._finish_record(job_id, status="succeeded")
        return result

    def _finish_record(
        self,
        job_id: str,
        *,
        status: JobStatus,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._guard:
            record = self._records.get(job_id)
            if record is None:
                return
            record.status = status
            record.finished_at = _utc_now()
            record.error_type = error_type
            record.error_message = error_message
            self._trim_history()

    def _mark_cancelled(self, job_id: str) -> None:
        self._finish_record(job_id, status="cancelled")

    def _trim_history(self) -> None:
        while len(self._records) > self._max_history:
            removable_id = next(
                (
                    job_id
                    for job_id, record in self._records.items()
                    if record.status not in {"queued", "running"}
                ),
                None,
            )
            if removable_id is None:
                return
            self._records.pop(removable_id, None)
