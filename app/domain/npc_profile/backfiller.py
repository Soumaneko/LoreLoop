import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.schemas import (
    NPCProfile,
    NPCProfileBackfillOutput,
)
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import project_current_state, project_scene_summaries


class NPCProfileBackfiller:
    def __init__(self, gateway: LLMGateway):
        self._gateway = gateway

    def backfill(
        self,
        *,
        profile: NPCProfile,
        existing_profiles: list[NPCProfile],
        scene_text: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary] | None = None,
        trace_id: str | None = None,
    ) -> NPCProfile:
        result = cast(
            NPCProfileBackfillOutput,
            self._gateway.run(
                task="npc_profile_backfill",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "current_state_summary_json": project_current_state(current_state),
                    "recent_summaries_json": project_scene_summaries(recent_summaries or []),
                    "scene_text": scene_text,
                    "new_npc_profile_json": profile.public_view().model_dump_json(indent=2),
                    "existing_npcs_json": json.dumps(
                        [_existing_npc_view(npc) for npc in existing_profiles],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        NPCProfileBackfillOutput.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=NPCProfileBackfillOutput,
                trace_id=trace_id,
            ),
        )
        merged = profile.model_copy(deep=True)
        _apply_backfill(merged, result)
        return merged


def _existing_npc_view(npc: NPCProfile) -> dict:
    return {
        "name": npc.name,
        "role": npc.role,
        "core_motivation": npc.core_motivation,
        "hidden_secret": npc.hidden_secret,
    }


def _apply_backfill(profile: NPCProfile, backfill: NPCProfileBackfillOutput) -> None:
    original_name = profile.name
    if backfill.name.strip() and backfill.name.strip() != original_name:
        profile.name = backfill.name.strip()
        alias_keys = {alias.casefold() for alias in profile.aliases}
        if original_name.strip() and original_name.casefold() not in alias_keys:
            profile.aliases.append(original_name)
            profile.alias_hit_counts[original_name] = max(
                profile.alias_hit_counts.get(original_name, 0),
                1,
            )
    profile.role = profile.role or backfill.role
    profile.appearance = profile.appearance or backfill.appearance
    if not profile.personality_traits:
        profile.personality_traits = list(backfill.personality_traits)
    if not profile.known_abilities:
        profile.known_abilities = list(backfill.known_abilities)
    profile.faction = profile.faction or backfill.faction
    profile.core_motivation = profile.core_motivation or backfill.core_motivation
    profile.hidden_secret = profile.hidden_secret or backfill.hidden_secret
    profile.conflict_style = profile.conflict_style or backfill.conflict_style
    profile.social_style = profile.social_style or backfill.social_style
