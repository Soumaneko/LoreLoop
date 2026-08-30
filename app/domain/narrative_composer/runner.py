from dataclasses import dataclass
import json
import re
from collections.abc import Callable
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.action_judgment.schemas import OutcomeBand
from app.domain.context_facts.views import project_context_facts
from app.domain.director_event.views import project_director_events
from app.domain.scene_writer.schemas import NextActionDirection
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.views import (
    project_current_state,
    project_npc_profiles,
)

from .schemas import NarrativeComposerContext, NarrativeComposerResult, PlannedEvent


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


_HARD_LIMIT = 80
_ACCEPT_LIMIT = 120
_SOFT_LIMIT = 180


@dataclass(frozen=True)
class ComposerWarning:
    code: str
    message: str


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
    judgments_data = [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        for item in context.action_judgments
    ]
    return {
        "turn_index": turn_index,
        "core_memory_json": context.core_memory.model_dump_json(indent=2),
        "current_state_json": project_current_state(context.current_state),
        "user_input": context.user_input,
        "action_judgments_json": json.dumps(judgments_data, ensure_ascii=False, indent=2),
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


class _HardActionResolution(BaseModel):
    action_id: str = Field(description="对应玩家输入的 action_id (A1, A2...)")
    outcome_band: OutcomeBand = Field(
        description="严格继承检定档位 (critical_success / success / partial_success / failure / critical_failure)"
    )
    resolved_fact: str = Field(
        min_length=1,
        max_length=_HARD_LIMIT,
        description=f"一句话锁定直接客观事实结论（目标约 {_HARD_LIMIT} 字）",
    )


class _HardComposerResult(BaseModel):
    director_reason: str | None = Field(
        default=None,
        description="说明采用或不采用 Director Agent 舞台材料的理由",
    )
    action_resolutions: list[_HardActionResolution] = Field(
        default_factory=list,
        description="逐项锁定每个 action 的成败事实，按顺序 100% 一一对应覆盖所有 action_id",
    )
    scene_stopping_point: str = Field(
        min_length=1,
        description="在本回合全部确定结果落实后停留的唯一可交互状态。必须是具体事实快照",
    )
    planned_events: list[PlannedEvent] = Field(
        default_factory=list,
        min_length=1,
        max_length=4,
        description="用 1-4 个按发生顺序排列的确定事件，把语义骨架具体演出来",
    )
    scene_reasoning: str = Field(
        min_length=1,
        description="2-4 句写作前审计。确认正文将如何落实刚刚制定的规划",
    )
    scene_text: str = Field(
        min_length=1,
        description="正文",
    )
    closing_prompt: str = Field(
        min_length=1,
        description="结尾提示",
    )
    next_action_directions: list[NextActionDirection] = Field(
        default_factory=list,
        max_length=5,
    )


class _LooseActionResolution(BaseModel):
    action_id: str = Field(min_length=1)
    outcome_band: Any
    resolved_fact: str = Field(min_length=1)


class _LooseComposerResult(BaseModel):
    director_reason: str | None = None
    action_resolutions: list[_LooseActionResolution] = Field(default_factory=list)
    scene_stopping_point: str = Field(min_length=1)
    planned_events: list[PlannedEvent] = Field(default_factory=list)
    scene_reasoning: str = Field(min_length=1)
    scene_text: str = Field(min_length=1)
    closing_prompt: str = Field(min_length=1)
    next_action_directions: list[NextActionDirection] = Field(
        default_factory=list,
        max_length=5,
    )

    @field_validator("scene_text", mode="before")
    @classmethod
    def _strip_scene_text(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v


def run_narrative_composition(
    llm: LLMGateway,
    *,
    task: str = "narrative_compose",
    context: NarrativeComposerContext,
    recent_scene: ShortTermScene | None = None,
    turn_index: int = 1,
    extra_input: dict[str, Any] | None = None,
    trace_id: str | None = None,
    on_scene_text_delta: Callable[[str], None] | None = None,
    on_stream_start: Callable[[int], None] | None = None,
    on_warning: Callable[[ComposerWarning], None] | None = None,
) -> NarrativeComposerResult:

    expected_action_ids: list[str] = [
        str(item.action_id) for item in context.action_judgments
    ]
    expected_bands: dict[str, str] = {
        str(item.action_id): str(item.outcome_band)
        for item in context.action_judgments
    }

    current_attempt = 1
    scene_stream = _JSONStringFieldStream("scene_text")

    def start_attempt(attempt: int) -> None:
        nonlocal current_attempt
        current_attempt = attempt
        scene_stream.reset()
        if on_stream_start is not None:
            on_stream_start(attempt)

    def consume_chunk(chunk: str) -> None:
        delta = scene_stream.feed(chunk)
        if delta and on_scene_text_delta is not None:
            on_scene_text_delta(delta)

    def validate_output(result: _LooseComposerResult) -> None:
        res_ids = [r.action_id for r in result.action_resolutions]
        if res_ids != expected_action_ids:
            raise ValueError(
                f"action_resolutions must strictly match expected action_ids in order without duplicates or omissions; "
                f"expected {expected_action_ids}, got {res_ids}"
            )

        if not result.planned_events or len(result.planned_events) > 4:
            raise ValueError(
                f"planned_events must contain between 1 and 4 events covering all action_ids; got {len(result.planned_events)}"
            )

        linked_ids: list[str] = []
        for event in result.planned_events:
            for outcome_id in event.linked_outcome_ids:
                if outcome_id not in linked_ids:
                    linked_ids.append(outcome_id)

        expected_set = set(expected_action_ids)
        unknown = [item for item in linked_ids if item not in expected_set]
        if unknown:
            raise ValueError(
                f"planned_events reference unknown action_ids in linked_outcome_ids: {unknown}"
            )

        missing = [item for item in expected_action_ids if item not in set(linked_ids)]
        if missing:
            raise ValueError(
                f"all actions must be covered in planned_events; missing action_ids: {missing}"
            )

        if linked_ids != expected_action_ids:
            raise ValueError(
                f"planned_events must introduce action_ids in the expected order: expected {expected_action_ids}, got {linked_ids}"
            )

        retry_errors: list[str] = []
        for index, r in enumerate(result.action_resolutions):
            exp_band = expected_bands.get(r.action_id)
            if exp_band and r.outcome_band != exp_band:
                retry_errors.append(
                    f"outcome_band mismatch for action {r.action_id}: expected '{exp_band}', got '{r.outcome_band}'"
                )
            fact_len = len(r.resolved_fact)
            if current_attempt == 1 and fact_len > _ACCEPT_LIMIT:
                retry_errors.append(
                    f"action_resolutions[{index}].resolved_fact length {fact_len} exceeds accept limit {_ACCEPT_LIMIT}; "
                    f"return to the prompt target {_HARD_LIMIT}"
                )
            elif current_attempt > 1 and fact_len > _SOFT_LIMIT:
                r.resolved_fact = r.resolved_fact[:_SOFT_LIMIT]
                if on_warning is not None:
                    on_warning(
                        ComposerWarning(
                            code="resolved_fact_truncated",
                            message=(
                                f"action_resolutions[{index}].resolved_fact exceeded soft limit "
                                f"{_SOFT_LIMIT} and was truncated"
                            ),
                        )
                    )
        if retry_errors:
            raise ValueError("; ".join(retry_errors))

    input_data = _base_input(context, recent_scene, turn_index)
    input_data.update(extra_input or {})
    input_data["output_schema_json"] = schema_json(_HardComposerResult)

    result = cast(
        _LooseComposerResult,
        llm.run(
            task=task,
            input_data=input_data,
            output_schema=_LooseComposerResult,
            output_validator=validate_output,
            temperature=0.8,
            trace_id=trace_id,
            on_chunk=consume_chunk if on_scene_text_delta is not None else None,
            on_attempt_start=start_attempt,
        ),
    )
    data = result.model_dump()
    data["scene_text"] = data["scene_text"].strip()
    data["closing_prompt"] = data["closing_prompt"].strip()
    if data["closing_prompt"] and data["scene_text"].endswith(data["closing_prompt"]):
        data["scene_text"] = data["scene_text"][:-len(data["closing_prompt"])].rstrip()
    return NarrativeComposerResult.model_validate(data)


class NarrativeComposerAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_warnings: list[ComposerWarning] = []

    def compose(
        self,
        context: NarrativeComposerContext,
        *,
        recent_scene: ShortTermScene | None = None,
        turn_index: int = 1,
        extra_input: dict[str, Any] | None = None,
        trace_id: str | None = None,
        on_scene_text_delta: Callable[[str], None] | None = None,
        on_stream_start: Callable[[int], None] | None = None,
    ) -> NarrativeComposerResult:
        self.last_warnings = []
        return run_narrative_composition(
            self.llm,
            context=context,
            recent_scene=recent_scene,
            turn_index=turn_index,
            extra_input=extra_input,
            trace_id=trace_id,
            on_scene_text_delta=on_scene_text_delta,
            on_stream_start=on_stream_start,
            on_warning=lambda w: self.last_warnings.append(w),
        )
