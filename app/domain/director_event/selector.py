import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
    project_scene_summaries,
)

from .schemas import DirectorEvent, DirectorEventRole, DirectorEventSelectionResult


def _event_list_view(event: DirectorEvent) -> dict:
    return {
        "role": event.role,
        "status": event.status,
        "title": event.title,
        "premise": event.premise,
        "director_npc_id": event.director_npc_id,
        "actors": event.actors,
        "objective": event.objective,
        "bridge_anchors": event.bridge_anchors,
        "milestones": event.milestones,
        "exposure": event.exposure,
        "entry_points": [entry.model_dump(mode="json") for entry in event.entry_points],
    }


def _standby_director_npc_view(npc: DirectorNPCDraft) -> dict:
    return {
        "draft_id": npc.draft_id,
        "name": npc.name,
        "role": npc.role,
        "appearance": npc.appearance,
        "personality_traits": npc.personality_traits,
        "known_abilities": npc.known_abilities,
        "faction": npc.faction,
        "core_motivation": npc.core_motivation,
        "hidden_secret": npc.hidden_secret,
        "conflict_style": npc.conflict_style,
        "social_style": npc.social_style,
        "encounter_potentials": npc.encounter_potentials,
    }


class DirectorEventSelector:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def select_or_create(
        self,
        *,
        role: DirectorEventRole,
        events: list[DirectorEvent],
        standby_director_npcs: list[DirectorNPCDraft],
        current_state: CurrentState,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary] | None = None,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        npc_profiles: list[NPCProfileDirectorView] | None = None,
        trace_id: str | None = None,
    ) -> DirectorEventSelectionResult:
        input_data = {
            "events_json": json.dumps(
                [_event_list_view(event) for event in events],
                ensure_ascii=False,
                indent=2,
            ),
            "current_state_json": project_current_state(current_state),
            "core_memory_json": core_memory.model_dump_json(indent=2),
            "recent_summaries_json": project_scene_summaries(recent_summaries or []),
            "entity_profiles_json": project_entity_profiles(entity_profiles or []),
            "npc_profiles_json": project_npc_profiles(npc_profiles or []),
            "output_schema_json": json.dumps(
                DirectorEventSelectionResult.model_json_schema(),
                ensure_ascii=False,
                indent=2,
            ),
        }
        if role == "ambient":
            input_data["standby_director_npcs_json"] = json.dumps(
                [_standby_director_npc_view(npc) for npc in standby_director_npcs],
                ensure_ascii=False,
                indent=2,
            )

        return cast(
            DirectorEventSelectionResult,
            self.llm.run(
                task=f"director_event_select_{role}",
                input_data=input_data,
                output_schema=DirectorEventSelectionResult,
                temperature=0.3,
                trace_id=trace_id,
            ),
        )
