"""Director NPC draft → NPCProfile promotion logic（纯领域，无 store）。"""

from __future__ import annotations

from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import NPC_DISCLOSURE_ORDER, NPCProfile


def npc_profile_from_director_draft(
    draft: DirectorNPCDraft,
    *,
    disclosure_level=None,
) -> NPCProfile:
    return NPCProfile(
        name=draft.name,
        public_summary=draft.role,
        role=draft.role,
        appearance=draft.appearance,
        personality_traits=list(draft.personality_traits),
        known_abilities=list(draft.known_abilities),
        faction=draft.faction,
        core_motivation=draft.core_motivation,
        hidden_secret=draft.hidden_secret,
        conflict_style=draft.conflict_style,
        social_style=draft.social_style,
        encounter_potentials=list(draft.encounter_potentials),
        source_director_npc_id=draft.draft_id,
        created_turn=draft.created_turn,
        introduced_turn=draft.introduced_turn,
        disclosure_level=disclosure_level or "remote",
        confidence=0.7,
        name_hit_count=0,
    )


def merge_director_draft_into_npc_profile(
    profile: NPCProfile,
    draft: DirectorNPCDraft,
    *,
    disclosure_level=None,
) -> NPCProfile:
    merged = profile.model_copy(deep=True)
    merged.public_summary = merged.public_summary or draft.role
    if merged.source_director_npc_id:
        merged.role = merged.role or draft.role
        merged.appearance = merged.appearance or draft.appearance
        if not merged.personality_traits:
            merged.personality_traits = list(draft.personality_traits)
        if not merged.known_abilities:
            merged.known_abilities = list(draft.known_abilities)
        merged.faction = merged.faction or draft.faction
    else:
        merged.role = draft.role
        merged.appearance = draft.appearance
        merged.personality_traits = list(draft.personality_traits)
        merged.known_abilities = list(draft.known_abilities)
        merged.faction = draft.faction
    merged.core_motivation = draft.core_motivation
    merged.hidden_secret = draft.hidden_secret
    merged.conflict_style = draft.conflict_style
    merged.social_style = draft.social_style
    merged.encounter_potentials = list(draft.encounter_potentials)
    merged.source_director_npc_id = merged.source_director_npc_id or draft.draft_id
    merged.created_turn = merged.created_turn or draft.created_turn
    merged.introduced_turn = draft.introduced_turn
    if disclosure_level is not None:
        current = NPC_DISCLOSURE_ORDER.get(merged.disclosure_level, 0)
        granted = NPC_DISCLOSURE_ORDER.get(disclosure_level, 0)
        if granted > current:
            merged.disclosure_level = disclosure_level
    merged.confidence = max(merged.confidence, 0.7)
    merged.name_hit_count = max(merged.name_hit_count, 0)
    return merged
