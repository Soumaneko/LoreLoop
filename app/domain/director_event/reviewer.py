import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import project_current_state, project_scene_summaries

from .schemas import (
    DirectorEvent,
    DirectorEventEngagedReviewBatch,
    DirectorEventHiddenReviewBatch,
)


def _shared_inputs(
    *,
    events: list[DirectorEvent],
    include_hooks: bool,
    user_input: str,
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
) -> dict:
    event_views = []
    for event in events:
        view = event.semantic_view()
        view.update(
            subject=event.subject,
            intent=event.intent,
            method=event.method,
            macro_intent=event.macro_intent,
        )
        if include_hooks:
            view["hook_pool"] = [
                {
                    "hook_id": hook.hook_id,
                    "kind": hook.kind,
                    "content": hook.content,
                    "trigger_anchors": list(hook.trigger_anchors),
                }
                for hook in event.hook_pool
                if not hook.consumed
            ]
        event_views.append(view)
    return {
        "events_json": json.dumps(
            event_views,
            ensure_ascii=False,
            indent=2,
        ),
        "user_input": user_input,
        "scene_facts_json": scene_facts,
        "current_state_json": project_current_state(current_state),
        "core_memory_json": core_memory.model_dump_json(indent=2),
        "scene_summaries_json": project_scene_summaries(scene_summaries),
    }


class DirectorEventHiddenReviewer:
    """批量审查 hidden 事件：检测 hook 消费与主角是否接触（接触则转入 engaged）。"""

    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def review_batch(
        self,
        *,
        events: list[DirectorEvent],
        user_input: str,
        scene_facts: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        scene_summaries: list[SceneSummary],
        trace_id: str | None = None,
        guidance: str | None = None,
    ) -> DirectorEventHiddenReviewBatch:
        inputs = _shared_inputs(
            events=events,
            include_hooks=True,
            user_input=user_input,
            scene_facts=scene_facts,
            current_state=current_state,
            core_memory=core_memory,
            scene_summaries=scene_summaries,
        )
        inputs["director_guidance"] = guidance or ""
        inputs["output_schema_json"] = json.dumps(
            DirectorEventHiddenReviewBatch.model_json_schema(),
            ensure_ascii=False,
            indent=2,
        )
        expected_ids = {event.event_id for event in events}

        def validator(batch) -> None:
            if {review.event_id for review in batch.reviews} != expected_ids:
                raise ValueError(
                    "hidden review batch must cover every supplied event"
                )

        return cast(
            DirectorEventHiddenReviewBatch,
            self.llm.run(
                task="director_event_review_hidden",
                input_data=inputs,
                output_schema=DirectorEventHiddenReviewBatch,
                temperature=0.2,
                trace_id=trace_id,
                output_validator=validator,
            ),
        )


class DirectorEventEngagedReviewer:
    """批量审查 engaged 事件：判断核心冲突是否收束（resolve）。"""

    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def review_batch(
        self,
        *,
        events: list[DirectorEvent],
        user_input: str,
        scene_facts: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        scene_summaries: list[SceneSummary],
        trace_id: str | None = None,
        guidance: str | None = None,
    ) -> DirectorEventEngagedReviewBatch:
        inputs = _shared_inputs(
            events=events,
            include_hooks=False,
            user_input=user_input,
            scene_facts=scene_facts,
            current_state=current_state,
            core_memory=core_memory,
            scene_summaries=scene_summaries,
        )
        inputs["director_guidance"] = guidance or ""
        inputs["output_schema_json"] = json.dumps(
            DirectorEventEngagedReviewBatch.model_json_schema(),
            ensure_ascii=False,
            indent=2,
        )
        expected_ids = {event.event_id for event in events}

        def validator(batch) -> None:
            if {review.event_id for review in batch.reviews} != expected_ids:
                raise ValueError(
                    "engaged review batch must cover every supplied event"
                )

        return cast(
            DirectorEventEngagedReviewBatch,
            self.llm.run(
                task="director_event_review_engaged",
                input_data=inputs,
                output_schema=DirectorEventEngagedReviewBatch,
                temperature=0.2,
                trace_id=trace_id,
                output_validator=validator,
            ),
        )
