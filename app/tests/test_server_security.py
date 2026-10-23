import io
import json
import sys
import urllib.parse
from types import SimpleNamespace
from pathlib import Path
import pytest
from app import debug_server, player_server
from app.core.client_error import ClientValidationError
from app.player_server import PlayerHandler


def _make_handler(
    *,
    handler_class=debug_server.DebugHandler,
    token="test-token",
    auth="test-token",
    host="127.0.0.1:8765",
    bound_host="127.0.0.1",
    path="/api/state",
):
    handler = object.__new__(handler_class)
    handler.session_token = token
    handler.server = SimpleNamespace(server_address=(bound_host, 8765))
    headers = {"Host": host}
    if auth is not None:
        headers["X-LoreLoop-Token"] = auth
    handler.headers = headers
    handler.path = path
    handler.recorded_status = None
    handler.recorded_headers = {}
    handler.wfile = io.BytesIO()

    def send_response(status, message=None):
        handler.recorded_status = status

    def send_header(name, value):
        handler.recorded_headers[name] = value

    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = lambda: None
    return handler


def _body(handler) -> dict:
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


@pytest.mark.parametrize("auth, host, bound_host, status", [
    pytest.param(None, "127.0.0.1:8765", "127.0.0.1", 403, id="missing-token"),
    pytest.param("wrong-token", "127.0.0.1:8765", "127.0.0.1", 403, id="wrong-token"),
    pytest.param("test-token", "127.0.0.1:8765", "127.0.0.1", 200, id="authenticated"),
    pytest.param("test-token", "other.example:8765", "127.0.0.1", 403, id="wrong-host"),
    pytest.param("test-token", "other.example:8765", "0.0.0.0", 200, id="explicit-external"),
])
def test_api_authorization(monkeypatch, auth, host, bound_host, status):
    monkeypatch.setattr(debug_server, "load_debug_state", lambda: {"value": 42})
    handler = _make_handler(auth=auth, host=host, bound_host=bound_host)
    handler.do_GET()
    assert handler.recorded_status == status
    if status == 200:
        assert _body(handler)["data"] == {"value": 42}


@pytest.mark.parametrize("path", ["/", "/debug"])
@pytest.mark.parametrize("bound_host, host, status, injected", [
    ("127.0.0.1", "127.0.0.1:8765", 200, True),
    ("0.0.0.0", "other.example:8765", 200, False),
    ("127.0.0.1", "other.example:8765", 403, False),
])
def test_index_token_delivery(path, bound_host, host, status, injected):
    handler = _make_handler(path=path, auth=None, bound_host=bound_host, host=host)
    handler.do_GET()
    assert handler.recorded_status == status
    assert ('content="test-token"' in handler.wfile.getvalue().decode()) == injected


def test_static_assets_do_not_require_token():
    handler = _make_handler(auth=None, path="/debug-assets/styles.css")
    handler.do_GET()
    assert handler.recorded_status == 200


@pytest.mark.parametrize("error, status, code, public_cause", [
    (ClientValidationError("user_input is required"), 400, "validation_error", True),
    (RuntimeError("private story secret-key"), 500, "internal_error", False),
])
def test_player_json_error_feedback(error, status, code, public_cause):
    handler = _make_handler(handler_class=PlayerHandler)
    def explode():
        raise error
    handler._handle_player_json(explode)
    assert handler.recorded_status == status
    feedback = _body(handler)["error"]
    assert feedback["code"] == code
    assert feedback["message"]
    if public_cause:
        assert feedback["message"] == str(error)
    else:
        assert "secret-key" not in feedback["message"]
        assert "private story" not in feedback["message"]


@pytest.mark.parametrize("error_type, message, code, public_cause", [
    ("ClientValidationError", "Pending turn not found.", "validation_error", True),
    ("auth_failed", "credential rejected", "auth_failed", True),
    ("provider_error", None, "provider_error", False),
    ("timeout", "scene_facts", "timeout", False),
    ("LLMProviderError", "private story secret-key", "operation_failed", False),
])
def test_operation_failure_feedback(error_type, message, code, public_cause):
    error = SimpleNamespace(error_type=error_type, error_message=message, operation_type="confirmation")
    actual_code, feedback = PlayerHandler._project_operation_failure(error)
    assert actual_code == code
    assert feedback
    if public_cause:
        assert feedback == message
    else:
        assert "secret-key" not in feedback
        assert "private story" not in feedback


@pytest.mark.parametrize("missing", [True, False], ids=["missing-operation", "stream-failure"])
def test_operation_stream_reports_failure(monkeypatch, missing):
    handler = _make_handler(handler_class=PlayerHandler)
    class Operations:
        def get(self, operation_id):
            return None if missing else SimpleNamespace(operation_id=operation_id, operation_type="generation")
        def read_events(self, *_args, **_kwargs):
            raise RuntimeError("private story secret-key")
    monkeypatch.setattr(player_server, "get_campaign_runtime", lambda: SimpleNamespace(operations=Operations()))
    handler._handle_operation_event_stream(
        "op", urllib.parse.urlparse("/api/player/operations/op/events"), player=True, resumed=True,
    )
    payload = handler.wfile.getvalue().decode()
    assert "validation_error" in payload if missing else "internal_error" in payload
    assert "secret-key" not in payload
    assert "private story" not in payload


@pytest.mark.parametrize("host, allow_external, accepted", [
    ("127.0.0.1", False, True), ("0.0.0.0", False, False), ("0.0.0.0", True, True),
])
def test_server_bind_configuration(monkeypatch, host, allow_external, accepted):
    served = []
    class FakeServer:
        def __init__(self, address, handler_class):
            self.address = address
        def serve_forever(self):
            served.append(self.address)
    args = ["app.debug_server", "--host", host, "--port", "0"]
    if allow_external:
        args.append("--allow-external")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(debug_server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(debug_server.DebugHandler, "session_token", "")
    if accepted:
        debug_server.main()
        assert served == [(host, 0)]
    else:
        with pytest.raises(SystemExit) as error:
            debug_server.main()
        assert error.value.code != 0
        assert served == []


@pytest.mark.parametrize("kind", ["not_zip", "missing_files", "new_version", "permission", "install"])
def test_save_import_reports_real_reason_without_installing_save(campaign_runtime, tmp_path, monkeypatch, kind):
    import zipfile
    from app.runtime import saves
    from app.runtime.session import get_campaign_runtime

    manager = saves.SaveManager(tmp_path / "saves", runtime=get_campaign_runtime())
    monkeypatch.setattr(saves, "_get_save_manager", lambda: manager)
    before = {item.save_id for item in manager.list()}
    body = io.BytesIO()
    if kind == "not_zip":
        body.write(b"not zip")
    elif kind == "new_version":
        import sqlite3
        archive = tmp_path / "future.zip"
        manager.export_to(manager.active_id, archive)
        with zipfile.ZipFile(archive) as original, zipfile.ZipFile(body, "w") as bundle:
            for info in original.infolist():
                content = original.read(info)
                if info.filename == "save/campaign.db":
                    database = tmp_path / "future.db"
                    database.write_bytes(content)
                    with sqlite3.connect(database) as conn:
                        conn.execute("UPDATE schema_versions SET version=999 WHERE component='current_state'")
                    content = database.read_bytes()
                bundle.writestr(info, content)
    else:
        with zipfile.ZipFile(body, "w"):
            pass
    if kind == "install":
        original_replace = saves.os.replace
        def reject_install(source, destination):
            if Path(source).name.startswith(".import-"):
                raise PermissionError("import installation denied")
            return original_replace(source, destination)
        archive = tmp_path / "valid.zip"
        manager.export_to(manager.active_id, archive)
        body = io.BytesIO(archive.read_bytes())
        monkeypatch.setattr(saves.os, "replace", reject_install)
    if kind == "permission":
        def denied(*args, **kwargs):
            raise PermissionError("staging cannot be written")
        monkeypatch.setattr(saves, "extract_archive", denied)
    raw = body.getvalue()
    handler = _make_handler()
    handler.headers.update({"Content-Type": "application/zip", "Content-Length": str(len(raw))})
    handler.rfile = io.BytesIO(raw)
    handler._handle_save_import("导入测试")
    error = _body(handler)["error"]
    assert handler.recorded_status == (500 if kind in {"permission", "install"} else 400)
    assert error["code"] == ("file_access_failed" if kind in {"permission", "install"} else "validation_error")
    assert error["message"]
    assert {item.save_id for item in manager.list()} == before
    assert not list(manager.root.glob(".import-*"))


def test_duplicate_save_name_is_a_business_error(campaign_runtime, tmp_path, monkeypatch):
    from app.runtime import saves
    from app.runtime.session import get_campaign_runtime

    manager = saves.SaveManager(tmp_path / "saves", runtime=get_campaign_runtime())
    monkeypatch.setattr(saves, "_get_save_manager", lambda: manager)
    manager.create("重复名称")
    handler = _make_handler()
    handler._handle_player_json(lambda: saves.create_save({"name": "重复名称"}))
    assert handler.recorded_status == 400
    assert "重复名称" in _body(handler)["error"]["message"]


@pytest.mark.parametrize("body", [b"invalid-json", b"[]", b"null", b"\xff"])
def test_malformed_json_is_explained_as_request_error(body):
    handler = _make_handler(path="/api/player/saves/create")
    handler.headers.update({"Content-Length": str(len(body)), "Content-Type": "application/json"})
    handler.rfile = io.BytesIO(body)
    handler.do_POST()
    assert handler.recorded_status == 400
    assert _body(handler)["error"]["code"] == "validation_error"
    assert "JSON" in _body(handler)["error"]["message"]
