import threading

import pytest

from app.graph import generation_graph
from app.runtime.graph_cache import clear_compiled_graph_cache
from app.runtime.jobs import MaintenanceJobCoordinator


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


def test_memory_batch_single_flight_tracks_superseded_rerun():
    coordinator = MaintenanceJobCoordinator(max_workers=1)
    started = threading.Event()
    release = threading.Event()
    rerun_finished = threading.Event()
    calls = []

    def work(label):
        calls.append(label)
        if label == "first":
            started.set()
            assert release.wait(timeout=2)
        else:
            rerun_finished.set()

    try:
        coordinator.submit_memory_batch_once(work, "first")
        assert started.wait(timeout=2)
        coordinator.submit_memory_batch_once(work, "second")
        coordinator.submit_memory_batch_once(work, "third")

        queued = coordinator.list(status="queued", job_type="memory_batch")
        superseded = coordinator.list(status="superseded", job_type="memory_batch")
        assert len(queued) == 1
        assert len(superseded) == 1
        assert queued[0].rerun_of is not None

        release.set()
        assert rerun_finished.wait(timeout=2)
    finally:
        coordinator.shutdown()

    assert calls == ["first", "third"]
    assert coordinator.status_counts() == {"succeeded": 2, "superseded": 1}


def test_compiled_generation_graph_is_cached_and_dependency_aware(monkeypatch):
    clear_compiled_graph_cache("generation")
    first = generation_graph.build_generation_graph()
    second = generation_graph.build_generation_graph()
    assert first is second

    original = generation_graph.compose_narrative_node

    def replacement(state):
        return original(state)

    monkeypatch.setattr(generation_graph, "compose_narrative_node", replacement)
    rebuilt = generation_graph.build_generation_graph()
    assert rebuilt is not first
