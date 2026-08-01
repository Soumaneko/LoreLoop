import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
    project_scene_summaries,
)

from .schemas import (
    DirectorEvent,
    DirectorEventInterventionPlan,
    DirectorEventReviewResult,
)


def _normalize_profile_anchor(value: str) -> str:
    return " ".join(value.casefold().split())


def _profile_matches_anchors(
    profile: EntityProfileUpdateInput | NPCProfileDirectorView,
    anchors: set[str],
) -> bool:
    names = {profile.name, *profile.aliases}
    return any(_normalize_profile_anchor(name) in anchors for name in names)


class DirectorEventInterventionPlanner:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def plan(
        self,
        *,
        active_event: DirectorEvent,
        redirect_review: DirectorEventReviewResult,
        user_input: str,
        scene_facts: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary] | None = None,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        npc_profiles: list[NPCProfileDirectorView] | None = None,
        entity_mentions: list[str] | None = None,
        npc_mentions: list[str] | None = None,
        trace_id: str | None = None,
        guidance: str | None = None,
    ) -> DirectorEventInterventionPlan:
        if active_event.route_mode != "default":
            raise ValueError("InterventionPlanner requires the default route")
        if redirect_review.action != "redirect":
            raise ValueError("InterventionPlanner requires a redirect review")
        actor_anchors = {
            _normalize_profile_anchor(actor)
            for actor in active_event.actors
            if actor.strip()
        }
        entity_anchors = actor_anchors | {
            _normalize_profile_anchor(name)
            for name in entity_mentions or []
            if name.strip()
        }
        npc_anchors = actor_anchors | {
            _normalize_profile_anchor(name)
            for name in [
                *(participant.name for participant in current_state.scene.participants),
                *(npc_mentions or []),
            ]
            if name.strip()
        }
        relevant_entities = [
            profile
            for profile in entity_profiles or []
            if _profile_matches_anchors(profile, entity_anchors)
        ]
        relevant_npcs = [
            profile
            for profile in npc_profiles or []
            if _profile_matches_anchors(profile, npc_anchors)
        ]
        return cast(
            DirectorEventInterventionPlan,
            self.llm.run(
                task="director_event_intervention_plan",
                input_data={
                    "active_event_json": json.dumps(
                        active_event.semantic_view(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "redirect_review_json": json.dumps(
                        redirect_review.model_dump(mode="json"),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "user_input": user_input,
                    "scene_facts_json": scene_facts,
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "recent_summaries_json": project_scene_summaries(
                        recent_summaries or []
                    ),
                    "entity_profiles_json": project_entity_profiles(
                        relevant_entities
                    ),
                    "npc_profiles_json": project_npc_profiles(relevant_npcs),
                    "director_guidance": guidance or "",
                    "output_schema_json": json.dumps(
                        DirectorEventInterventionPlan.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=DirectorEventInterventionPlan,
                temperature=0.3,
                trace_id=trace_id,
            ),
        )
