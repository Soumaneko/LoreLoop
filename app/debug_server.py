from __future__ import annotations

import json
import mimetypes
import os
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from app.core.llm import TraceLogger
from app.api import (
    acknowledge_operation,
    cancel_operation,
    copy_save,
    create_save,
    delete_save,
    discard_pending_turn,
    export_save_archive,
    get_operation,
    get_maintenance_job,
    import_save_archive,
    list_maintenance_jobs,
    list_operations,
    list_saves,
    load_debug_state,
    load_player_history,
    load_player_state,
    model_config_status,
    preview_core_memory,
    rename_save,
    redo_last_turn,
    save_dice_display,
    save_difficulty,
    save_max_chars,
    save_model_config,
    start_batch_classify_operation,
    start_confirmation_operation,
    start_generation_operation,
    start_feedback_rewrite_operation,
    start_initialization_operation,
    start_memory_maintain_operation,
    start_rephrase_operation,
    start_reroll_operation,
    start_story_direction_operation,
    switch_save,
    test_model_connection,
    undo_last_turn,
)
from app.runtime.session import get_campaign_runtime
from app.runtime.player_state import player_saves_payload
from app.runtime.operation_types import (
    PLAYER_OPERATION_TYPES,
    operation_policy,
)

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
        if path == "/api/player/model-config":
            self._handle_player_json(model_config_status)
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
        if path == "/api/player/saves/export":
            query = parse_qs(parsed_url.query)
            self._handle_save_export(query.get("save_id", [""])[0])
            return
        if path == "/api/player/operations/recoverable":
            self._handle_player_json(self._load_recoverable_player_operations)
            return
        if path.startswith("/api/player/operations/"):
            self._handle_player_operation_get(path, parsed_url)
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
        self.send_error(404)

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path.startswith("/api/player/operations/") and path.endswith("/ack"):
            self._handle_player_json(
                lambda: self._acknowledge_player_operation(path)
            )
            return
        if path.startswith("/api/player/operations/") and path.endswith("/cancel"):
            self._handle_player_json(
                lambda: self._cancel_player_operation(path)
            )
            return
        if path.startswith("/api/operations/") and path.endswith("/ack"):
            self._handle_json(lambda: self._acknowledge_operation(path))
            return
        if path == "/api/player/generate":
            self._handle_operation_start_stream(
                self._start_generate_operation,
                player=True,
            )
            return
        if path == "/api/player/confirm":
            self._handle_operation_start_stream(
                self._start_player_confirmation_operation,
                player=True,
            )
            return
        if path == "/api/player/rephrase":
            self._handle_operation_start_stream(
                self._start_rephrase_operation,
                player=True,
            )
            return
        if path == "/api/player/reroll":
            self._handle_operation_start_stream(
                self._start_reroll_operation,
                player=True,
            )
            return
        if path == "/api/player/feedback-rewrite":
            self._handle_operation_start_stream(
                self._start_feedback_rewrite_operation,
                player=True,
            )
            return
        if path == "/api/player/pending/discard":
            self._handle_player_json(lambda: self._discard_pending_turn())
            return
        if path == "/api/player/init":
            self._handle_operation_start_stream(
                self._start_initialization_operation,
                player=True,
            )
            return
        if path == "/api/player/init/story-direction":
            self._handle_operation_start_stream(
                self._start_story_direction_operation,
                player=True,
            )
            return
        if path == "/api/player/difficulty":
            self._handle_player_json(lambda: save_difficulty(_read_json(self)))
            return
        if path == "/api/player/dice-display":
            self._handle_player_json(lambda: save_dice_display(_read_json(self)))
            return
        if path == "/api/player/model-config/test":
            self._handle_player_json(
                lambda: test_model_connection(_read_json(self))
            )
            return
        if path == "/api/player/model-config":
            self._handle_player_json(lambda: save_model_config(_read_json(self)))
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
        if path == "/api/player/saves/rename":
            self._handle_player_json(
                lambda: player_saves_payload(rename_save(_read_json(self)))
            )
            return
        if path == "/api/player/saves/copy":
            self._handle_player_json(
                lambda: player_saves_payload(copy_save(_read_json(self)))
            )
            return
        if path == "/api/player/saves/delete":
            self._handle_player_json(
                lambda: player_saves_payload(delete_save(_read_json(self)))
            )
            return
        if path == "/api/player/saves/import":
            query = parse_qs(parsed_url.query)
            self._handle_save_import(query.get("name", [""])[0])
            return
        if path == "/api/player/history/undo":
            self._handle_player_json(undo_last_turn)
            return
        if path == "/api/player/history/redo":
            self._handle_player_json(redo_last_turn)
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
            self._handle_json(lambda: self._discard_pending_turn())
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
            self._handle_json(lambda: preview_core_memory(_read_json(self)))
            return
        if path == "/api/init/story-direction":
            self._handle_operation_start_stream(
                self._start_story_direction_operation,
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

    def _handle_save_export(self, save_id: str) -> None:
        temporary_path: Path | None = None
        response_started = False
        try:
            descriptor, raw_path = tempfile.mkstemp(prefix="tvg-save-", suffix=".zip")
            os.close(descriptor)
            temporary_path = Path(raw_path)
            temporary_path.unlink()
            export_save_archive(save_id, temporary_path)
            saves = list_saves().get("saves", [])
            save = next((item for item in saves if item.get("save_id") == save_id), None)
            display_name = str(save.get("name", "TVG-save")) if save else "TVG-save"
            payload_size = temporary_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(payload_size))
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=save.zip; filename*=UTF-8''{quote(display_name + '.zip')}",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            response_started = True
            with temporary_path.open("rb") as archive:
                for block in iter(lambda: archive.read(1024 * 1024), b""):
                    self.wfile.write(block)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            if not response_started and not self.wfile.closed:
                _json_response(
                    self,
                    500,
                    {"ok": False, "error": {"code": "export_failed", "message": str(exc)}},
                )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _handle_save_import(self, name: str) -> None:
        temporary_path: Path | None = None
        try:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type not in {"application/zip", "application/octet-stream"}:
                raise ValueError("Import body must be a ZIP archive")
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 512 * 1024 * 1024:
                raise ValueError("ZIP archive size must be between 1 byte and 512 MB")
            descriptor, raw_path = tempfile.mkstemp(prefix="tvg-import-", suffix=".zip")
            temporary_path = Path(raw_path)
            remaining = length
            with os.fdopen(descriptor, "wb") as destination:
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("ZIP upload ended before Content-Length")
                    destination.write(block)
                    remaining -= len(block)
            data = player_saves_payload(import_save_archive(temporary_path, name))
            _json_response(self, 200, {"ok": True, "data": data})
        except Exception as exc:
            _json_response(
                self,
                500,
                {"ok": False, "error": {"code": "import_failed", "message": str(exc)}},
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

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

    @staticmethod
    def _player_event(event: dict[str, Any]) -> dict[str, Any]:
        step = str(event.get("step", "progress"))
        data = event.get("data") or {}
        if step == "operation_started":
            public_data = {
                "operation_id": data.get("operation_id"),
                "operation_type": data.get("operation_type"),
                "resumed": bool(data.get("resumed", False)),
            }
        elif step == "operation_events_truncated":
            public_data = {
                "first_available_sequence": data.get("first_available_sequence"),
            }
        elif step == "scene_text_delta":
            public_data = {"delta": data.get("delta", "")}
        elif step == "scene_stream_started":
            public_data = {
                "attempt": data.get("attempt", 1),
                "version_index": data.get("version_index"),
                "revision": bool(data.get("revision", False)),
            }
            if data.get("rewrite_mode") is not None:
                public_data["rewrite_mode"] = data.get("rewrite_mode")
        elif step in {"rephrase_started", "reroll_started", "feedback_rewrite_started"}:
            public_data = {"version_index": data.get("version_index")}
        elif step == "feedback_rewrite_strategy_selected":
            public_data = {
                "strategy": data.get("strategy"),
                "reason_code": data.get("reason_code"),
            }
        elif step == "story_direction_done":
            public_data = {
                "config": data.get("config"),
                "story_direction": data.get("story_direction"),
            }
        elif step == "initialization_pending":
            public_data = {
                "turn_id": data.get("turn_id"),
                "status": data.get("status"),
            }
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

    @staticmethod
    def _player_operation(operation: dict[str, Any]) -> dict[str, Any]:
        metadata = operation.get("metadata") or {}
        policy = operation_policy(str(operation.get("operation_type", "")))
        return {
            "operation_id": operation.get("operation_id"),
            "operation_type": operation.get("operation_type"),
            "status": operation.get("status"),
            "current_step": operation.get("current_step"),
            "submitted_at": operation.get("submitted_at"),
            "started_at": operation.get("started_at"),
            "recovery_kind": policy.recovery_kind,
            "metadata": {
                key: metadata.get(key)
                for key in ("user_input", "turn_id", "group_id", "stage")
                if metadata.get(key) is not None
            },
        }

    def _load_recoverable_player_operations(self) -> dict[str, Any]:
        newest_first = [
            operation
            for operation in list_operations(recoverable_only=True, limit=None)
            if operation.get("operation_type") in PLAYER_OPERATION_TYPES
        ]
        return {
            "operations": [
                self._player_operation(operation)
                for operation in reversed(newest_first)
            ]
        }

    @staticmethod
    def _operation_id_from_action_path(
        path: str,
        *,
        player: bool,
        action: str,
    ) -> str:
        parts = path.strip("/").split("/")
        expected_length = 5 if player else 4
        if len(parts) != expected_length or parts[-1] != action:
            raise ValueError(f"Invalid Operation {action} path")
        operation_id = parts[-2].strip()
        if not operation_id:
            raise ValueError("operation_id is required")
        return operation_id

    def _acknowledge_player_operation(self, path: str) -> dict[str, Any]:
        operation_id = self._operation_id_from_action_path(
            path,
            player=True,
            action="ack",
        )
        self._load_player_operation(operation_id)
        acknowledge_operation(operation_id)
        return {"operation_id": operation_id, "acknowledged": True}

    def _acknowledge_operation(self, path: str) -> dict[str, Any]:
        operation_id = self._operation_id_from_action_path(
            path,
            player=False,
            action="ack",
        )
        return {"operation": acknowledge_operation(operation_id)}

    def _cancel_player_operation(self, path: str) -> dict[str, Any]:
        operation_id = self._operation_id_from_action_path(
            path,
            player=True,
            action="cancel",
        )
        self._load_player_operation(operation_id)
        return cancel_operation(operation_id)

    def _handle_player_operation_get(self, path: str, parsed_url) -> None:
        parts = path.strip("/").split("/")
        if len(parts) not in {4, 5}:
            self.send_error(404)
            return
        operation_id = parts[3].strip()
        if len(parts) == 5 and parts[4] == "events":
            self._handle_operation_event_stream(
                operation_id,
                parsed_url,
                player=True,
                resumed=True,
            )
            return
        if len(parts) == 4:
            self._handle_player_json(
                lambda: self._load_player_operation(operation_id)
            )
            return
        self.send_error(404)

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

    def _load_player_operation(self, operation_id: str) -> dict[str, Any]:
        operation = get_operation(operation_id)
        if (
            operation is None
            or operation.get("operation_type") not in PLAYER_OPERATION_TYPES
        ):
            raise ValueError("Operation not found")
        return {"operation": self._player_operation(operation)}

    def _handle_operation_start_stream(self, starter, *, player: bool = False) -> None:
        try:
            operation_id = starter()
        except Exception as exc:
            self._write_operation_start_error(exc, player=player)
            return
        self._handle_operation_event_stream(
            operation_id,
            urlparse(self.path),
            player=player,
            resumed=False,
        )

    def _write_operation_start_error(self, exc: Exception, *, player: bool) -> None:
        self._send_sse_headers()
        error = (
            {
                "ok": False,
                "error": {
                    "code": "operation_start_failed",
                    "message": str(exc) or "本次操作没有开始，请重新尝试。",
                    "recoverable": True,
                },
            }
            if player
            else {
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        try:
            self._write_sse_payload(error)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        self.close_connection = True

    def _handle_operation_event_stream(
        self,
        operation_id: str,
        parsed_url,
        *,
        player: bool = False,
        resumed: bool,
    ) -> None:
        try:
            after_sequence = max(
                0,
                int((parse_qs(parsed_url.query).get("after") or ["0"])[0]),
            )
        except (TypeError, ValueError):
            after_sequence = 0
        runtime = get_campaign_runtime()
        operation = runtime.operations.get(operation_id)
        if operation is None or (
            player
            and operation.operation_type not in PLAYER_OPERATION_TYPES
        ):
            self._write_operation_start_error(
                ValueError("Operation not found"),
                player=player,
            )
            return

        self._send_sse_headers()
        started_event = {
            "step": "operation_started",
            "data": {
                "operation_id": operation.operation_id,
                "operation_type": operation.operation_type,
                "resumed": resumed,
            },
        }
        if player:
            started_event = self._player_event(started_event)
        try:
            self._write_sse_payload({"ok": True, **started_event, "sequence": 0})
            while True:
                batch = runtime.operations.read_events(
                    operation_id,
                    after_sequence=after_sequence,
                    wait_timeout=15,
                )
                if batch.truncated:
                    truncated_event = {
                        "step": "operation_events_truncated",
                        "data": {
                            "first_available_sequence": (
                                batch.first_available_sequence
                            ),
                        },
                    }
                    if player:
                        truncated_event = self._player_event(truncated_event)
                    self._write_sse_payload({"ok": True, **truncated_event})
                for stored_event in batch.events:
                    event = {
                        "step": stored_event.step,
                        "data": stored_event.data,
                    }
                    if player:
                        event = self._player_event(event)
                    self._write_sse_payload({
                        "ok": True,
                        **event,
                        "sequence": stored_event.sequence,
                    })
                    after_sequence = stored_event.sequence
                if batch.status == "succeeded":
                    self._write_sse_payload({"ok": True, "step": "done"})
                    break
                if batch.status == "cancelled":
                    self._write_sse_payload({
                        "ok": True,
                        "step": "operation_cancelled",
                        "data": {},
                    })
                    self._write_sse_payload({"ok": True, "step": "done"})
                    break
                if batch.status == "failed":
                    failed = runtime.operations.get(operation_id)
                    message = (
                        failed.error_message if failed is not None else None
                    ) or "本次操作没有完成，请重新尝试。"
                    error = (
                        {
                            "ok": False,
                            "error": {
                                "code": "operation_failed",
                                "message": message,
                                "recoverable": True,
                            },
                        }
                        if player
                        else {"ok": False, "error": message}
                    )
                    self._write_sse_payload(error)
                    break
                if not batch.events:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            error = (
                {
                    "ok": False,
                    "error": {
                        "code": "operation_stream_failed",
                        "message": str(exc) or "任务状态读取失败。",
                        "recoverable": True,
                    },
                }
                if player
                else {"ok": False, "error": str(exc)}
            )
            try:
                self._write_sse_payload(error)
            except Exception:
                pass
        self.close_connection = True

    def _send_sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _write_sse_payload(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"data: {encoded}\n\n".encode("utf-8"))
        self.wfile.flush()

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

    def _start_generate_operation(self) -> str:
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
        request_id = str(payload.get("request_id", "")).strip() or None
        return start_generation_operation(
            user_input,
            difficulty=difficulty,
            max_chars=max_chars,
            request_id=request_id,
        )

    def _start_confirmation_operation(self) -> str:
        payload = _read_json(self)
        turn_id = str(payload.get("turn_id", "")).strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        write_db = bool(payload.get("write_db", True))
        scene_text = payload.get("scene_text")
        if scene_text is not None:
            scene_text = str(scene_text).strip()
        request_id = str(payload.get("request_id", "")).strip() or None
        return start_confirmation_operation(
            turn_id,
            write_db=write_db,
            scene_text=scene_text,
            request_id=request_id,
        )

    def _start_player_confirmation_operation(self) -> str:
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
            write_db=True,
            scene_text=scene_text,
            request_id=request_id,
        )

    def _start_story_direction_operation(self) -> str:
        payload = _read_json(self)
        request_id = str(payload.pop("request_id", "")).strip() or None
        return start_story_direction_operation(payload, request_id=request_id)

    def _start_initialization_operation(self) -> str:
        payload = _read_json(self)
        request_id = str(payload.pop("request_id", "")).strip() or None
        return start_initialization_operation(payload, request_id=request_id)

    def _retry_request(self) -> tuple[str, str | None]:
        payload = _read_json(self)
        turn_id = str(payload.get("turn_id", "")).strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        request_id = str(payload.get("request_id", "")).strip() or None
        return turn_id, request_id

    def _start_rephrase_operation(self) -> str:
        turn_id, request_id = self._retry_request()
        return start_rephrase_operation(turn_id, request_id=request_id)

    def _start_reroll_operation(self) -> str:
        turn_id, request_id = self._retry_request()
        return start_reroll_operation(turn_id, request_id=request_id)

    def _start_feedback_rewrite_operation(self) -> str:
        payload = _read_json(self)
        turn_id = str(payload.get("turn_id", "")).strip()
        source_scene_text = str(payload.get("source_scene_text", "")).strip()
        user_feedback = str(payload.get("user_feedback", "")).strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        if not source_scene_text:
            raise ValueError("source_scene_text is required")
        if not user_feedback:
            raise ValueError("user_feedback is required")
        request_id = str(payload.get("request_id", "")).strip() or None
        return start_feedback_rewrite_operation(
            turn_id,
            source_scene_text=source_scene_text,
            user_feedback=user_feedback,
            request_id=request_id,
        )

    def _discard_pending_turn(self):
        payload = _read_json(self)
        turn_id = str(payload.get("turn_id", "")).strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        return discard_pending_turn(turn_id)

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
    export_save_archive,
    import_save_archive,
    rename_save,
