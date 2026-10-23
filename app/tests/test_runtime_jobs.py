import threading

import pytest

from app.graph import generation_graph
from app.runtime.jobs import MaintenanceJobCoordinator
from app.runtime.operations import OperationManager


def test_job_coordinator_records_success_and_failure():
    coordinator = MaintenanceJobCoordinator(max_workers=1)
    try:
        success = coordinator.submit_job(
            "summary",
            lambda: "ok",
            trace_id="trace-ok",
            turn_index=3,
        )
        assert success.result(timeout=2) == "ok"

        def fail():
            raise ValueError("broken")

        failure = coordinator.submit_job("memory", fail, trace_id="trace-fail")
        with pytest.raises(ValueError, match="broken"):
            failure.result(timeout=2)

        successful_job = coordinator.get(success.job_id)
        failed_job = coordinator.get(failure.job_id)
        assert successful_job.status == "succeeded"
        assert successful_job.trace_id == "trace-ok"
        assert successful_job.turn_index == 3
        assert successful_job.started_at is not None
        assert successful_job.finished_at is not None
        assert failed_job.status == "failed"
        assert failed_job.error_type == "ValueError"
        assert failed_job.error_message == "broken"
        assert coordinator.status_counts() == {"succeeded": 1, "failed": 1}
    finally:
        coordinator.shutdown()


def test_submit_forwards_all_keyword_arguments():
    coordinator = MaintenanceJobCoordinator(max_workers=1)
    try:
        future = coordinator.submit(
            lambda *, trace_id: trace_id,
            trace_id="forwarded",
        )
        assert future.result(timeout=2) == "forwarded"
        assert coordinator.get(future.job_id).status == "succeeded"
    finally:
        coordinator.shutdown()


def test_job_coordinator_delegates_execution_to_operation_manager():
    operations = OperationManager("campaign-1", max_workers=1)
    coordinator = MaintenanceJobCoordinator(
        max_workers=1,
        operation_manager=operations,
    )
    try:
        future = coordinator.submit_job(
            "scene_summary",
            lambda: "ok",
            trace_id="trace-1",
            turn_index=4,
            metadata={"scene_id": "scene-4"},
        )

        assert future.result(timeout=2) == "ok"
        job = coordinator.get(future.job_id)
        operation = operations.get(future.operation_id)
        assert job.status == "succeeded"
        assert operation.status == "succeeded"
        assert operation.operation_type == "maintenance_scene_summary"
        assert operation.metadata == {
            "scene_id": "scene-4",
            "maintenance_job_id": future.job_id,
            "trace_id": "trace-1",
            "turn_index": 4,
        }
    finally:
        coordinator.shutdown()
        operations.shutdown()


def test_compiled_generation_graph_reuses_configuration():
    builder = generation_graph.build_generation_graph
    builder.cache_clear()
    try:
        enabled = builder(canon_validation_is_enabled=True)
        assert builder(canon_validation_is_enabled=True) is enabled
        assert builder(canon_validation_is_enabled=False) is not enabled
    finally:
        builder.cache_clear()
