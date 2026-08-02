import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionExtractionResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import (
    project_current_state,
    project_npc_profiles,
    project_scene_summaries,
)


def _event_hook_view(event: DirectorEvent) -> dict:
    return {
        "event_id": event.event_id,
        "role": event.role,
        "title": event.title,
        "subject": event.subject,
        "intent": event.intent,
        "hooks": [
            {
                "hook_id": hook.hook_id,
                "kind": hook.kind,
                "content": hook.content,
                "trigger_anchors": hook.trigger_anchors,
            }
            for hook in event.hook_pool
            if not hook.consumed
        ],
    }


class ActionParser:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def parse(
        self,
        user_input: str,
        current_state: CurrentState,
        *,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary] | None = None,
        npc_profiles: list[NPCProfileDirectorView] | None = None,
        events: list[DirectorEvent] | None = None,
        trace_id: str | None = None,
    ) -> ActionExtractionResult:
        return cast(
            ActionExtractionResult,
            self.llm.run(
                task="action_extract",
                input_data={
                    "user_input": user_input,
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "recent_summaries_json": project_scene_summaries(recent_summaries or []),
                    "npc_profiles_json": project_npc_profiles(npc_profiles or []),
                    "director_events_json": json.dumps(
                        [_event_hook_view(event) for event in (events or [])],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        ActionExtractionResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=ActionExtractionResult,
                trace_id=trace_id,
            ),
        )
