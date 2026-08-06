import json
import re
from collections.abc import Callable
from typing import Any, cast

from pydantic import BaseModel, Field, model_validator

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.context_facts.views import project_context_facts
from app.domain.director_event.views import project_director_events
from app.domain.plot_outline.views import project_turn_resolution
from app.domain.scene_writer.schemas import NextActionDirection
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.views import (
    project_current_state,
    project_npc_profiles,
)

from .schemas import NarrativeComposerContext, NarrativeComposerResult


class _RuntimePlannedEvent(BaseModel):
    event_fact: str = Field(min_length=1)
    linked_outcome_ids: list[str] = Field(default_factory=list)
    involved_entities: list[str] = Field(default_factory=list)


def run_narrative_composition(
    llm: LLMGateway,
    *,
    task: str,
    context: NarrativeComposerContext,
    recent_scene: ShortTermScene | None = None,
    turn_index: int = 1,
    extra_input: dict[str, Any] | None = None,
    trace_id: str | None = None,
    on_scene_text_delta: Callable[[str], None] | None = None,
    on_stream_start: Callable[[int], None] | None = None,
) -> NarrativeComposerResult:
    expected_outcome_ids = [
        frame.action_id for frame in context.turn_resolution.action_resolutions
    ]

    class RuntimeNarrativeComposerResult(BaseModel):
        director_reason: str | None = None
        planning_reasoning: str = Field(min_length=1)
        must_establish_facts: list[str] = Field(default_factory=list)
        must_not_establish_facts: list[str] = Field(default_factory=list)
        information_boundaries: list[str] = Field(default_factory=list)
        scene_stopping_point: str = Field(min_length=1)
        planned_events: list[_RuntimePlannedEvent] = Field(default_factory=list)
        scene_reasoning: str = Field(min_length=1)
        scene_text: str = Field(min_length=1)
        closing_prompt: str = Field(min_length=1)
        next_action_directions: list[NextActionDirection] = Field(
            default_factory=list,
            max_length=5,
        )

        @model_validator(mode="after")
        def _require_all_turn_resolution_outcomes(self):
            linked_ids: list[str] = []
            for event in self.planned_events:
                for outcome_id in event.linked_outcome_ids:
                    if outcome_id not in linked_ids:
                        linked_ids.append(outcome_id)
            expected_ids = set(expected_outcome_ids)
            unknown = [item for item in linked_ids if item not in expected_ids]
            if unknown:
                raise ValueError(
                    f"planned_events reference unknown linked_outcome_ids: {unknown}"
                )
            linked_set = set(linked_ids)
            missing = [item for item in expected_outcome_ids if item not in linked_set]
            if missing:
                raise ValueError(
                    "all turn resolution outcomes must be enacted in planned_events; "
                    f"missing linked_outcome_ids: {missing}"
                )
            return self

    scene_stream = _JSONStringFieldStream("scene_text")

    def start_attempt(attempt: int) -> None:
        scene_stream.reset()
        if on_stream_start is not None:
            on_stream_start(attempt)

    def consume_chunk(chunk: str) -> None:
        delta = scene_stream.feed(chunk)
        if delta and on_scene_text_delta is not None:
            on_scene_text_delta(delta)

    input_data = _base_input(context, recent_scene, turn_index)
    input_data.update(extra_input or {})
    input_data["output_schema_json"] = schema_json(
        RuntimeNarrativeComposerResult
    )
    result = cast(
        RuntimeNarrativeComposerResult,
        llm.run(
            task=task,
            input_data=input_data,
            output_schema=RuntimeNarrativeComposerResult,
            temperature=0.8,
            trace_id=trace_id,
            on_chunk=consume_chunk if on_scene_text_delta is not None else None,
            on_attempt_start=start_attempt if on_scene_text_delta is not None else None,
        ),
    )
    data = result.model_dump()
    data["planned_events"] = [
        {"event_id": f"e{index}", **event}
        for index, event in enumerate(data["planned_events"], start=1)
    ]
    data["scene_text"] = data["scene_text"].strip()
    data["closing_prompt"] = data["closing_prompt"].strip()
    if data["closing_prompt"] and data["scene_text"].endswith(data["closing_prompt"]):
        data["scene_text"] = data["scene_text"][:-len(data["closing_prompt"])].rstrip()
    return NarrativeComposerResult.model_validate(data)


def composer_context_input(
    context: NarrativeComposerContext,
    recent_scene: ShortTermScene | None,
    turn_index: int,
) -> dict[str, Any]:
    return _base_input(context, recent_scene, turn_index)


def _base_input(
    context: NarrativeComposerContext,
    recent_scene: ShortTermScene | None,
    turn_index: int,
) -> dict[str, Any]:
    return {
        "turn_index": turn_index,
        "core_memory_json": context.core_memory.model_dump_json(indent=2),
        "current_state_json": project_current_state(context.current_state),
        "user_input": context.user_input,
        "turn_resolution_json": project_turn_resolution(context.turn_resolution),
        "context_facts_json": project_context_facts(context.context_facts),
        "npc_profiles_json": project_npc_profiles(context.npc_profiles),
        "director_stage_json": json.dumps(
            (
                context.director_stage.model_dump(mode="json")
                if context.director_stage is not None
                else None
            ),
            ensure_ascii=False,
            indent=2,
        ),
        "engaged_events_json": project_director_events(
            context.engaged_events,
            effective_stage="engaged",
        ),
        "recent_scene_json": _project_recent_scene(recent_scene),
    }


def _project_recent_scene(scene: ShortTermScene | None) -> str:
    if scene is None:
        return "null"
    return json.dumps(
        {
            "scene_id": scene.scene_id,
            "turn_index": scene.turn_index,
            "scene_text": scene.scene_text,
        },
        ensure_ascii=False,
        indent=2,
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
