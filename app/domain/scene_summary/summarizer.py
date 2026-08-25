import json
from dataclasses import dataclass
from typing import Any

from pydantic import Field, create_model

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.llm.schema import schema_json
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary, SceneSummaryDraft


@dataclass(frozen=True)
class SceneSummaryWarning:
    code: str
    message: str


class SceneSummarizer:
    def __init__(self, llm: LLMGateway):
        self._llm = llm
        self.last_warnings: list[SceneSummaryWarning] = []

    def summarize_turn(
        self,
        *,
        core_memory: CoreMemory,
        scene_text: str,
        current_state: CurrentState,
        scene_id: str,
        turn_index: int,
        trace_id: str | None = None,
    ) -> SceneSummary:
        from app.domain.views import project_current_state

        self.last_warnings = []
        if not scene_text.strip():
            return SceneSummary(
                level=1,
                content="本轮无正文。",
                scene_ids=[scene_id],
                source_ids=[],
                turn_start=turn_index,
                turn_end=turn_index,
            )

        content = self._request_draft(
            task="scene_summarize",
            input_data={
                "core_memory_json": core_memory.model_dump_json(indent=2),
                "scene_text": scene_text,
                "scene_id": scene_id,
                "current_state_json": project_current_state(current_state),
            },
            hard_limit=300,
            accept_limit=375,
            soft_limit=450,
            trace_id=trace_id,
        )
        if content is None:
            self.last_warnings.append(SceneSummaryWarning(
                code="scene_summary_fallback",
                message="No usable turn summary candidate; using placeholder.",
            ))
            content = "（本轮无可用摘要）"
        return SceneSummary(
            level=1,
            content=content,
            scene_ids=[scene_id],
            source_ids=[],
            turn_start=turn_index,
            turn_end=turn_index,
        )

    def condense(
        self,
        *,
        core_memory: CoreMemory,
        source_summaries: list[SceneSummary],
        target_level: int,
        trace_id: str | None = None,
    ) -> SceneSummary | None:
        if not source_summaries:
            raise ValueError("source_summaries is required")
        self.last_warnings = []
        content = self._request_draft(
            task="scene_summary_condense",
            input_data={
                "core_memory_json": core_memory.model_dump_json(indent=2),
                "target_level": target_level,
                "source_summaries_json": json.dumps(
                    [summary.model_dump(mode="json") for summary in source_summaries],
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            hard_limit=400,
            accept_limit=500,
            soft_limit=600,
            trace_id=trace_id,
        )
        if content is None:
            self.last_warnings.append(SceneSummaryWarning(
                code="scene_summary_condense_skipped",
                message="No usable condensed summary candidate; skipped.",
            ))
            return None
        return SceneSummary(
            level=target_level,
            content=content,
            scene_ids=_unique(
                scene_id
                for summary in source_summaries
                for scene_id in summary.scene_ids
            ),
            source_ids=[summary.id for summary in source_summaries],
            turn_start=min(summary.turn_start for summary in source_summaries),
            turn_end=max(summary.turn_end for summary in source_summaries),
        )

    def _request_draft(
        self,
        *,
        task: str,
        input_data: dict[str, Any],
        hard_limit: int,
        accept_limit: int,
        soft_limit: int,
        trace_id: str | None,
    ) -> str | None:
        prompt_schema = _summary_draft_schema(max_length=hard_limit)
        # Gateway 不设 max_length：第一轮由 output_validator 按 Accept 上限
        # （H < len <= A）拒绝，第二轮允许保留到软上限 S；超过软上限才截断。
        gateway_schema = SceneSummaryDraft
        candidates: dict[int, str] = {}
        truncated_attempts: set[int] = set()
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: SceneSummaryDraft) -> None:
            content = output.content.strip()
            if not content:
                raise ValueError("content must be non-empty")
            if len(content) > soft_limit:
                truncated_attempts.add(current_attempt)
                content = content[:soft_limit]
            candidates[current_attempt] = content
            if current_attempt == 1 and len(content) > accept_limit:
                raise ValueError(
                    f"content length {len(content)} exceeds accept limit "
                    f"{accept_limit}; return to the prompt target {hard_limit}"
                )

        def selected(attempt: int | None) -> tuple[str | None, int | None]:
            if attempt is not None and attempt in candidates:
                return candidates[attempt], attempt
            if 2 in candidates:
                return candidates[2], 2
            if 1 in candidates:
                return candidates[1], 1
            return None, None

        def warn_if_truncated(attempt: int | None) -> None:
            if attempt is not None and attempt in truncated_attempts:
                self.last_warnings.append(SceneSummaryWarning(
                    code="scene_summary_truncated",
                    message=(
                        f"summary exceeded soft limit {soft_limit} and was "
                        f"truncated to {soft_limit}."
                    ),
                ))

        try:
            self._llm.run(
                task=task,
                input_data={
                    **input_data,
                    "output_schema_json": schema_json(prompt_schema),
                },
                output_schema=gateway_schema,
                output_validator=validate_output,
                on_attempt_start=on_attempt_start,
                trace_id=trace_id,
            )
            content, attempt = selected(current_attempt)
            warn_if_truncated(attempt)
            return content
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
        ):
            content, attempt = selected(None)
            warn_if_truncated(attempt)
            return content


def _unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _summary_draft_schema(*, max_length: int):
    return create_model(
        f"RuntimeSceneSummaryDraft{max_length}",
        __base__=SceneSummaryDraft,
        content=(str, Field(min_length=1, max_length=max_length)),
    )
