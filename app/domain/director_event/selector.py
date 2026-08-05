import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.schemas import WorldSetting
from app.domain.views import (
    project_current_state,
    project_scene_summaries,
)

from .schemas import DirectorEvent, DirectorEventRole, DirectorEventSelectionResult


def _event_list_view(event: DirectorEvent) -> dict:
    return {
        "role": event.role,
        "stage": event.stage,
        "title": event.title,
        "subject": event.subject,
        "intent": event.intent,
        "method": event.method,
        "actors": [actor.name for actor in event.actors],
    }


class DirectorEventSelector:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def select_or_create(
        self,
        *,
        role: DirectorEventRole,
        events: list[DirectorEvent],
        current_state: CurrentState,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary] | None = None,
        settings: list[WorldSetting] | None = None,
        required_actors: list[dict] | None = None,
        trace_id: str | None = None,
        guidance: str | None = None,
    ) -> DirectorEventSelectionResult:
        required_actors = required_actors or []
        input_data = {
            "events_json": json.dumps(
                [_event_list_view(event) for event in events],
                ensure_ascii=False,
                indent=2,
            ),
            "current_state_json": project_current_state(current_state),
            "core_memory_json": core_memory.model_dump_json(indent=2),
            "recent_summaries_json": project_scene_summaries(recent_summaries or []),
            "settings_json": json.dumps(
                [setting.director_view() for setting in (settings or [])],
                ensure_ascii=False,
                indent=2,
            ),
            "required_actors_json": json.dumps(
                required_actors,
                ensure_ascii=False,
                indent=2,
            ),
            "director_guidance": guidance or "",
            "output_schema_json": json.dumps(
                DirectorEventSelectionResult.model_json_schema(),
                ensure_ascii=False,
                indent=2,
            ),
        }
        expected_actor_keys = {
            str(actor["actor_key"])
            for actor in required_actors
        }

        def validate_actor_coverage(selection: DirectorEventSelectionResult) -> None:
            actual_actor_keys = {
                role.actor_key
                for role in selection.actor_roles
            }
            if actual_actor_keys != expected_actor_keys:
                raise ValueError(
                    "actor_roles must cover exactly every required actor_key"
                )

        return cast(
            DirectorEventSelectionResult,
            self.llm.run(
                task=f"director_event_select_{role}",
                input_data=input_data,
                output_schema=DirectorEventSelectionResult,
                temperature=0.3,
                trace_id=trace_id,
                output_validator=validate_actor_coverage,
            ),
        )
