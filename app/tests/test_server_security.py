import io
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from app import debug_server
from app.player_server import PlayerHandler


def _make_handler(
    *,
    token="test-token",
    auth="test-token",
    host="127.0.0.1:8765",
    bound_host="127.0.0.1",
    path="/api/state",
):
    handler = object.__new__(debug_server.DebugHandler)
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


def test_api_without_token_is_rejected():
    handler = _make_handler(auth=None)
    handler.do_GET()
    assert handler.recorded_status == 403


def test_api_with_wrong_token_is_rejected():
    handler = _make_handler(auth="wrong-token")
    handler.do_GET()
    assert handler.recorded_status == 403


def test_api_with_correct_token_is_allowed(monkeypatch):
    monkeypatch.setattr(debug_server, "load_debug_state", lambda: {"ok": True})
    handler = _make_handler()
    handler.do_GET()
    assert handler.recorded_status == 200
    assert _body(handler) == {"ok": True, "data": {"ok": True}}


def test_loopback_bind_rejects_wrong_host_even_with_token():
    handler = _make_handler(host="evil.example:8765")
    handler.do_GET()
    assert handler.recorded_status == 403


def test_external_bind_skips_host_check(monkeypatch):
    monkeypatch.setattr(debug_server, "load_debug_state", lambda: {"ok": True})
    handler = _make_handler(
        bound_host="0.0.0.0",
        host="evil.example:8765",
    )
    handler.do_GET()
    assert handler.recorded_status == 200


def test_static_assets_do_not_require_token():
    handler = _make_handler(auth=None, path="/debug-assets/styles.css")
    handler.do_GET()
    assert handler.recorded_status == 200


def test_player_static_root_does_not_require_token():
    handler = _make_handler(auth=None, path="/")
    handler.do_GET()
    assert handler.recorded_status == 200


def test_player_root_injects_token_on_loopback():
    handler = _make_handler(path="/")
    handler.do_GET()
    assert handler.recorded_status == 200
    body = handler.wfile.getvalue().decode("utf-8")
    assert 'content="test-token"' in body


def test_player_root_does_not_inject_token_external():
    handler = _make_handler(path="/", bound_host="0.0.0.0")
    handler.do_GET()
    assert handler.recorded_status == 200
    body = handler.wfile.getvalue().decode("utf-8")
    assert 'content="test-token"' not in body
    assert 'content=""' in body


def test_player_root_rejects_wrong_host_on_loopback():
    handler = _make_handler(path="/", host="evil.example:8765")
    handler.do_GET()
    assert handler.recorded_status == 403
    body = handler.wfile.getvalue().decode("utf-8")
    assert 'content="test-token"' not in body


def test_debug_index_injects_token_on_loopback():
    handler = _make_handler(path="/debug")
    handler.do_GET()
    assert handler.recorded_status == 200
    body = handler.wfile.getvalue().decode("utf-8")
    assert 'content="test-token"' in body


def test_debug_index_does_not_inject_token_external():
    handler = _make_handler(path="/debug", bound_host="0.0.0.0")
    handler.do_GET()
    assert handler.recorded_status == 200
    body = handler.wfile.getvalue().decode("utf-8")
    assert 'content="test-token"' not in body
    assert 'content=""' in body


def test_debug_index_rejects_wrong_host_on_loopback():
    handler = _make_handler(path="/debug", host="evil.example:8765")
    handler.do_GET()
    assert handler.recorded_status == 403
    body = handler.wfile.getvalue().decode("utf-8")
    assert 'content="test-token"' not in body


def test_player_json_shows_validation_message():
    from app.core.client_error import ClientValidationError

    handler = object.__new__(PlayerHandler)
    handler.wfile = io.BytesIO()
    handler.recorded_status = None
    handler.send_response = lambda status, message=None: setattr(
        handler, "recorded_status", status
    )
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None

    def explode():
        raise ClientValidationError("user_input is required")

    handler._handle_player_json(explode)
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.recorded_status == 400
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["message"] == "user_input is required"
    assert "recoverable" not in payload["error"]


def test_player_json_genericizes_internal_error():
    handler = object.__new__(PlayerHandler)
    handler.wfile = io.BytesIO()
    handler.recorded_status = None
    handler.send_response = lambda status, message=None: setattr(
        handler, "recorded_status", status
    )
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None

    def explode():
        raise RuntimeError("boom /home/secret/app.py")

    handler._handle_player_json(explode)
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.recorded_status == 500
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["message"].startswith("本地操作未能完成，请复制诊断信息以便排查。")
    assert "/home" not in json.dumps(payload, ensure_ascii=False)


def test_save_import_validation_returns_400_validation_error():
    handler = object.__new__(PlayerHandler)
    handler.headers = {"Content-Type": "text/plain"}
    handler.wfile = io.BytesIO()
    handler.recorded_status = None
    handler.send_response = lambda status, message=None: setattr(
        handler, "recorded_status", status
    )
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None

    handler._handle_save_import("new-save")
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.recorded_status == 400
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["message"] == "Import body must be a ZIP archive"
    assert "recoverable" not in payload["error"]


def test_client_error_projection_genericizes_save_archive_error():
    from app.player_server import _project_client_error
    from app.runtime.save_archive import SaveArchiveError

    status, code, message = _project_client_error(
        SaveArchiveError(
            "Save directory is not a regular directory: "
            "/home/secret/.loreloop/campaigns/c-1"
        ),
        label="save import",
    )
    assert status == 500
    assert code == "internal_error"
    assert message.startswith("本地操作未能完成，请复制诊断信息以便排查。")


def test_operation_failure_projection_shows_validation_only():
    from app.player_server import PlayerHandler

    validation = SimpleNamespace(
        error_type="ClientValidationError",
        error_message="Pending turn not found.",
    )
    internal = SimpleNamespace(
        error_type="RuntimeError",
        error_message="boom /home/secret/app.py",
    )
    assert PlayerHandler._project_operation_failure(validation) == (
        "validation_error",
        "Pending turn not found.",
    )
    assert PlayerHandler._project_operation_failure(internal) == (
        "operation_failed",
        "本地操作未能完成，请复制诊断信息以便排查。",
    )


def test_operation_failure_projection_surfaces_classified_provider_error():
    from app.player_server import PlayerHandler

    provider = SimpleNamespace(
        error_type="auth_failed",
        error_message="API Key 无效或没有访问权限",
    )
    assert PlayerHandler._project_operation_failure(provider) == (
        "auth_failed",
        "API Key 无效或没有访问权限",
    )

    generic = SimpleNamespace(
        error_type="provider_error",
        error_message=None,
    )
    assert PlayerHandler._project_operation_failure(generic) == (
        "provider_error",
        "模型服务暂时不可用，请检查后重试。",
    )


def test_operation_failure_projection_maps_timeout_to_operation_label():
    from app.core.llm import FORMAL_LLM_CALL_TIMEOUT_SECONDS
    from app.player_server import PlayerHandler

    seconds = int(FORMAL_LLM_CALL_TIMEOUT_SECONDS)
    confirmation = SimpleNamespace(
        error_type="timeout",
        error_message="模型请求超过120秒，已停止等待（任务：scene_facts）。",
        operation_type="confirmation",
    )
    assert PlayerHandler._project_operation_failure(confirmation) == (
        "timeout",
        f"确认回合的模型请求超过{seconds}秒，已停止等待。",
    )

    unknown = SimpleNamespace(
        error_type="timeout",
        error_message="timeout",
        operation_type="background_job",
    )
    assert PlayerHandler._project_operation_failure(unknown) == (
        "timeout",
        f"模型请求超过{seconds}秒，已停止等待。",
    )


def test_operation_failure_projection_never_surfaces_raw_provider_text():
    from app.player_server import PlayerHandler

    raw = SimpleNamespace(
        error_type="LLMProviderError",
        error_message="Authentication failed for secret-key /api/v1",
    )
    assert PlayerHandler._project_operation_failure(raw) == (
        "operation_failed",
        "本地操作未能完成，请复制诊断信息以便排查。",
    )


def test_operation_stream_missing_operation_returns_validation_error(monkeypatch):
    import urllib.parse

    from app import player_server

    handler = object.__new__(PlayerHandler)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda _status, _message=None: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None

    class EmptyOperations:
        def get(self, operation_id):
            return None

    monkeypatch.setattr(
        player_server,
        "get_campaign_runtime",
        lambda: SimpleNamespace(operations=EmptyOperations()),
    )
    handler._handle_operation_event_stream(
        "missing-op",
        urllib.parse.urlparse("/api/player/operations/missing-op/events"),
        player=True,
        resumed=True,
    )

    payload = handler.wfile.getvalue().decode("utf-8")
    assert "validation_error" in payload
    assert "Operation not found" in payload
    assert "recoverable" not in payload


def test_operation_stream_exception_does_not_leak(monkeypatch):
    import urllib.parse

    from app import player_server

    handler = object.__new__(PlayerHandler)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda _status, _message=None: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None

    class ExplodingOperations:
        def get(self, operation_id):
            return SimpleNamespace(
                operation_id=operation_id,
                operation_type="generation",
            )

        def read_events(self, *_args, **_kwargs):
            raise RuntimeError("boom /home/secret/app.py")

    monkeypatch.setattr(
        player_server,
        "get_campaign_runtime",
        lambda: SimpleNamespace(operations=ExplodingOperations()),
    )
    handler._handle_operation_event_stream(
        "op-1",
        urllib.parse.urlparse("/api/operations/op-1/events"),
        player=False,
        resumed=False,
    )

    payload = handler.wfile.getvalue().decode("utf-8")
    assert "boom" not in payload
    assert "/home" not in payload
    assert "traceback" not in payload
    assert "本地操作未能完成，请复制诊断信息以便排查。" in payload


def test_error_response_does_not_leak_traceback_or_path(monkeypatch):
    def explode():
        raise RuntimeError("boom /home/secret/app.py")

    monkeypatch.setattr(debug_server, "load_debug_state", explode)
    handler = _make_handler()
    handler.do_GET()
    assert handler.recorded_status == 500
    payload = _body(handler)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["message"].startswith("本地操作未能完成，请复制诊断信息以便排查。")
    assert "traceback" not in payload
    assert "/home" not in json.dumps(payload, ensure_ascii=False)


def test_operation_start_error_does_not_leak_traceback():
    handler = object.__new__(debug_server.DebugHandler)
    handler.send_response = lambda _status, _message=None: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None
    handler.wfile = io.BytesIO()
    handler.headers = {}

    handler._write_operation_start_error(
        RuntimeError("boom /home/secret/app.py"),
        player=False,
    )

    payload = handler.wfile.getvalue().decode("utf-8")
    assert "traceback" not in payload
    assert "/home" not in payload
    assert "本地操作未能完成，请复制诊断信息以便排查。" in payload


def test_main_rejects_external_bind_without_flag(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["app.debug_server", "--host", "0.0.0.0", "--port", "0"],
    )
    with pytest.raises(SystemExit) as excinfo:
        debug_server.main()
    assert excinfo.value.code != 0


def test_main_allows_external_bind_with_flag(monkeypatch):
    served = []

    class FakeServer:
        def __init__(self, address, handler_class):
            self.address = address
            self.handler_class = handler_class

        def serve_forever(self):
            served.append(True)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "app.debug_server",
            "--host",
            "0.0.0.0",
            "--port",
            "0",
            "--allow-external",
        ],
    )
    monkeypatch.setattr(debug_server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(
        debug_server,
        "secrets",
        SimpleNamespace(token_hex=lambda n: "a" * (2 * n)),
    )
    try:
        debug_server.main()
        assert served == [True]
        assert debug_server.DebugHandler.session_token == "a" * 64
    finally:
        debug_server.DebugHandler.session_token = ""


def test_main_allows_loopback_bind_without_flag(monkeypatch):
    served = []

    class FakeServer:
        def __init__(self, address, handler_class):
            self.address = address
            self.handler_class = handler_class

        def serve_forever(self):
            served.append(True)

    monkeypatch.setattr(sys, "argv", ["app.debug_server"])
    monkeypatch.setattr(debug_server, "ThreadingHTTPServer", FakeServer)
    debug_server.main()
    assert served == [True]


def test_player_handler_raw_base_remains_unauthenticated():
    handler = object.__new__(PlayerHandler)
    handler.path = "/api/player/state"
    handler.headers = {}
    results = []
    handler._handle_player_json = lambda callback: results.append(callback())
    handler.do_GET()
    assert results != []


def test_player_index_serves_html():
    handler = object.__new__(PlayerHandler)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status, message=None: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    handler._serve_player_index(inject_token=False)
    content = handler.wfile.getvalue().decode("utf-8")
    assert "<!doctype html>" in content
    assert '<meta name="loreloop-session-token"' in content


@pytest.mark.parametrize("kind", ["not_zip", "missing_files", "new_version", "permission", "install"])
def test_save_import_reports_real_reason_without_installing_save(tmp_path, monkeypatch, kind):
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
    assert "权限" in error["message"] if kind in {"permission", "install"} else "存档校验失败" in error["message"]
    assert {item.save_id for item in manager.list()} == before
    assert not list(manager.root.glob(".import-*"))


def test_duplicate_save_name_is_a_business_error(tmp_path, monkeypatch):
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
