from __future__ import annotations

from typing import Any

from app.domain.continuity.schemas import ContinuityItemDraft
from app.domain.entity.schemas import profile_from_semantic
from app.domain.npc_profile.schemas import (
    NPCProfile,
    NPCProfileUpdateInput,
    profile_from_update,
)
from app.runtime.serialization import model_data
from app.runtime.session import CampaignRuntime, get_campaign_runtime

_CONTINUITY_EDITABLE_FIELDS = {
    "subject",
    "statement",
    "entities",
    "location",
    "deadline",
}
_CONTINUITY_NULLABLE_FIELDS = {"location", "deadline"}
_NPC_PUBLIC_FIELDS = {
    "name",
    "aliases",
    "public_summary",
    "role",
    "appearance",
    "personality_traits",
    "known_abilities",
    "faction",
    "description",
    "current_location",
    "attitude_to_player",
    "current_intent",
}
_NPC_NULLABLE_FIELDS = {
    "public_summary",
    "role",
    "appearance",
    "faction",
    "description",
    "current_location",
    "attitude_to_player",
    "current_intent",
}
_NPC_LIST_FIELDS = {"aliases", "personality_traits", "known_abilities"}
_NPC_DIRECTOR_FIELDS = {
    "core_motivation",
    "hidden_secret",
    "conflict_style",
    "social_style",
    "encounter_potentials",
    "director_notes",
    "source_director_npc_id",
    "created_turn",
    "introduced_turn",
}
_ENTITY_EDITABLE_FIELDS = {
    "entity_type",
    "name",
    "aliases",
    "summary",
    "item_type",
    "item_source",
    "location_type",
    "features",
    "parent_location",
    "description",
}
_ENTITY_NULLABLE_FIELDS = {
    "summary",
    "item_type",
    "item_source",
    "location_type",
    "parent_location",
    "description",
}
_ENTITY_LIST_FIELDS = {"aliases", "features"}


def _normalize_payload(
    payload: dict[str, Any],
    *,
    nullable: set[str],
    list_fields: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Coerce empty strings back to None so untouched optional fields stay null."""
    normalized: dict[str, Any] = {}
    for field, value in payload.items():
        if field in list_fields:
            if isinstance(value, list):
                normalized[field] = [
                    item for item in value if str(item).strip()
                ]
            else:
                normalized[field] = value
        elif field in nullable and isinstance(value, str) and not value.strip():
            normalized[field] = None
        else:
            normalized[field] = value
    return normalized


def _strip_name_and_aliases(update) -> None:
    """Strip whitespace from the canonical name and aliases before saving."""
    name = getattr(update, "name", None)
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("name must not be empty")
        update.name = name
    update.aliases = [
        alias.strip() for alias in update.aliases if alias.strip()
    ]


def _maintenance_running(runtime: CampaignRuntime) -> bool:
    counts = runtime.jobs.status_counts()
    return bool(counts.get("queued") or counts.get("running"))


def load_player_records() -> dict[str, Any]:
    """Player-safe archive projection: continuity + NPC + entity records."""
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        return _load_player_records(runtime)


def _load_player_records(runtime: CampaignRuntime) -> dict[str, Any]:
    with runtime.stores.continuity() as store:
        continuity_items = store.list_all()
    with runtime.stores.npc_profile() as store:
        npc_profiles = store.list_all(active_only=False)
    with runtime.stores.entity() as store:
        entity_profiles = store.list_all(active_only=False)
    return {
        "maintenance_running": _maintenance_running(runtime),
        "continuity": [
            {
                "id": item.id,
                "status": item.status,
                "created_turn": item.created_turn,
                "updated_turn": item.updated_turn,
                "closed_turn": item.closed_turn,
                "subject": item.subject,
                "statement": item.statement,
                "entities": list(item.entities),
                "location": item.location,
                "deadline": item.deadline,
                "editable": (
                    list(_CONTINUITY_EDITABLE_FIELDS)
                    if item.status == "active"
                    else []
                ),
            }
            for item in continuity_items
        ],
        "npc_profiles": [
            {
                "id": profile.npc_id,
                "public": model_data(profile.update_view()),
                "director_only": {
                    key: model_data(getattr(profile, key))
                    for key in _NPC_DIRECTOR_FIELDS
                },
            }
            for profile in npc_profiles
        ],
        "entity_profiles": [
            {
                "id": profile.entity_id,
                "entity_type": profile.entity_type,
                "fields": model_data(profile.semantic_view()),
            }
            for profile in entity_profiles
        ],
    }


def update_player_record(
    kind: str,
    record_id: str,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Update one existing record's editable fields.

    Continuity edits accept only active items and never change status.
    NPC edits are restricted to public fields; director fields are read-only.
    Entity edits accept the semantic fields only.
    """
    runtime = get_campaign_runtime()
    if kind not in {"continuity", "npc", "entity"}:
        raise ValueError(f"unknown record kind: {kind}")
    if not record_id:
        raise ValueError("record_id is required")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    with runtime.confirmation_lock:
        if kind == "continuity":
            return _update_continuity(runtime, record_id, payload)
        if kind == "npc":
            return _update_npc_profile(runtime, record_id, payload)
        return _update_entity_profile(runtime, record_id, payload)


def _latest_confirmed_turn(runtime: CampaignRuntime) -> int:
    with runtime.stores.short_term() as store:
        entries = store.list_navigation_entries()
    if not entries:
        return 0
    return max(turn_index for turn_index, _user_input in entries)


def _update_continuity(
    runtime: CampaignRuntime,
    item_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with runtime.stores.continuity() as store:
        current = store.get(item_id)
        if current is None:
            raise ValueError(f"continuity item not found: {item_id}")
        unknown = set(payload) - _CONTINUITY_EDITABLE_FIELDS
        if unknown:
            raise ValueError(
                "continuity edit only supports: "
                + ", ".join(sorted(_CONTINUITY_EDITABLE_FIELDS))
            )
        if current.status != "active":
            raise ValueError("only active continuity items can be edited")
        payload = _normalize_payload(
            payload,
            nullable=_CONTINUITY_NULLABLE_FIELDS,
        )
        draft = ContinuityItemDraft(
            kind=current.kind,
            subject=payload.get("subject", current.subject),
            statement=payload.get("statement", current.statement),
            entities=payload.get("entities", current.entities),
            location=payload.get("location", current.location),
            deadline=payload.get("deadline", current.deadline),
        )
        turn_index = _latest_confirmed_turn(runtime)
        with store.transaction():
            updated = store.update_active(item_id, draft, turn_index=turn_index)
    return {
        "id": updated.id,
        "status": updated.status,
        "created_turn": updated.created_turn,
        "updated_turn": updated.updated_turn,
        "closed_turn": updated.closed_turn,
        "subject": updated.subject,
        "statement": updated.statement,
        "entities": list(updated.entities),
        "location": updated.location,
        "deadline": updated.deadline,
        "editable": list(_CONTINUITY_EDITABLE_FIELDS),
    }


def _update_npc_profile(
    runtime: CampaignRuntime,
    npc_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    director_fields = set(payload) & _NPC_DIRECTOR_FIELDS
    if director_fields:
        raise ValueError(
            "NPC director fields are read-only: "
            + ", ".join(sorted(director_fields))
        )
    unknown = set(payload) - _NPC_PUBLIC_FIELDS
    if unknown:
        raise ValueError(
            "NPC public fields only: " + ", ".join(sorted(_NPC_PUBLIC_FIELDS))
        )
    with runtime.stores.npc_profile() as store:
        current = store.get_by_id(npc_id)
        if current is None:
            raise ValueError(f"NPC profile not found: {npc_id}")
        payload = _normalize_payload(
            payload,
            nullable=_NPC_NULLABLE_FIELDS,
            list_fields=_NPC_LIST_FIELDS,
        )
        update = current.update_view()
        for field in _NPC_PUBLIC_FIELDS:
            if field in payload:
                setattr(update, field, payload[field])
        _strip_name_and_aliases(update)
        validated = NPCProfileUpdateInput.model_validate(update.model_dump())
        _validate_npc_name_conflicts(store, npc_id, validated)
        merged = profile_from_update(
            validated,
            npc_id=current.npc_id,
            source_scene_ids=list(current.source_scene_ids),
            confidence=current.confidence,
            updated_at=None,
            active=current.active,
            name_hit_count=current.name_hit_count,
            alias_hit_counts=dict(current.alias_hit_counts),
        )
        merged.absent_turns = current.absent_turns
        _preserve_npc_director_fields(merged, current)
        store.upsert(merged)
        saved = store.get_by_id(npc_id)
        if saved is None:
            raise RuntimeError("NPC profile disappeared after save")
    return {
        "id": saved.npc_id,
        "public": model_data(saved.update_view()),
        "director_only": {
            key: model_data(getattr(saved, key))
            for key in _NPC_DIRECTOR_FIELDS
        },
    }


def _preserve_npc_director_fields(profile: NPCProfile, current: NPCProfile) -> None:
    for field in _NPC_DIRECTOR_FIELDS:
        setattr(profile, field, getattr(current, field))


def _validate_npc_name_conflicts(store, npc_id: str, update: NPCProfileUpdateInput) -> None:
    keys = {
        update.name.strip().casefold(),
        *[alias.strip().casefold() for alias in update.aliases],
    }
    for other in store.list_all(active_only=False):
        if other.npc_id == npc_id:
            continue
        other_keys = {
            other.name.strip().casefold(),
            *[alias.strip().casefold() for alias in other.aliases],
        }
        if keys & other_keys:
            raise ValueError(
                f"NPC name or alias conflicts with existing profile: {other.name}"
            )


def _validate_entity_name_conflicts(store, entity_id: str, update) -> None:
    keys = {
        update.name.strip().casefold(),
        *[alias.strip().casefold() for alias in update.aliases],
    }
    for other in store.list_all(active_only=False):
        if other.entity_id == entity_id:
            continue
        if other.entity_type != update.entity_type:
            continue
        other_keys = {
            other.name.strip().casefold(),
            *[alias.strip().casefold() for alias in other.aliases],
        }
        if keys & other_keys:
            raise ValueError(
                f"entity name or alias conflicts with existing profile: {other.name}"
            )


def _update_entity_profile(
    runtime: CampaignRuntime,
    entity_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    unknown = set(payload) - _ENTITY_EDITABLE_FIELDS
    if unknown:
        raise ValueError(
            "entity semantic fields only: "
            + ", ".join(sorted(_ENTITY_EDITABLE_FIELDS))
        )
    with runtime.stores.entity() as store:
        current = store.get_by_id(entity_id)
        if current is None:
            raise ValueError(f"entity profile not found: {entity_id}")
        payload = _normalize_payload(
            payload,
            nullable=_ENTITY_NULLABLE_FIELDS,
            list_fields=_ENTITY_LIST_FIELDS,
        )
        semantic = current.semantic_view()
        for field in _ENTITY_EDITABLE_FIELDS:
            if field in payload:
                setattr(semantic, field, payload[field])
        _strip_name_and_aliases(semantic)
        _validate_entity_name_conflicts(store, entity_id, semantic)
        merged = profile_from_semantic(
            semantic,
            entity_id=current.entity_id,
            source_scene_ids=list(current.source_scene_ids),
            confidence=current.confidence,
            updated_at=None,
            active=current.active,
            name_hit_count=current.name_hit_count,
            alias_hit_counts=dict(current.alias_hit_counts),
        )
        store.upsert(merged)
        saved = store.get_by_id(entity_id)
        if saved is None:
            raise RuntimeError("entity profile disappeared after save")
    return {
        "id": saved.entity_id,
        "entity_type": saved.entity_type,
        "fields": model_data(saved.semantic_view()),
    }
