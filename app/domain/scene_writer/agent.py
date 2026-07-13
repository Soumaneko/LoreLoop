import json
import re
from collections.abc import Callable
from typing import cast

from pydantic import Field, create_model

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfilePublicView
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.scene_writer.views import project_outline, project_recent_scene
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
)


class SceneWriterAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def write(
        self,
        *,
        outline: PlotOutlineResult,
        current_state: CurrentState,
        core_memory: CoreMemory,
        recent_scene: ShortTermScene | None = None,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        npc_profiles: list[NPCProfilePublicView] | None = None,
        turn_index: int = 1,
        max_chars: int = 1500,
        trace_id: str | None = None,
        on_scene_text_delta: Callable[[str], None] | None = None,
        on_stream_start: Callable[[int], None] | None = None,
    ) -> SceneWriterResult:
        if max_chars < 1:
            raise ValueError("max_chars must be >= 1")
        runtime_schema = create_model(
            "RuntimeSceneWriterResult",
            __base__=SceneWriterResult,
            scene_reasoning=(str, Field(min_length=1)),
            scene_text=(str, Field(min_length=1)),
            closing_prompt=(str, Field(min_length=1)),
        )
        schema = runtime_schema.model_json_schema()
        scene_stream = _JSONStringFieldStream("scene_text")

        def start_attempt(attempt: int) -> None:
            scene_stream.reset()
            if on_stream_start is not None:
                on_stream_start(attempt)

        def consume_chunk(chunk: str) -> None:
            delta = scene_stream.feed(chunk)
            if delta and on_scene_text_delta is not None:
                on_scene_text_delta(delta)

        result = cast(
            SceneWriterResult,
            self.llm.run(
                task="scene_write",
                input_data={
                    "turn_index": turn_index,
                    "max_chars": max_chars,
                    "outline_json": project_outline(outline),
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "entity_profiles_json": project_entity_profiles(entity_profiles or []),
                    "npc_profiles_json": project_npc_profiles(npc_profiles or []),
                    "recent_scene_json": project_recent_scene(recent_scene),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=runtime_schema,
                temperature=0.8,
                trace_id=trace_id,
                on_chunk=consume_chunk if on_scene_text_delta is not None else None,
                on_attempt_start=start_attempt if on_scene_text_delta is not None else None,
            ),
        )
        closing_prompt = result.closing_prompt.strip()
        scene_text = result.scene_text.strip()
        if closing_prompt and scene_text.endswith(closing_prompt):
            scene_text = scene_text[:-len(closing_prompt)].rstrip()
        return SceneWriterResult(
            scene_reasoning=result.scene_reasoning,
            scene_text=scene_text,
            closing_prompt=closing_prompt,
            next_action_directions=result.next_action_directions,
        )


class _JSONStringFieldStream:
    def __init__(self, field: str):
        self._marker = re.compile(rf'"{re.escape(field)}"\s*:\s*"')
        self.reset()

    def reset(self) -> None:
        self._prefix = ""
        self._started = False
        self._finished = False
        self._escape = ""

    def feed(self, chunk: str) -> str:
        if self._finished or not chunk:
            return ""
        if not self._started:
            self._prefix += chunk
            match = self._marker.search(self._prefix)
            if match is None:
                return ""
            chunk = self._prefix[match.end():]
            self._prefix = ""
            self._started = True

        output: list[str] = []
        for char in chunk:
            if self._escape:
                self._escape += char
                if self._escape == "\\u" or (
                    self._escape.startswith("\\u") and len(self._escape) < 6
                ):
                    continue
                output.append(self._decode_escape(self._escape))
                self._escape = ""
                continue
            if char == "\\":
                self._escape = "\\"
            elif char == '"':
                self._finished = True
                break
            else:
                output.append(char)
        return "".join(output)

    @staticmethod
    def _decode_escape(value: str) -> str:
        try:
            return json.loads(f'"{value}"')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return value
