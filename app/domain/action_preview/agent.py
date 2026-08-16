import json
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
    LLMProviderError,
)
from app.core.llm.schema import schema_json
from app.domain.action_preview.schemas import (
    ActionPreview,
    ActionPreviewResult,
    ActionPreviewResultDisplay,
    ActionPreviewResultLoose,
)
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.scene_writer.schemas import NextActionDirection
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import (
    project_current_state,
    project_scene_facts,
    project_scene_summaries,
)

_PREVIEW_ADAPTER = TypeAdapter(ActionPreview)


@dataclass(frozen=True)
class ActionPreviewWarning:
    code: str
    message: str


@dataclass
class _AttemptRecord:
    previews: list[ActionPreview] = field(default_factory=list)
    warnings: list[ActionPreviewWarning] = field(default_factory=list)


class ActionPreviewAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_warnings: list[ActionPreviewWarning] = []

    def generate(
        self,
        *,
        current_state: CurrentState,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary],
        next_action_directions: list[NextActionDirection] | None = None,
        scene_facts: SceneFactSet | None = None,
        scene_text: str | None = None,
        trace_id: str | None = None,
    ) -> ActionPreviewResult:
        self.last_warnings = []
        scene_text_section = ""
        if scene_text and (scene_facts is None or not scene_facts.facts):
            scene_text_section = (
                "## 本轮正文\n\n"
                "Scene Facts 为空，以下正文是当前轮次的场景依据；"
                "建议必须紧扣该正文。\n\n"
                f"```text\n{scene_text}\n```\n\n"
            )

        attempt_records: dict[int, _AttemptRecord] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: ActionPreviewResultLoose) -> None:
            top_warnings: list[ActionPreviewWarning] = []
            extra_keys = sorted((output.model_extra or {}).keys())
            if extra_keys:
                top_warnings.append(ActionPreviewWarning(
                    code="action_preview_top_level",
                    message=f"unexpected top-level fields: {extra_keys}",
                ))

            raw_previews = output.previews
            retry_errors = [
                warning.message for warning in top_warnings
            ]
            count_warnings: list[ActionPreviewWarning] = []
            if len(raw_previews) != 4:
                retry_errors.append(
                    f"previews must contain exactly 4 items, got "
                    f"{len(raw_previews)}"
                )
                count_warnings.append(ActionPreviewWarning(
                    code="action_preview_count",
                    message=(
                        f"previews must contain exactly 4 items, got "
                        f"{len(raw_previews)}"
                    ),
                ))

            previews: list[ActionPreview] = []
            dropped_warnings: list[ActionPreviewWarning] = []
            seen_labels: set[str] = set()
            for index, raw in enumerate(raw_previews):
                try:
                    preview = _parse_preview(raw)
                except ValidationError as exc:
                    retry_errors.append(f"preview[{index}]: {exc}")
                    dropped_warnings.append(ActionPreviewWarning(
                        code="action_preview_dropped",
                        message=f"preview[{index}] dropped: {exc}",
                    ))
                    continue
                label_key = preview.label
                if label_key in seen_labels:
                    retry_errors.append(
                        f"preview[{index}] duplicate label: {label_key}"
                    )
                    dropped_warnings.append(ActionPreviewWarning(
                        code="action_preview_dropped",
                        message=(
                            f"preview[{index}] dropped: duplicate label "
                            f"{label_key}"
                        ),
                    ))
                    continue
                if len(previews) >= 4:
                    retry_errors.append(
                        f"preview[{index}] exceeds the 4-preview cap"
                    )
                    dropped_warnings.append(ActionPreviewWarning(
                        code="action_preview_dropped",
                        message=(
                            f"preview[{index}] dropped: exceeds 4-preview cap"
                        ),
                    ))
                    continue
                seen_labels.add(label_key)
                previews.append(preview)

            attempt_records[current_attempt] = _AttemptRecord(
                previews=previews,
                warnings=[
                    *top_warnings,
                    *count_warnings,
                    *dropped_warnings,
                ],
            )
            if retry_errors:
                raise ValueError("; ".join(retry_errors))

        def salvage(exception: Exception) -> ActionPreviewResult:
            latest = attempt_records.get(2)
            if latest is None:
                latest = attempt_records.get(1)
            usable = attempt_records.get(2)
            if usable is None or not usable.previews:
                usable = attempt_records.get(1)
            if usable is not None and usable.previews:
                self.last_warnings = list(usable.warnings)
                return ActionPreviewResult(previews=usable.previews)
            warnings = list(latest.warnings) if latest is not None else []
            warnings.append(ActionPreviewWarning(
                code="action_preview_fallback",
                message=(
                    "Action preview failed; no suggestions were generated: "
                    f"{exception}"
                ),
            ))
            self.last_warnings = warnings
            return ActionPreviewResult(previews=[])

        try:
            result = cast(
                ActionPreviewResultLoose,
                self.llm.run(
                    task="action_preview",
                    input_data={
                        "next_action_directions_json": json.dumps(
                            [
                                direction.model_dump(mode="json")
                                for direction in (next_action_directions or [])
                            ],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "scene_facts_json": (
                            project_scene_facts(scene_facts)
                            if scene_facts is not None
                            else "{}"
                        ),
                        "scene_text_section": scene_text_section,
                        "current_state_json": project_current_state(current_state),
                        "core_memory_json": core_memory.model_dump_json(indent=2),
                        "recent_summaries_json": project_scene_summaries(
                            recent_summaries
                        ),
                        "output_schema_json": schema_json(
                            ActionPreviewResultDisplay
                        ),
                    },
                    output_schema=ActionPreviewResultLoose,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    temperature=0.8,
                    trace_id=trace_id,
                ),
            )
            if current_attempt in attempt_records:
                record = attempt_records[current_attempt]
            else:
                # StubGateway compatibility: real Gateway always calls
                # output_validator, but test stubs return without it.
                previews: list[ActionPreview] = []
                warnings: list[ActionPreviewWarning] = []
                seen_labels: set[str] = set()
                for index, raw in enumerate(result.previews):
                    try:
                        preview = _parse_preview(raw)
                    except ValidationError as exc:
                        warnings.append(ActionPreviewWarning(
                            code="action_preview_dropped",
                            message=f"preview[{index}] dropped: {exc}",
                        ))
                        continue
                    if preview.label in seen_labels or len(previews) >= 4:
                        warnings.append(ActionPreviewWarning(
                            code="action_preview_dropped",
                            message=f"preview[{index}] dropped",
                        ))
                        continue
                    seen_labels.add(preview.label)
                    previews.append(preview)
                record = _AttemptRecord(
                    previews=previews,
                    warnings=warnings,
                )
            self.last_warnings = list(record.warnings)
            return ActionPreviewResult(previews=record.previews)
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
            LLMProviderError,
        ) as exc:
            return salvage(exc)


def _parse_preview(raw: Any) -> ActionPreview:
    if isinstance(raw, dict):
        cleaned = dict(raw)
        for key in ("level", "label", "good_result", "risk"):
            value = cleaned.get(key)
            if isinstance(value, str):
                cleaned[key] = value.strip()
        raw = cleaned
    return _PREVIEW_ADAPTER.validate_python(raw)
