import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import project_current_state, project_scene_summaries

from .schemas import DirectorEvent, DirectorEventSelectionResult


def _archived_event_dedupe_view(event: DirectorEvent) -> dict:
    return {
        "title": event.title,
        "backstage_premise": event.backstage_premise,
        "stages": event.stages.model_dump(mode="json"),
        "related_npcs": event.related_npcs,
        "location_constraints": event.location_constraints,
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
        suspended_events: list[DirectorEvent],
        archived_events: list[DirectorEvent],
        standby_director_npcs: list[DirectorNPCDraft],
        scene_text: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        scene_summaries: list[SceneSummary],
        trace_id: str | None = None,
    ) -> DirectorEventSelectionResult:
        return cast(
            DirectorEventSelectionResult,
            self.llm.run(
                task="director_event_select",
                input_data={
                    "suspended_events_json": json.dumps(
                        [event.model_dump(mode="json") for event in suspended_events],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "archived_events_json": json.dumps(
                        [_archived_event_dedupe_view(event) for event in archived_events],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "standby_director_npcs_json": json.dumps(
                        [_standby_director_npc_view(npc) for npc in standby_director_npcs],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "scene_text": scene_text,
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "scene_summaries_json": project_scene_summaries(scene_summaries),
                    "output_schema_json": json.dumps(
                        DirectorEventSelectionResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=DirectorEventSelectionResult,
                temperature=0.3,
                trace_id=trace_id,
            ),
        )
