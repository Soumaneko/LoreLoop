from __future__ import annotations

import argparse
import json
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.llm import LLMGateway, LLMProvider, PromptLoader, TraceLogger
from app.core.llm.providers import ProviderResult


class FilePromptExchange:
    """Exchanges rendered prompts and manual LLM responses through files."""

    def __init__(
        self,
        session_dir: Path,
        *,
        poll_interval: float = 0.5,
        timeout_seconds: float = 3600,
    ):
        self.session_dir = session_dir
        self.poll_interval = poll_interval
        self.timeout_seconds = timeout_seconds
        self._counter = 0
        self._lock = threading.Lock()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._write_instructions()

    def create_gateway(self) -> LLMGateway:
        provider = FilePromptProvider(self)
        return ManualFileGateway(
            provider=provider,
            prompt_loader=PromptLoader(),
            tracer=TraceLogger(),
            default_model="manual-web",
        )

    def request(
        self,
        *,
        task: str,
        attempt: int,
        trace_id: str | None,
        prompt: str,
        model: str,
        temperature: float,
    ) -> str:
        request_number = self._next_request_number()
        safe_task = re.sub(r"[^a-zA-Z0-9_-]+", "_", task).strip("_") or "llm"
        stem = f"{request_number:02d}_{safe_task}_attempt_{attempt}"
        prompt_path = self.session_dir / f"{stem}.prompt.md"
        response_path = self.session_dir / f"{stem}.response.txt"
        metadata_path = self.session_dir / f"{stem}.metadata.json"

        prompt_path.write_text(prompt, encoding="utf-8")
        response_path.touch()
        metadata_path.write_text(
            json.dumps(
                {
                    "request_number": request_number,
                    "task": task,
                    "attempt": attempt,
                    "trace_id": trace_id,
                    "model": model,
                    "temperature": temperature,
                    "prompt_path": str(prompt_path),
                    "response_path": str(response_path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print()
        print(f"[等待 LLM] {task} / attempt {attempt}")
        print(f"Prompt:   {prompt_path}")
        print(f"Response: {response_path}")
        print("将 prompt 复制到网页，把模型返回的完整 JSON 写入 response 文件。")

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            response = response_path.read_text(encoding="utf-8").strip()
            if response:
                print(f"[已读取响应] {response_path}")
                return response
            time.sleep(self.poll_interval)

        raise TimeoutError(f"等待手动响应超时: {response_path}")

    def write_event(self, event: dict[str, Any]) -> None:
        event_path = self.session_dir / "pipeline_events.jsonl"
        with self._lock:
            with event_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def write_result(self, filename: str, data: Any) -> Path:
        path = self.session_dir / filename
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        return path

    def _next_request_number(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def _write_instructions(self) -> None:
        instructions = (
            "1. 运行脚本后，每次 LLM 调用会生成 *.prompt.md 和空的 *.response.txt。\n"
            "2. 将 prompt 文件内容复制到网页模型。\n"
            "3. 将网页返回的完整 JSON 写入同名 response 文件并保存。\n"
            "4. 脚本读取响应后会继续执行，下一次调用会生成下一组文件。\n"
            "5. 若 JSON 无法通过 schema 校验，脚本会生成带重试说明的新 prompt 文件。\n"
        )
        (self.session_dir / "README.txt").write_text(instructions, encoding="utf-8")


class FilePromptProvider(LLMProvider):
    def __init__(self, exchange: FilePromptExchange):
        self.exchange = exchange
        self.task = "unknown"
        self.trace_id: str | None = None
        self.attempt = 0

    def begin_task(self, task: str, trace_id: str | None) -> None:
        self.task = task
        self.trace_id = trace_id
        self.attempt = 0

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> ProviderResult:
        self.attempt += 1
        return ProviderResult(
            content=self.exchange.request(
                task=self.task,
                attempt=self.attempt,
                trace_id=self.trace_id,
                prompt=prompt,
                model=model,
                temperature=temperature,
            )
        )


class ManualFileGateway(LLMGateway):
    def run(
        self,
        task: str,
        input_data: dict[str, Any],
        output_schema,
        **kwargs,
    ):
        provider = self.provider
        if not isinstance(provider, FilePromptProvider):
            raise TypeError("ManualFileGateway requires FilePromptProvider")
        provider.begin_task(task, kwargs.get("trace_id"))
        return super().run(task, input_data, output_schema, **kwargs)


def install_manual_gateway(exchange: FilePromptExchange) -> None:
    """Redirect graph-local gateway constructors for manual prompt exchange."""
    from app.graph import (
        confirmation_nodes,
        generation_helpers,
        initialization_helpers,
        maintenance_helpers,
    )

    factory = exchange.create_gateway
    confirmation_nodes.create_gateway = factory
    generation_helpers.create_gateway = factory
    initialization_helpers.create_gateway = factory
    maintenance_helpers.create_gateway = factory


def run_manual_turn(
    user_input: str,
    *,
    exchange: FilePromptExchange,
    difficulty: str = "困难",
    max_chars: int = 1500,
    confirm: bool = False,
    write_db: bool = False,
) -> dict[str, Any]:
    from app.graph.confirmation_graph import confirm_stream
    from app.graph.generation_graph import generate_turn_stream

    install_manual_gateway(exchange)

    latest: dict[str, Any] = {}
    for event in generate_turn_stream(
        user_input,
        difficulty=difficulty,
        max_chars=max_chars,
    ):
        exchange.write_event(event)
        latest.update(event.get("data") or {})
        print(f"[管线] {event.get('step')}")

    turn_id = latest.get("turn_id")
    scene_result = latest.get("scene_result") or {}
    scene_text = scene_result.get("scene_text", "")
    exchange.write_result("generated_scene.txt", scene_text)
    exchange.write_result("generation_result.json", latest)

    if not confirm:
        return latest
    if not turn_id:
        raise RuntimeError("生成管线未返回 turn_id，无法执行确认流")

    confirmation: dict[str, Any] = {}
    for event in confirm_stream(turn_id, write_db=write_db):
        exchange.write_event(event)
        confirmation.update(event.get("data") or {})
        print(f"[确认流] {event.get('step')}")
    exchange.write_result("confirmation_result.json", confirmation)
    return {"generation": latest, "confirmation": confirmation}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将管线中的完整 LLM prompt 输出为文件，并等待手动网页响应。",
    )
    parser.add_argument("user_input", nargs="?", help="玩家行动；省略时从终端读取")
    parser.add_argument(
        "--difficulty",
        choices=("简单", "普通", "困难"),
        default="困难",
    )
    parser.add_argument("--max-chars", type=int, default=1500)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="生成正文后继续执行确认流",
    )
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="确认流写入 DB；必须同时使用 --confirm",
    )
    parser.add_argument(
        "--session-dir",
        type=Path,
        help="prompt/response 输出目录，默认写入 debug_prompts/<时间戳>",
    )
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=3600)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.write_db and not args.confirm:
        raise SystemExit("--write-db 必须与 --confirm 一起使用")

    user_input = args.user_input or input("玩家行动: ").strip()
    if not user_input:
        raise SystemExit("玩家行动不能为空")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = args.session_dir or Path("debug_prompts") / (
        f"{timestamp}_{uuid.uuid4().hex[:6]}"
    )
    exchange = FilePromptExchange(
        session_dir,
        poll_interval=max(0.1, args.poll_interval),
        timeout_seconds=max(1, args.timeout),
    )

    print(f"手动 Prompt 会话目录: {session_dir.resolve()}")
    result = run_manual_turn(
        user_input,
        exchange=exchange,
        difficulty=args.difficulty,
        max_chars=max(100, min(5000, args.max_chars)),
        confirm=args.confirm,
        write_db=args.write_db,
    )
    exchange.write_result("result.json", result)
    print(f"完成。结果目录: {session_dir.resolve()}")


if __name__ == "__main__":
    main()
