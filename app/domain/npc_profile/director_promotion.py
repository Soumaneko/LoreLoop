"""Director NPC draft → NPCProfile promotion logic（纯领域，无 store）。"""

from __future__ import annotations

from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import (
    NPC_DISCLOSURE_ORDER,
    KnownAbility,
    NPCProfile,
)


def _profile_abilities(draft: DirectorNPCDraft) -> list[KnownAbility]:
    return [
        KnownAbility(name=ability.name, description=ability.description)
        for ability in draft.known_abilities
    ]


def npc_profile_from_director_draft(
    draft: DirectorNPCDraft,
    *,
    disclosure_level=None,
) -> NPCProfile:
    return NPCProfile(
        name=draft.name,
        role=draft.role,
        appearance=draft.appearance,
        personality_trait=draft.personality_trait,
        known_abilities=_profile_abilities(draft),
        faction=draft.faction,
        core_motivation=draft.core_motivation,
        hidden_secret=draft.hidden_secret,
        conflict_style=draft.conflict_style,
        social_style=draft.social_style,
        source_director_npc_id=draft.draft_id,
        created_turn=draft.created_turn,
        introduced_turn=draft.introduced_turn,
        disclosure_level=disclosure_level or "remote",
        name_hit_count=0,
    )


def merge_director_draft_into_npc_profile(
    profile: NPCProfile,
    draft: DirectorNPCDraft,
    *,
    disclosure_level=None,
) -> NPCProfile:
    merged = profile.model_copy(deep=True)
    if merged.source_director_npc_id:
        merged.role = merged.role or draft.role
        merged.appearance = merged.appearance or draft.appearance
        if not merged.personality_trait:
            merged.personality_trait = draft.personality_trait
        if not merged.known_abilities:
            merged.known_abilities = _profile_abilities(draft)
        merged.faction = merged.faction or draft.faction
    else:
        merged.role = draft.role
        merged.appearance = draft.appearance
        merged.personality_trait = draft.personality_trait
        merged.known_abilities = _profile_abilities(draft)
        merged.faction = draft.faction
    merged.core_motivation = draft.core_motivation
    merged.hidden_secret = draft.hidden_secret
    merged.conflict_style = draft.conflict_style
    merged.social_style = draft.social_style
    merged.source_director_npc_id = merged.source_director_npc_id or draft.draft_id
    merged.created_turn = merged.created_turn or draft.created_turn
    merged.introduced_turn = draft.introduced_turn
    if disclosure_level is not None:
        current = NPC_DISCLOSURE_ORDER.get(merged.disclosure_level, 0)
        granted = NPC_DISCLOSURE_ORDER.get(disclosure_level, 0)
        if granted > current:
            merged.disclosure_level = disclosure_level
    merged.name_hit_count = max(merged.name_hit_count, 0)
    return merged


def npc_profile_from_spontaneous_material(
    material,
    *,
    turn_index: int,
    scene_id: str | None = None,
) -> NPCProfile:
    payload = material.payload or {}
    known_abilities = [
        KnownAbility(
            name=a.get("name") if isinstance(a, dict) else a.name,
            description=(
                a.get("description", "") if isinstance(a, dict) else a.description
            ),
        )
        for a in payload.get("known_abilities", [])
    ]
    return NPCProfile(
        name=payload["name"],
        role=payload.get("role"),
        appearance=payload.get("appearance"),
        personality_trait=payload.get("personality_trait"),
        known_abilities=known_abilities,
        faction=payload.get("faction"),
        source_director_npc_id=None,
        created_turn=turn_index,
        introduced_turn=turn_index,
        disclosure_level=payload.get("view_mode") or "face_to_face",
        source_scene_ids=[scene_id] if scene_id else [],
        name_hit_count=0,
    )
