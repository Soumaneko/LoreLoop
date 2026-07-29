from __future__ import annotations

import traceback
from http.server import ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.api import (
    acknowledge_operation,
    create_save,
    get_maintenance_job,
    get_operation,
    list_maintenance_jobs,
    list_operations,
    list_saves,
    load_debug_state,
    preview_initialization_config,
    redo_last_turn,
    save_difficulty,
    save_max_chars,
    start_batch_classify_operation,
    start_confirmation_operation,
    start_memory_maintain_operation,
    switch_save,
    undo_last_turn,
)
from app.core.llm import TraceLogger
from app.player_server import PlayerHandler, _json_response, _read_json
from app.runtime.app_paths import APP_PATHS, validate_debug_resource_paths
from app.runtime.session import get_campaign_runtime


DEBUG_STATIC_DIR = APP_PATHS.debug_static_dir
validate_debug_resource_paths(APP_PATHS)


class DebugHandler(PlayerHandler):
    """Development-only routes layered on top of the player HTTP surface."""

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path in {"/debug", "/debug/"}:
            self._static_file(DEBUG_STATIC_DIR / "index.html", DEBUG_STATIC_DIR)
            return
        if path.startswith("/debug-assets/"):
            self._static_file(
                DEBUG_STATIC_DIR / path.removeprefix("/debug-assets/"),
                DEBUG_STATIC_DIR,
            )
            return
        if path == "/api/state":
            self._handle_json(load_debug_state)
            return
        if path == "/api/saves":
            self._handle_json(list_saves)
            return
        if path == "/api/traces":
            self._handle_json(self._load_traces)
            return
        if path == "/api/maintenance-jobs":
            self._handle_json(self._load_maintenance_jobs)
            return
        if path == "/api/operations/recoverable":
            self._handle_json(
                lambda: {
                    "operations": list_operations(recoverable_only=True)
                }
            )
            return
        if path.startswith("/api/operations/"):
            self._handle_operation_get(path, parsed_url)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/operations/") and path.endswith("/ack"):
            self._handle_json(lambda: self._acknowledge_operation(path))
            return
        if path == "/api/generate":
            self._handle_operation_start_stream(self._start_generate_operation)
            return
        if path == "/api/confirm":
            self._handle_operation_start_stream(
                self._start_confirmation_operation,
            )
            return
        if path == "/api/rephrase":
            self._handle_operation_start_stream(self._start_rephrase_operation)
            return
        if path == "/api/reroll":
            self._handle_operation_start_stream(self._start_reroll_operation)
            return
        if path == "/api/pending/discard":
            self._handle_json(self._discard_pending_turn)
            return
        if path == "/api/batch-classify":
            self._handle_operation_start_stream(
                self._start_batch_classify_operation,
            )
            return
        if path == "/api/memory-maintain":
            self._handle_operation_start_stream(
                self._start_memory_maintain_operation,
            )
            return
        if path == "/api/init/preview":
            self._handle_json(
                lambda: preview_initialization_config(_read_json(self))
            )
            return
        if path == "/api/init/campaign-setup":
            self._handle_operation_start_stream(
                self._start_campaign_setup_operation,
            )
            return
        if path == "/api/init/apply":
            self._handle_operation_start_stream(
                self._start_initialization_operation,
            )
            return
        if path == "/api/difficulty":
            self._handle_json(lambda: save_difficulty(_read_json(self)))
            return
        if path == "/api/max-chars":
            self._handle_json(lambda: save_max_chars(_read_json(self)))
            return
        if path == "/api/saves/create":
            self._handle_json(lambda: create_save(_read_json(self)))
            return
        if path == "/api/saves/switch":
            self._handle_json(lambda: switch_save(_read_json(self)))
            return
        if path == "/api/history/undo":
            self._handle_json(undo_last_turn)
            return
        if path == "/api/history/redo":
            self._handle_json(redo_last_turn)
            return
        super().do_POST()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[debug-server] {self.address_string()} - {format % args}")

    def _handle_json(self, fn) -> None:
        try:
            _json_response(self, 200, {"ok": True, "data": fn()})
        except Exception as exc:
            _json_response(
                self,
                500,
                {
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )

    def _acknowledge_operation(self, path: str) -> dict[str, Any]:
        operation_id = self._operation_id_from_action_path(
            path,
            player=False,
            action="ack",
        )
        return {"operation": acknowledge_operation(operation_id)}

    def _handle_operation_get(self, path: str, parsed_url) -> None:
        parts = path.strip("/").split("/")
        if len(parts) not in {3, 4}:
            self.send_error(404)
            return
        operation_id = parts[2].strip()
        if len(parts) == 4 and parts[3] == "events":
            self._handle_operation_event_stream(
                operation_id,
                parsed_url,
                resumed=True,
            )
            return
        if len(parts) == 3:
            self._handle_json(lambda: {"operation": get_operation(operation_id)})
            return
        self.send_error(404)

    def _load_traces(self) -> dict[str, Any]:
        params = parse_qs(urlparse(self.path).query)
        trace_id = (params.get("trace_id") or [""])[0]
        if not trace_id:
            raise ValueError("trace_id is required")
        return {
            "trace_id": trace_id,
            "traces": TraceLogger().read_traces(trace_id=trace_id),
        }

    def _load_maintenance_jobs(self) -> dict[str, Any]:
        params = parse_qs(urlparse(self.path).query)
        job_id = (params.get("job_id") or [""])[0].strip()
        if job_id:
            return {"job": get_maintenance_job(job_id)}
        status = (params.get("status") or [""])[0].strip() or None
        job_type = (params.get("job_type") or [""])[0].strip() or None
        try:
            limit = max(1, min(200, int((params.get("limit") or ["100"])[0])))
        except (TypeError, ValueError):
            limit = 100
        return {
            "jobs": list_maintenance_jobs(
                status=status,
                job_type=job_type,
                limit=limit,
            ),
            "status_counts": get_campaign_runtime().jobs.status_counts(),
        }

    def _start_confirmation_operation(self) -> str:
        payload = _read_json(self)
        turn_id = str(payload.get("turn_id", "")).strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        scene_text = payload.get("scene_text")
        if scene_text is not None:
            scene_text = str(scene_text).strip()
        request_id = str(payload.get("request_id", "")).strip() or None
        return start_confirmation_operation(
            turn_id,
            write_db=bool(payload.get("write_db", True)),
            scene_text=scene_text,
            request_id=request_id,
        )

    def _start_batch_classify_operation(self) -> str:
        payload = _read_json(self)
        request_id = str(payload.get("request_id", "")).strip() or None
        return start_batch_classify_operation(request_id=request_id)

    def _start_memory_maintain_operation(self) -> str:
        payload = _read_json(self)
        request_id = str(payload.get("request_id", "")).strip() or None
        return start_memory_maintain_operation(request_id=request_id)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run LoreLoop (续章).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DebugHandler)
    print(f"LoreLoop / 续章: http://{args.host}:{args.port}")
    print(f"Debug Console: http://{args.host}:{args.port}/debug")
    server.serve_forever()


if __name__ == "__main__":
    main()
