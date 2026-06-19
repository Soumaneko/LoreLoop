import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary, SceneSummaryDraft


class SceneSummarizer:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def summarize_turn(
        self,
        *,
        scene_text: str,
        current_state: CurrentState,
        scene_id: str,
        turn_index: int,
        trace_id: str | None = None,
    ) -> SceneSummary:
        from app.domain.views import project_current_state

        draft = cast(
            SceneSummaryDraft,
            self._llm.run(
                task="scene_summarize",
                input_data={
                    "scene_text": scene_text,
                    "scene_id": scene_id,
                    "current_state_json": project_current_state(current_state),
                },
                output_schema=SceneSummaryDraft,
                trace_id=trace_id,
            ),
        )
        return SceneSummary(
            level=1,
            content=draft.content,
            scene_ids=[scene_id],
            source_ids=[],
            turn_start=turn_index,
            turn_end=turn_index,
        )

    def condense(
        self,
        *,
        source_summaries: list[SceneSummary],
        target_level: int,
        trace_id: str | None = None,
    ) -> SceneSummary:
        if not source_summaries:
            raise ValueError("source_summaries is required")
        draft = cast(
            SceneSummaryDraft,
            self._llm.run(
                task="scene_summary_condense",
                input_data={
                    "target_level": target_level,
                    "source_summaries_json": json.dumps(
                        [summary.model_dump(mode="json") for summary in source_summaries],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        SceneSummaryDraft.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=SceneSummaryDraft,
                trace_id=trace_id,
            ),
        )
        return SceneSummary(
            level=target_level,
            content=draft.content,
            scene_ids=_unique(
                scene_id
                for summary in source_summaries
                for scene_id in summary.scene_ids
            ),
            source_ids=[summary.id for summary in source_summaries],
            turn_start=min(summary.turn_start for summary in source_summaries),
            turn_end=max(summary.turn_end for summary in source_summaries),
        )


def _unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
