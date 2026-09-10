from __future__ import annotations

from dataclasses import dataclass

from app.core.text import normalize_compact_key
from app.domain.npc_profile.selection import resolve_actor_profiles


@dataclass(frozen=True)
class ScheduledNpc:
    """One NPC the Director arranged to take part in this turn."""

    name: str
    role: str | None
    appearance: str | None
    personality_trait: str | None
    guidance: str
    source: str
    npc_id: str | None = None
    draft_id: str | None = None


def collect_scheduled_npcs(materials, npc_store) -> list[ScheduledNpc]:
    """Fold every Director material into this turn's arranged-participant list.

    Both Composer (via ``GenerationState.director_stage``) and the confirmation
    pipeline (via ``PendingTurn.director_stage``) already receive the same
    ``DirectorStageResult.materials``, so both derive the identical list from
    this single helper rather than from a new field.

    All skills are treated uniformly: engaged events contribute their stored
    actors, ``present_event`` contributes the actors it explicitly declares,
    ``introduce_npc`` contributes its draft/spontaneous card, and
    ``upgrade_disclosure`` contributes the profile it promotes. Reserved
    temporary characters that have no profile yet still yield a name-based
    entry for confirmation-side identity binding.
    """
    scheduled: list[ScheduledNpc] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()

    def add(entry: ScheduledNpc) -> None:
        name_key = normalize_compact_key(entry.name)
        if (entry.npc_id and entry.npc_id in seen_ids) or (
            name_key and name_key in seen_names
        ):
            return
        if entry.npc_id:
            seen_ids.add(entry.npc_id)
        if name_key:
            seen_names.add(name_key)
        scheduled.append(entry)

    for material in materials or []:
        payload = material.payload or {}
        if material.kind == "engaged_event":
            for profile in resolve_actor_profiles(
                payload.get("actors") or [], npc_store
            ):
                add(ScheduledNpc(
                    name=profile.name,
                    role=profile.role,
                    appearance=profile.appearance,
                    personality_trait=profile.personality_trait,
                    guidance=material.guidance,
                    source="intrude_engaged_event",
                    npc_id=profile.npc_id,
                ))
        elif material.kind == "event":
            for raw in payload.get("actors") or []:
                name = (
                    raw if isinstance(raw, str) else str(raw.get("name") or "")
                ).strip()
                if not name:
                    continue
                profile = npc_store.get_by_name(name)
                add(ScheduledNpc(
                    name=profile.name if profile is not None else name,
                    role=profile.role if profile is not None else None,
                    appearance=(
                        profile.appearance if profile is not None else None
                    ),
                    personality_trait=(
                        profile.personality_trait
                        if profile is not None
                        else None
                    ),
                    guidance=material.guidance,
                    source="present_event",
                    npc_id=profile.npc_id if profile is not None else None,
                ))
        elif material.kind == "npc":
            source = payload.get("source")
            if source in {"director_npc", "spontaneous"}:
                name = str(payload.get("name") or "").strip()
                if not name:
                    continue
                add(ScheduledNpc(
                    name=name,
                    role=payload.get("role") or None,
                    appearance=payload.get("appearance") or None,
                    personality_trait=payload.get("personality_trait") or None,
                    guidance=material.guidance,
                    source=source,
                    npc_id=None,
                    draft_id=(
                        material.resource_id
                        if source == "director_npc"
                        else None
                    ),
                ))
            elif source == "npc_profile":
                profile = npc_store.get_by_id(material.resource_id)
                if profile is None:
                    continue
                add(ScheduledNpc(
                    name=profile.name,
                    role=profile.role,
                    appearance=profile.appearance,
                    personality_trait=profile.personality_trait,
                    guidance=material.guidance,
                    source="upgrade_disclosure",
                    npc_id=profile.npc_id,
                ))
    return scheduled
