import pytest

import io
import json
import threading
from types import SimpleNamespace
from urllib.parse import urlparse
from app import player_server
from app.api import record_player_diagnostic
from app.core.diagnostics import DiagnosticLogger
from app.debug_server import DebugHandler
from app.runtime.operations import OperationManager
from app.runtime.session import get_campaign_runtime


class _DisconnectedWriter:
    def write(self, _payload):
        raise BrokenPipeError

    def flush(self):
        pass



def test_player_diagnostics_are_written_to_the_separate_diagnostic_log(
    monkeypatch,
    tmp_path,
):
    runtime = get_campaign_runtime()
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")
    monkeypatch.setattr(runtime, "diagnostics", diagnostics)

    assert record_player_diagnostic({
        "severity": "error",
        "code": "player_frontend_error",
        "message": "render failed",
        "context": "window_error",
        "stack": "Error: render failed",
        "path": "/",
    }) == {"recorded": True}

    entry = diagnostics.read()[0]
    assert entry["source"] == "player_frontend"
    assert entry["campaign_id"] == "pytest"
    assert entry["details"] == {
        "context": "window_error",
        "stack": "Error: render failed",
        "path": "/",
    }


@pytest.mark.parametrize(
    "operation_type",
    [
        "generation",
        "confirmation",
        "initialization_campaign_setup",
        "initialization_opening",
        "opening_rewrite",
    ],
)
def test_pipeline_operation_continues_after_subscriber_disconnect(
    monkeypatch,
    operation_type,
):
    manager = OperationManager("disconnect-test", max_workers=1)
    handler = object.__new__(DebugHandler)
    handler.send_response = lambda _status: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None
    handler.wfile = _DisconnectedWriter()
    handler.path = "/api/player/generate"
    release = threading.Event()
    completed = []

    def generation_events():
        yield {"step": "state_loaded", "data": {}}
        assert release.wait(timeout=2)
        completed.append("continued")
        yield {"step": "scene_written", "data": {}}
        completed.append("finished")

    monkeypatch.setattr(
        player_server,
        "get_campaign_runtime",
        lambda: SimpleNamespace(operations=manager),
    )
    try:
        operation_id = manager.submit_stream(operation_type, generation_events)
        handler._handle_operation_event_stream(
            operation_id,
            urlparse(handler.path),
            player=True,
            resumed=False,
        )
        release.set()

        assert manager.wait(operation_id, timeout=2).status == "succeeded"
        assert completed == ["continued", "finished"]
    finally:
        release.set()
        manager.shutdown()


def test_cancelled_player_operation_ends_sse_without_failure_payload(monkeypatch):
    manager = OperationManager("cancel-stream-test", max_workers=1)
    handler = object.__new__(DebugHandler)
    handler.send_response = lambda _status: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None
    handler.wfile = io.BytesIO()
    handler.path = "/api/player/generate"
    waiting = threading.Event()
    release = threading.Event()

    def generation_events():
        yield {"step": "state_loaded", "data": {}}
        waiting.set()
        assert release.wait(timeout=2)
        yield {"step": "must_not_be_emitted", "data": {}}

    monkeypatch.setattr(
        player_server,
        "get_campaign_runtime",
        lambda: SimpleNamespace(operations=manager),
    )
    try:
        operation_id = manager.submit_stream("generation", generation_events)
        assert waiting.wait(timeout=2)
        subscriber = threading.Thread(
            target=handler._handle_operation_event_stream,
            args=(operation_id, urlparse(handler.path)),
            kwargs={"player": True, "resumed": False},
        )
        subscriber.start()
        manager.cancel(operation_id)
        release.set()
        subscriber.join(timeout=2)

        payload = handler.wfile.getvalue().decode("utf-8")
        assert '"step": "operation_cancelled"' in payload
        assert '"step": "done"' in payload
        assert '"ok": false' not in payload
    finally:
        release.set()
        manager.shutdown()


def test_completed_player_operation_is_returned_until_acknowledged(monkeypatch):
    captured = {}

    def list_recoverable(**kwargs):
        captured.update(kwargs)
        return [
            {
                "operation_id": "operation-newer",
                "operation_type": "initialization_opening",
                "status": "running",
                "current_step": "initialization_started",
                "submitted_at": "later",
                "started_at": "later",
                "metadata": {"stage": "opening"},
            },
            {
                "operation_id": "operation-finished",
                "operation_type": "initialization_campaign_setup",
                "status": "succeeded",
                "current_step": "campaign_setup_done",
                "submitted_at": "earlier",
                "started_at": "earlier",
                "metadata": {"stage": "campaign_setup"},
            },
        ]

    monkeypatch.setattr(player_server, "list_operations", list_recoverable)
    handler = object.__new__(DebugHandler)

    result = handler._load_recoverable_player_operations()

    assert captured == {"recoverable_only": True, "limit": None}
    assert [operation["operation_id"] for operation in result["operations"]] == [
        "operation-finished",
        "operation-newer",
    ]
    assert result["operations"][0]["recovery_kind"] == "initialization"


def test_operation_ack_path_parser_rejects_ambiguous_paths():
    assert DebugHandler._operation_id_from_action_path(
        "/api/player/operations/operation-1/ack",
        player=True,
        action="ack",
    ) == "operation-1"
    assert DebugHandler._operation_id_from_action_path(
        "/api/player/operations/operation-1/cancel",
        player=True,
        action="cancel",
    ) == "operation-1"
    with pytest.raises(ValueError, match="Invalid"):
        DebugHandler._operation_id_from_action_path(
            "/api/player/operations/operation-1/extra/ack",
            player=True,
            action="ack",
        )


def test_player_ack_removes_completed_operation_from_recovery():
    runtime = get_campaign_runtime()

    def stream():
        yield {"step": "campaign_setup_done", "data": {}}

    operation_id = runtime.operations.submit_stream(
        "initialization_campaign_setup",
        stream,
        requires_ack=True,
    )
    runtime.operations.wait(operation_id, timeout=2)
    handler = object.__new__(DebugHandler)

    result = handler._acknowledge_player_operation(
        f"/api/player/operations/{operation_id}/ack"
    )

    assert result == {"operation_id": operation_id, "acknowledged": True}
    assert operation_id not in {
        operation.operation_id
        for operation in runtime.operations.list(recoverable_only=True)
    }


def test_feedback_rewrite_rejects_non_string_payload_fields(monkeypatch):
    handler = object.__new__(player_server.PlayerHandler)
    monkeypatch.setattr(
        player_server,
        "_read_json",
        lambda _self: {
            "turn_id": "turn_1",
            "source_scene_text": None,
            "user_feedback": "改一下",
        },
    )
    monkeypatch.setattr(
        player_server,
        "start_feedback_rewrite_operation",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not start with non-string source_scene_text")
        ),
    )

    with pytest.raises(
        player_server.ClientValidationError,
        match="source_scene_text must be a string",
    ):
        handler._start_feedback_rewrite_operation()

    monkeypatch.setattr(
        player_server,
        "_read_json",
        lambda _self: {
            "turn_id": "turn_1",
            "source_scene_text": "正文",
            "user_feedback": [],
        },
    )
    with pytest.raises(
        player_server.ClientValidationError,
        match="user_feedback must be a string",
    ):
        handler._start_feedback_rewrite_operation()
