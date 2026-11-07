"""Director NPC draft → NPCProfile promotion logic（纯领域，无 store）。"""

from __future__ import annotations

from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import (
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
        source_setting_ids=list(draft.source_setting_ids),
        created_turn=draft.created_turn,
        introduced_turn=draft.introduced_turn,
        disclosure_level=disclosure_level or "remote",
        name_hit_count=0,
    )


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
