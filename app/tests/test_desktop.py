import http.client
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from app.core.crash_logging import write_desktop_crash
from app.desktop import WINFORMS_RUNTIME_CHECK, WINDOW_TITLE, run_desktop
from app.player_server import make_player_handler
from app.runtime.app_paths import resolve_application_paths
from app.runtime.single_instance import AlreadyRunningError, SingleInstanceLock


def test_player_handler_exposes_only_authenticated_player_surface():
    token = "desktop-test-token"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_player_handler(token),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    port = server.server_address[1]

    def request(path, *, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    try:
        status, body = request("/")
        assert status == 200
        assert b'loreloop-session-token" content="desktop-test-token' in body

        assert request("/api/player/model-config")[0] == 403
        assert request(
            "/api/player/model-config",
            headers={"X-LoreLoop-Token": token},
        )[0] == 200
        assert request(
            "/debug",
            headers={"X-LoreLoop-Token": token},
        )[0] == 404
        assert request(
            "/api/state",
            headers={"X-LoreLoop-Token": token},
        )[0] == 404

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.putrequest("GET", "/", skip_host=True)
        connection.putheader("Host", "localhost")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_player_model_configs_delete_requires_token():
    token = "desktop-test-token"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_player_handler(token),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    port = server.server_address[1]

    def delete(path, *, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("DELETE", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    try:
        status, _ = delete("/api/player/model-configs/abc")
        assert status == 403
        status, body = delete(
            "/api/player/model-configs/abc",
            headers={"X-LoreLoop-Token": token},
        )
        # Authenticated but unknown config_id -> client validation error.
        assert status == 400
        assert b'"ok": false' in body
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_winforms_runtime_check_does_not_start_the_desktop(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.desktop._check_winforms_runtime",
        lambda: calls.append("checked"),
    )
    monkeypatch.setattr(
        "app.desktop.run_desktop",
        lambda *_args: pytest.fail("desktop must not start during runtime check"),
    )

    from app import desktop

    assert desktop.main([WINFORMS_RUNTIME_CHECK]) == 0
    assert calls == ["checked"]


def test_winforms_runtime_check_returns_failure_without_showing_ui(monkeypatch):
    monkeypatch.setattr(
        "app.desktop._check_winforms_runtime",
        lambda: (_ for _ in ()).throw(RuntimeError("broken CLR")),
    )
    monkeypatch.setattr(
        "app.desktop._show_message",
        lambda *_args, **_kwargs: pytest.fail("self-test must not show a dialog"),
    )

    from app import desktop

    assert desktop.main([WINFORMS_RUNTIME_CHECK]) == 1


def test_single_instance_lock_rejects_second_owner_and_can_be_reacquired(tmp_path):
    path = tmp_path / "config" / "desktop.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_player_handler_rejects_empty_desktop_session_token():
    with pytest.raises(ValueError, match="session token cannot be empty"):
        make_player_handler("")


def test_crash_log_redacts_environment_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value-for-test")
    error = RuntimeError("provider rejected secret-value-for-test")

    path = write_desktop_crash(
        error,
        phase="test",
        logs_dir=tmp_path,
    )

    content = path.read_text(encoding="utf-8")
    assert "phase=test" in content
    assert "app_version=0.1.0" in content
    assert "RuntimeError" in content
    assert "secret-value-for-test" not in content
    assert "[REDACTED]" in content


def test_run_desktop_uses_random_loopback_server_and_cleans_up(tmp_path):
    paths = resolve_application_paths(
        {"LORELOOP_DATA_DIR": str(tmp_path / "user-data")}
    )
    calls = {}

    class FakeWebview:
        token = "webview-session-token"
        settings = {}

        @staticmethod
        def create_window(title, url, **options):
            calls["window"] = (title, url, options)

        @staticmethod
        def start(**options):
            calls["start"] = options

    class FakeRuntime:
        def begin_shutdown(self):
            calls["cancelled"] = True
            return 0

        def close(self, *, wait_for_jobs):
            calls["closed"] = wait_for_jobs

    run_desktop(
        paths,
        webview_module=FakeWebview,
        handler_factory=lambda token: make_player_handler(token),
        runtime_factory=FakeRuntime,
    )

    title, url, window_options = calls["window"]
    assert title == WINDOW_TITLE
    assert url.startswith("http://127.0.0.1:")
    assert not url.startswith("http://127.0.0.1:8765")
    assert window_options["min_size"] == (900, 640)
    assert calls["start"]["debug"] is False
    assert calls["start"]["private_mode"] is False
    assert calls["start"]["storage_path"] == str(paths.webview_dir)
    assert FakeWebview.settings["ALLOW_DOWNLOADS"] is True
    assert calls["cancelled"] is True
    assert calls["closed"] is True


def test_run_desktop_cleans_up_when_webview_fails(tmp_path):
    paths = SimpleNamespace(webview_dir=tmp_path / "webview")
    calls = []

    class BrokenWebview:
        token = "webview-session-token"
        settings = {}

        @staticmethod
        def create_window(*args, **kwargs):
            return None

        @staticmethod
        def start(**options):
            raise RuntimeError("GUI failed")

    class FakeRuntime:
        def begin_shutdown(self):
            calls.append("cancel")
            return 0

        def close(self, *, wait_for_jobs):
            calls.append(("close", wait_for_jobs))

    with pytest.raises(RuntimeError, match="GUI failed"):
        run_desktop(
            paths,
            webview_module=BrokenWebview,
            handler_factory=lambda token: make_player_handler(token),
            runtime_factory=FakeRuntime,
        )

    assert calls == ["cancel", ("close", True)]
