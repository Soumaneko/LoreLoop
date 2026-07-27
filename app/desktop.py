from __future__ import annotations

import sys
import threading
from http.server import ThreadingHTTPServer
from typing import Any, Callable

from app.core.crash_logging import write_desktop_crash
from app.runtime.single_instance import AlreadyRunningError, SingleInstanceLock


WINDOW_TITLE = "续章 · LoreLoop"


class DesktopHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False


def run_desktop(
    paths,
    *,
    webview_module=None,
    handler_factory: Callable[[str], type] | None = None,
    runtime_factory: Callable[[], Any] | None = None,
    server_class: type[ThreadingHTTPServer] = DesktopHTTPServer,
) -> None:
    """Run the webview event loop and deterministically release local services."""
    if webview_module is None:
        import webview as webview_module
    if handler_factory is None:
        from app.player_server import make_player_handler

        handler_factory = make_player_handler
    if runtime_factory is None:
        from app.runtime.session import get_campaign_runtime

        runtime_factory = get_campaign_runtime

    runtime = runtime_factory()
    server = None
    server_thread = None
    try:
        token = str(webview_module.token)
        server = server_class(("127.0.0.1", 0), handler_factory(token))
        server_thread = threading.Thread(
            target=server.serve_forever,
            name="loreloop-http",
        )
        port = int(server.server_address[1])
        url = f"http://127.0.0.1:{port}/"

        webview_module.settings["ALLOW_DOWNLOADS"] = True
        webview_module.create_window(
            WINDOW_TITLE,
            url,
            width=1200,
            height=820,
            min_size=(900, 640),
            background_color="#11110e",
        )
        server_thread.start()
        start_options = {
            "debug": False,
            "private_mode": False,
            "storage_path": str(paths.webview_dir),
        }
        if sys.platform == "win32":
            start_options["gui"] = "edgechromium"
        webview_module.start(**start_options)
    finally:
        active_failure = sys.exc_info()[0] is not None
        cleanup_errors = []
        try:
            runtime.begin_shutdown()
        except BaseException as error:
            cleanup_errors.append(error)
        if server is not None and server_thread is not None:
            try:
                if server_thread.is_alive():
                    server.shutdown()
                    server_thread.join()
                server.server_close()
            except BaseException as error:
                cleanup_errors.append(error)
        try:
            runtime.close(wait_for_jobs=True)
        except BaseException as error:
            cleanup_errors.append(error)
        if cleanup_errors and not active_failure:
            raise cleanup_errors[0]


def main() -> int:
    logs_dir = None
    try:
        from app.runtime.app_paths import (
            APP_PATHS,
            ensure_user_directories,
            migrate_legacy_user_data,
        )

        migrate_legacy_user_data(APP_PATHS)
        ensure_user_directories(APP_PATHS)
        logs_dir = APP_PATHS.logs_dir
        with SingleInstanceLock(APP_PATHS.config_dir / "desktop.lock"):
            run_desktop(APP_PATHS)
        return 0
    except AlreadyRunningError:
        _show_message("续章已经在运行。", error=False)
        return 0
    except BaseException as error:
        crash_path = write_desktop_crash(
            error,
            phase="desktop_startup",
            logs_dir=logs_dir,
        )
        _show_message(
            f"续章无法启动。\n\n诊断信息已写入：\n{crash_path}",
            error=True,
        )
        return 1


def _show_message(message: str, *, error: bool) -> None:
    if sys.platform == "win32":
        import ctypes

        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, flags)
        return
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
