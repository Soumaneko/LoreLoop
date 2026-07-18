from __future__ import annotations

import json
import mimetypes
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.llm import TraceLogger
from app.api import (
    apply_core_memory_init,
    batch_classify_stream,
    confirm_stream,
    create_save,
    discard_pending_turn,
    generate_turn_stream,
    get_maintenance_job,
    list_maintenance_jobs,
    list_saves,
    load_debug_state,
    load_player_history,
    load_player_state,
    memory_maintain_stream,
    preview_core_memory,
    redo_last_turn,
    retry_turn_stream,
    save_difficulty,
    save_max_chars,
    switch_save,
    undo_last_turn,
)
from app.runtime.session import get_campaign_runtime
from app.runtime.player_state import player_saves_payload

# python -m app.debug_server

DEBUG_STATIC_DIR = Path(__file__).with_name("debug_static")
PLAYER_STATIC_DIR = Path(__file__).with_name("player_static")


def _json_response(handler: BaseHTTPRequestHandler, status: int, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw)


class DebugHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/":
            self._static_file(PLAYER_STATIC_DIR / "index.html", PLAYER_STATIC_DIR)
            return
        if path.startswith("/assets/"):
            self._static_file(
                PLAYER_STATIC_DIR / path.removeprefix("/assets/"),
                PLAYER_STATIC_DIR,
            )
            return
        if path in {"/debug", "/debug/"}:
            self._static_file(DEBUG_STATIC_DIR / "index.html", DEBUG_STATIC_DIR)
            return
        if path.startswith("/debug-assets/"):
            self._static_file(
                DEBUG_STATIC_DIR / path.removeprefix("/debug-assets/"),
                DEBUG_STATIC_DIR,
            )
            return
        if path == "/api/player/state":
            self._handle_player_json(load_player_state)
            return
        if path == "/api/player/history":
            query = parse_qs(parsed_url.query)
            before = query.get("before_turn", [None])[0]
            limit = query.get("limit", ["5"])[0]
            self._handle_player_json(
                lambda: load_player_history(
                    before_turn_index=int(before) if before is not None else None,
                    limit=int(limit),
                )
            )
            return
        if path == "/api/player/saves":
            self._handle_player_json(lambda: player_saves_payload(list_saves()))
            return
        if path == "/api/state":
            self._handle_json(load_debug_state)
            return
        if path == "/api/saves":
            self._handle_json(list_saves)
            return
        if path == "/api/traces":
            self._handle_json(lambda: self._load_traces())
            return
        if path == "/api/maintenance-jobs":
            self._handle_json(self._load_maintenance_jobs)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/player/generate":
            self._handle_player_json_stream(self._generate_stream)
            return
        if path == "/api/player/confirm":
            self._handle_player_json_stream(self._player_confirm_stream)
            return
        if path == "/api/player/retry":
            self._handle_player_json_stream(self._retry_stream)
            return
        if path == "/api/player/pending/discard":
            self._handle_player_json(lambda: self._discard_pending_turn())
            return
        if path == "/api/player/init":
            self._handle_player_json(self._player_init)
            return
        if path == "/api/player/difficulty":
            self._handle_player_json(lambda: save_difficulty(_read_json(self)))
            return
        if path == "/api/player/saves/create":
            self._handle_player_json(
                lambda: player_saves_payload(create_save(_read_json(self)))
            )
            return
        if path == "/api/player/saves/switch":
            self._handle_player_json(
                lambda: player_saves_payload(switch_save(_read_json(self)))
            )
            return
        if path == "/api/player/history/undo":
            self._handle_player_json(undo_last_turn)
            return
        if path == "/api/player/history/redo":
            self._handle_player_json(redo_last_turn)
            return
        if path == "/api/generate":
            self._handle_json_stream(self._generate_stream)
            return
        if path == "/api/confirm":
            self._handle_json_stream(self._confirm_stream)
            return
        if path == "/api/retry":
            self._handle_json_stream(self._retry_stream)
            return
        if path == "/api/pending/discard":
            self._handle_json(lambda: self._discard_pending_turn())
            return
        if path == "/api/batch-classify":
            self._handle_json_stream(self._batch_classify_stream)
            return
        if path == "/api/memory-maintain":
            self._handle_json_stream(self._memory_maintain_stream)
            return
        if path == "/api/init/preview":
            self._handle_json(lambda: preview_core_memory(_read_json(self)))
            return
        if path == "/api/init/apply":
            self._handle_json(lambda: apply_core_memory_init(_read_json(self)))
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
        self.send_error(404)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[debug-server] {self.address_string()} - {format % args}")

    def _static_file(self, path: Path, static_root: Path) -> None:
        try:
            resolved = path.resolve()
            root = static_root.resolve()
            if root not in resolved.parents and resolved != root:
                self.send_error(403)
                return
            if not resolved.is_file():
                self.send_error(404)
                return
            payload = resolved.read_bytes()
            content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            if resolved.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            elif resolved.suffix in {".html", ".css"}:
                content_type = f"{content_type}; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except Exception:
            self.send_error(500)

    def _handle_player_json(self, fn) -> None:
        try:
            _json_response(self, 200, {"ok": True, "data": fn()})
        except Exception as exc:
            _json_response(
                self,
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "request_failed",
                        "message": str(exc) or "请求没有完成，请稍后重试。",
                        "recoverable": True,
                    },
                },
            )

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

    def _handle_json_stream(self, generator_factory) -> None:
        try:
            generator = generator_factory()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            for event in generator:
                payload = json.dumps({"ok": True, **event}, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            done_payload = json.dumps({"ok": True, "step": "done"}, ensure_ascii=False)
            self.wfile.write(f"data: {done_payload}\n\n".encode("utf-8"))
            self.wfile.flush()
            self.close_connection = True
        except Exception as exc:
            err_payload = json.dumps(
                {"ok": False, "error": str(exc), "traceback": traceback.format_exc()},
                ensure_ascii=False,
            )
            try:
                self.wfile.write(f"data: {err_payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
            self.close_connection = True

    @staticmethod
    def _player_event(event: dict[str, Any]) -> dict[str, Any]:
        step = str(event.get("step", "progress"))
        data = event.get("data") or {}
        if step == "scene_text_delta":
            public_data = {"delta": data.get("delta", "")}
        elif step == "scene_stream_started":
            public_data = {
                "attempt": data.get("attempt", 1),
                "version_index": data.get("version_index"),
            }
        elif step == "retry_started":
            public_data = {"version_index": data.get("version_index")}
        elif step == "scene_written":
            scene_result = data.get("scene_result") or {}
            public_data = {
                "turn_id": data.get("turn_id"),
                "group_id": data.get("group_id"),
                "version_index": data.get("version_index"),
                "turn_index": data.get("turn_index"),
                "scene_text": scene_result.get("scene_text", ""),
                "closing_prompt": scene_result.get("closing_prompt", ""),
            }
        elif step == "finish":
            public_data = {
                "turn_id": data.get("turn_id"),
                "status": data.get("status"),
                "action_preview": data.get("action_preview"),
            }
        else:
            public_data = {}
        return {"step": step, "data": public_data}

    def _handle_player_json_stream(self, generator_factory) -> None:
        try:
            generator = generator_factory()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            for event in generator:
                payload = json.dumps(
                    {"ok": True, **self._player_event(event)},
                    ensure_ascii=False,
                )
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            done_payload = json.dumps(
                {"ok": True, "step": "done"}, ensure_ascii=False
            )
            self.wfile.write(f"data: {done_payload}\n\n".encode("utf-8"))
            self.wfile.flush()
            self.close_connection = True
        except Exception as exc:
            error = {
                "ok": False,
                "error": {
                    "code": "stream_failed",
                    "message": str(exc) or "本次操作没有完成，请重新尝试。",
                    "recoverable": True,
                },
            }
            try:
                self.wfile.write(
                    f"data: {json.dumps(error, ensure_ascii=False)}\n\n".encode(
                        "utf-8"
                    )
                )
                self.wfile.flush()
            except Exception:
                pass
            self.close_connection = True

    def _load_traces(self) -> dict[str, Any]:
        params = parse_qs(urlparse(self.path).query)
        trace_id = (params.get("trace_id") or [""])[0]
        if not trace_id:
            raise ValueError("trace_id is required")
        traces = TraceLogger().read_traces(trace_id=trace_id)
        return {"trace_id": trace_id, "traces": traces}

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

    def _generate_stream(self):
        payload = _read_json(self)
        user_input = str(payload.get("user_input", "")).strip()
        if not user_input:
            raise ValueError("user_input is required")
        difficulty = str(payload.get("difficulty", "困难")).strip()
        if difficulty not in ("简单", "普通", "困难"):
            difficulty = "困难"
        try:
            max_chars = int(payload.get("max_chars", 1500))
        except (TypeError, ValueError):
            max_chars = 1500
        max_chars = max(100, min(5000, max_chars))
        yield from generate_turn_stream(user_input, difficulty=difficulty, max_chars=max_chars)

    def _confirm_stream(self):
        payload = _read_json(self)
        turn_id = str(payload.get("turn_id", "")).strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        write_db = bool(payload.get("write_db", True))
        scene_text = payload.get("scene_text")
        if scene_text is not None:
            scene_text = str(scene_text).strip()
        yield from confirm_stream(
            turn_id,
            write_db=write_db,
            scene_text=scene_text,
        )

    def _player_confirm_stream(self):
        payload = _read_json(self)
        turn_id = str(payload.get("turn_id", "")).strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        scene_text = payload.get("scene_text")
        if scene_text is not None:
            scene_text = str(scene_text).strip()
        yield from confirm_stream(turn_id, write_db=True, scene_text=scene_text)

    def _player_init(self):
        result = apply_core_memory_init(_read_json(self))
        return {
            "turn_id": result["turn_id"],
            "opening_scene": result["opening_scene"],
            "status": result["status"],
        }

    def _retry_stream(self):
        payload = _read_json(self)
        turn_id = str(payload.get("turn_id", "")).strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        yield from retry_turn_stream(turn_id)

    def _discard_pending_turn(self):
        payload = _read_json(self)
        turn_id = str(payload.get("turn_id", "")).strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        return discard_pending_turn(turn_id)

    def _batch_classify_stream(self):
        yield from batch_classify_stream()

    def _memory_maintain_stream(self):
        yield from memory_maintain_stream()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run TVG Memory.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DebugHandler)
    print(f"TVG Memory: http://{args.host}:{args.port}")
    print(f"Debug Console: http://{args.host}:{args.port}/debug")
    server.serve_forever()


if __name__ == "__main__":
    main()
