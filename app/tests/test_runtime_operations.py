import threading
from types import SimpleNamespace

import pytest

from app import api
from app.runtime.operations import OperationEventBuffer, OperationManager
from app.runtime.session import get_campaign_runtime


def test_event_buffer_assigns_sequences_and_reports_truncation():
    buffer = OperationEventBuffer("operation-1", max_events=2)
    payload = {"value": 1}
    buffer.append("first", payload)
    payload["value"] = 99
    buffer.append("second", {"value": 2})
    buffer.append("third", {"value": 3})

    window = buffer.read(after_sequence=0)

    assert [event.sequence for event in window.events] == [2, 3]
    assert [event.step for event in window.events] == ["second", "third"]
    assert window.first_available_sequence == 2
    assert window.last_sequence == 3
    assert window.truncated is True
    assert buffer.read(after_sequence=2).truncated is False


def test_operation_manager_drains_stream_and_retains_result_and_events():
    manager = OperationManager(
        "campaign-1",
        max_workers=1,
        max_events_per_operation=10,
    )

    def stream():
        yield {"step": "started", "data": {"attempt": 1}}
        yield {"step": "written", "data": {"turn_id": "turn-1"}}
        return {"turn_id": "turn-1"}

    try:
        operation_id = manager.submit_stream(
            "generation",
            stream,
            request_id="request-1",
            metadata={"source": "player"},
        )
        snapshot = manager.wait(operation_id, timeout=2)
        batch = manager.read_events(operation_id)

        assert snapshot.campaign_id == "campaign-1"
        assert snapshot.operation_type == "generation"
        assert snapshot.status == "succeeded"
        assert snapshot.current_step == "written"
        assert snapshot.result == {"turn_id": "turn-1"}
        assert snapshot.metadata == {"source": "player"}
        assert [event.sequence for event in batch.events] == [1, 2]
        assert [event.step for event in batch.events] == ["started", "written"]
        assert batch.status == "succeeded"
    finally:
        manager.shutdown()


def test_operation_manager_records_stream_failure_without_losing_prior_events():
    manager = OperationManager("campaign-1", max_workers=1)

    def stream():
        yield {"step": "started", "data": {}}
        raise ValueError("broken pipeline")

    try:
        operation_id = manager.submit_stream("confirmation", stream)
        snapshot = manager.wait(operation_id, timeout=2)
        batch = manager.read_events(operation_id)

        assert snapshot.status == "failed"
        assert snapshot.error_type == "ValueError"
        assert snapshot.error_message == "broken pipeline"
        assert [event.step for event in batch.events] == ["started"]
        assert batch.status == "failed"
    finally:
        manager.shutdown()


def test_request_id_is_idempotent_for_the_same_operation_type():
    manager = OperationManager("campaign-1", max_workers=1)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def stream():
        calls.append("called")
        started.set()
        assert release.wait(timeout=2)
        yield {"step": "finished", "data": {}}

    try:
        first_id = manager.submit_stream(
            "generation",
            stream,
            request_id="same-request",
        )
        assert started.wait(timeout=2)
        second_id = manager.submit_stream(
            "generation",
            stream,
            request_id="same-request",
        )
        with pytest.raises(ValueError, match="different operation type"):
            manager.submit_stream(
                "confirmation",
                stream,
                request_id="same-request",
            )
        release.set()
        manager.wait(first_id, timeout=2)

        assert first_id == second_id
        assert calls == ["called"]
    finally:
        release.set()
        manager.shutdown()


def test_event_reader_can_wait_for_the_next_event():
    manager = OperationManager("campaign-1", max_workers=1)
    ready = threading.Event()
    release = threading.Event()

    def stream():
        ready.set()
        assert release.wait(timeout=2)
        yield {"step": "available", "data": {"value": 1}}

    try:
        operation_id = manager.submit_stream("generation", stream)
        assert ready.wait(timeout=2)
        release.set()

        batch = manager.read_events(
            operation_id,
            after_sequence=0,
            wait_timeout=2,
        )

        assert [event.step for event in batch.events] == ["available"]
    finally:
        release.set()
        manager.shutdown()


def test_exclusive_key_rejects_parallel_operation_and_releases_on_finish():
    manager = OperationManager("campaign-1", max_workers=2)
    started = threading.Event()
    release = threading.Event()

    def blocked_stream():
        started.set()
        assert release.wait(timeout=2)
        yield {"step": "finished", "data": {}}

    def quick_stream():
        yield {"step": "finished", "data": {}}

    try:
        first_id = manager.submit_stream(
            "generation",
            blocked_stream,
            exclusive_key="generation",
        )
        assert started.wait(timeout=2)
        with pytest.raises(RuntimeError, match="already active"):
            manager.submit_stream(
                "retry",
                quick_stream,
                exclusive_key="generation",
            )

        release.set()
        assert manager.wait(first_id, timeout=2).status == "succeeded"
        second_id = manager.submit_stream(
            "retry",
            quick_stream,
            exclusive_key="generation",
        )
        assert manager.wait(second_id, timeout=2).status == "succeeded"
    finally:
        release.set()
        manager.shutdown()


def test_public_api_starts_generation_as_background_operation(monkeypatch):
    runtime = get_campaign_runtime()
    captured = {}

    def generation_stream(user_input, difficulty, max_chars):
        captured.update({
            "user_input": user_input,
            "difficulty": difficulty,
            "max_chars": max_chars,
        })
        yield {"step": "scene_written", "data": {"turn_id": "turn-1"}}

    monkeypatch.setattr(api, "generate_turn_stream", generation_stream)

    operation_id = api.start_generation_operation(
        "调查钟楼",
        difficulty="普通",
        max_chars=1200,
        request_id="request-public-api",
    )
    snapshot = runtime.operations.wait(operation_id, timeout=2)

    assert snapshot.status == "succeeded"
    assert snapshot.operation_type == "generation"
    assert snapshot.metadata == {"user_input": "调查钟楼"}
    assert captured == {
        "user_input": "调查钟楼",
        "difficulty": "普通",
        "max_chars": 1200,
    }


def test_public_api_starts_retry_with_pending_turn_metadata(monkeypatch):
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(SimpleNamespace(
        turn_id="turn-v1",
        group_id="group-1",
        version_index=1,
        user_input="调查钟楼",
    ))

    def retry_stream(turn_id):
        yield {"step": "retry_started", "data": {"turn_id": turn_id}}

    monkeypatch.setattr(api, "retry_turn_stream", retry_stream)

    operation_id = api.start_retry_operation(
        "turn-v1",
        request_id="request-retry-api",
    )
    snapshot = runtime.operations.wait(operation_id, timeout=2)

    assert snapshot.status == "succeeded"
    assert snapshot.operation_type == "retry"
    assert snapshot.metadata == {
        "turn_id": "turn-v1",
        "group_id": "group-1",
        "user_input": "调查钟楼",
    }


def test_public_api_starts_confirmation_with_pending_turn_metadata(monkeypatch):
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(SimpleNamespace(
        turn_id="turn-confirm",
        group_id="group-confirm",
        version_index=1,
        user_input="推开石门",
    ))
    captured = {}

    def confirmation_stream(turn_id, *, write_db, scene_text):
        captured.update({
            "turn_id": turn_id,
            "write_db": write_db,
            "scene_text": scene_text,
        })
        yield {"step": "confirmation_started", "data": {"turn_id": turn_id}}

    monkeypatch.setattr(api, "confirm_stream", confirmation_stream)

    operation_id = api.start_confirmation_operation(
        "turn-confirm",
        write_db=True,
        scene_text="修改后的正文",
        request_id="request-confirm-api",
    )
    snapshot = runtime.operations.wait(operation_id, timeout=2)

    assert snapshot.status == "succeeded"
    assert snapshot.operation_type == "confirmation"
    assert snapshot.metadata == {
        "turn_id": "turn-confirm",
        "kind": "turn",
        "user_input": "推开石门",
        "write_db": True,
    }
    assert captured == {
        "turn_id": "turn-confirm",
        "write_db": True,
        "scene_text": "修改后的正文",
    }


def test_public_api_starts_manual_maintenance_as_operation(monkeypatch):
    runtime = get_campaign_runtime()

    def maintenance_stream():
        yield {"step": "memory_draft_started", "data": {}}
        yield {"step": "finish", "data": {"processed": 2}}

    monkeypatch.setattr(api, "batch_classify_stream", maintenance_stream)

    operation_id = api.start_batch_classify_operation(
        request_id="request-maintenance-api",
    )
    snapshot = runtime.operations.wait(operation_id, timeout=2)

    assert snapshot.status == "succeeded"
    assert snapshot.operation_type == "maintenance_batch_classify_manual"
    assert snapshot.metadata == {"manual": True}
    assert [
        event.step for event in runtime.operations.read_events(operation_id).events
    ] == ["memory_draft_started", "finish"]


def test_public_api_starts_both_initialization_stages_as_operations(monkeypatch):
    runtime = get_campaign_runtime()

    def direction_stream(payload):
        yield {
            "step": "story_direction_done",
            "data": {"config": payload, "story_direction": {"content": "向北"}},
        }

    def opening_stream(payload):
        yield {
            "step": "initialization_pending",
            "data": {"turn_id": "opening:1", "status": "awaiting_confirm"},
        }

    monkeypatch.setattr(api, "generate_story_direction_stream", direction_stream)
    monkeypatch.setattr(api, "initialization_stream", opening_stream)

    direction_id = api.start_story_direction_operation(
        {"story_expectation": "探索"},
        request_id="request-direction-api",
    )
    direction = runtime.operations.wait(direction_id, timeout=2)
    opening_id = api.start_initialization_operation(
        {"config": {}, "story_direction": {}},
        request_id="request-opening-api",
    )
    opening = runtime.operations.wait(opening_id, timeout=2)

    assert direction.status == "succeeded"
    assert direction.operation_type == "initialization_story_direction"
    assert direction.metadata == {"stage": "story_direction"}
    assert opening.status == "succeeded"
    assert opening.operation_type == "initialization_opening"
    assert opening.metadata == {"stage": "opening"}


def test_submit_callable_runs_inside_operation_executor():
    manager = OperationManager("campaign-1", max_workers=1)
    try:
        operation_id = manager.submit_callable(
            "maintenance_scene_summary",
            lambda value: value + 1,
            2,
            metadata={"turn_index": 3},
        )

        assert manager.future(operation_id).result(timeout=2) == 3
        snapshot = manager.get(operation_id)
        assert snapshot.status == "succeeded"
        assert snapshot.result == 3
        assert snapshot.metadata == {"turn_index": 3}
    finally:
        manager.shutdown()
