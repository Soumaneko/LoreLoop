from app.tests.factories.domains import make_pending_turn
from app.core.client_error import ClientValidationError
import concurrent.futures
import threading
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest

from app import api
from app.core.cancellation import CancellationToken, commit_if_not_cancelled
from app.core.client_error import ClientValidationError
from app.core.diagnostics import DiagnosticLogger
from app.core.llm.token_usage import get_trace_token_usage, publish_token_usage
from app.runtime.operation_models import OperationEventBuffer
from app.runtime.operations import OperationManager
from app.runtime.session import get_campaign_runtime


@pytest.fixture
def configured_runtime(monkeypatch, campaign_runtime):
    runtime = campaign_runtime
    monkeypatch.setattr(runtime, "is_model_configured", lambda: True)
    return runtime


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






def test_running_operation_cancels_at_checkpoint_without_emitting_failure():
    manager = OperationManager("campaign-1", max_workers=1)
    waiting = threading.Event()
    release = threading.Event()

    def stream():
        yield {"step": "started", "data": {}}
        waiting.set()
        assert release.wait(timeout=2)
        yield {"step": "must_not_be_emitted", "data": {}}

    try:
        operation_id = manager.submit_stream(
            "generation",
            stream,
            requires_ack=True,
        )
        assert waiting.wait(timeout=2)

        cancellation = manager.cancel(operation_id)
        assert cancellation.status == "cancellation_requested"
        release.set()

        snapshot = manager.wait(operation_id, timeout=2)
        assert snapshot.status == "cancelled"
        assert snapshot.error_type is None
        assert snapshot.error_message is None
        assert [
            event.step for event in manager.read_events(operation_id).events
        ] == ["started", "cancellation_requested"]
    finally:
        release.set()
        manager.shutdown()








def test_cancelled_operation_cannot_cross_final_commit_boundary():
    manager = OperationManager("campaign-1", max_workers=1)
    waiting = threading.Event()
    release = threading.Event()
    pending = []

    def stream():
        yield {"step": "pipeline_started", "data": {}}
        waiting.set()
        assert release.wait(timeout=2)
        commit_if_not_cancelled(lambda: pending.append("pending-turn"))
        yield {"step": "pending_created", "data": {}}

    try:
        operation_id = manager.submit_stream("generation", stream)
        assert waiting.wait(timeout=2)
        assert manager.cancel(operation_id).status == "cancellation_requested"
        release.set()

        assert manager.wait(operation_id, timeout=2).status == "cancelled"
        assert pending == []
    finally:
        release.set()
        manager.shutdown()


def test_cancellation_token_rejects_cancel_after_atomic_commit():
    token = CancellationToken()
    committed = []

    token.commit(lambda: committed.append("done"))

    assert token.request() is False
    assert committed == ["done"]

    cancelled = CancellationToken()
    assert cancelled.request() is True
    with pytest.raises(CancelledError):
        cancelled.commit(lambda: committed.append("must-not-run"))
    assert committed == ["done"]


def test_operation_manager_records_stream_failure_without_losing_prior_events(
    tmp_path,
):
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")
    manager = OperationManager(
        "campaign-1",
        max_workers=1,
        diagnostics=diagnostics,
    )

    def stream():
        yield {"step": "started", "data": {"trace_id": "failed-trace"}}
        raise ValueError("broken pipeline")

    try:
        operation_id = manager.submit_stream(
            "confirmation",
            stream,
            requires_ack=True,
        )
        snapshot = manager.wait(operation_id, timeout=2)
        batch = manager.read_events(operation_id)

        assert snapshot.status == "failed"
        assert snapshot.error_type == "ValueError"
        assert snapshot.error_message == "broken pipeline"
        assert [event.step for event in batch.events] == ["started", "runtime_notice"]
        assert snapshot.current_step == "started"
        assert batch.status == "failed"
        assert [
            operation.operation_id
            for operation in manager.list(recoverable_only=True)
        ] == [operation_id]
        diagnostic = diagnostics.read()[0]
        assert diagnostic["severity"] == "error"
        assert diagnostic["code"] == "operation_failed"
        assert diagnostic["operation_id"] == operation_id
        assert diagnostic["operation_type"] == "confirmation"
        assert get_trace_token_usage("failed-trace") is None
    finally:
        manager.shutdown()




def test_terminal_operation_remains_recoverable_until_acknowledged():
    manager = OperationManager("campaign-1", max_workers=1)

    def stream():
        yield {"step": "finished", "data": {"value": 1}}

    try:
        operation_id = manager.submit_stream(
            "initialization_campaign_setup",
            stream,
            requires_ack=True,
        )
        assert manager.wait(operation_id, timeout=2).status == "succeeded"
        background_id = manager.submit_stream("maintenance_scene_summary", stream)
        assert manager.wait(background_id, timeout=2).status == "succeeded"

        recoverable = manager.list(recoverable_only=True)
        assert [operation.operation_id for operation in recoverable] == [operation_id]
        assert recoverable[0].acknowledged_at is None

        acknowledged = manager.acknowledge(operation_id)

        assert acknowledged.acknowledged_at is not None
        assert manager.list(recoverable_only=True) == []
        assert manager.acknowledge(operation_id).acknowledged_at == (
            acknowledged.acknowledged_at
        )
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
        with pytest.raises(ClientValidationError, match="已有任务"):
            manager.submit_stream(
                "reroll",
                quick_stream,
                exclusive_key="generation",
            )

        release.set()
        assert manager.wait(first_id, timeout=2).status == "succeeded"
        second_id = manager.submit_stream(
            "reroll",
            quick_stream,
            exclusive_key="generation",
        )
        assert manager.wait(second_id, timeout=2).status == "succeeded"
    finally:
        release.set()
        manager.shutdown()


def test_public_api_starts_generation_as_background_operation(
    monkeypatch,
    configured_runtime,
):
    runtime = configured_runtime
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
    assert snapshot.requires_ack is True
    assert snapshot.supersedes_recoverable_types == ()
    assert captured == {
        "user_input": "调查钟楼",
        "difficulty": "普通",
        "max_chars": 1200,
    }






def test_public_api_starts_confirmation_with_pending_turn_metadata(
    monkeypatch,
    configured_runtime,
):
    runtime = configured_runtime
    runtime.pending_turns.add(make_pending_turn(
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
    assert snapshot.supersedes_recoverable_types == (
        "generation",
        "rephrase",
        "reroll",
        "feedback_rewrite",
        "opening_rewrite",
        "initialization_opening",
    )
    assert captured == {
        "turn_id": "turn-confirm",
        "write_db": True,
        "scene_text": "修改后的正文",
    }




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
