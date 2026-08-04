from __future__ import annotations

from typing import Any

from app.domain.continuity.schemas import ContinuityItemDraft
from app.domain.npc_profile.schemas import (
    NPCProfile,
    NPCProfileUpdateInput,
    profile_from_update,
)
from app.domain.world_setting.schemas import WorldSetting
from app.runtime.serialization import model_data
from app.runtime.session import CampaignRuntime, get_campaign_runtime

_CONTINUITY_EDITABLE_FIELDS = {
    "subject",
    "statement",
    "entities",
    "location",
}
_CONTINUITY_NULLABLE_FIELDS = {"location"}
_NPC_PUBLIC_FIELDS = {
    "name",
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
_NPC_LIST_FIELDS = {"personality_traits", "known_abilities"}
_NPC_DIRECTOR_FIELDS = {
    "core_motivation",
    "hidden_secret",
    "conflict_style",
    "social_style",
    "encounter_potentials",
    "director_notes",
    "source_director_npc_id",
    "source_setting_ids",
    "created_turn",
    "introduced_turn",
}
_NPC_ADVANCED_DIRECTOR_FIELDS = {
    "core_motivation",
    "hidden_secret",
    "conflict_style",
    "social_style",
    "encounter_potentials",
    "director_notes",
}
_NPC_ADVANCED_NULLABLE_FIELDS = {
    "core_motivation",
    "hidden_secret",
    "conflict_style",
    "social_style",
    "director_notes",
}
_NPC_ADVANCED_LIST_FIELDS = {"encounter_potentials"}
_SETTING_GENERAL_FIELDS = {
    "name",
    "summary",
    "public_facts",
}
_SETTING_ADVANCED_FIELDS = {
    "name",
    "summary",
    "public_facts",
    "hidden_facts",
}
_SETTING_LIST_FIELDS = {
    "public_facts",
    "hidden_facts",
    "related_setting_ids",
}

RECORD_VIEWS = ("general", "advanced")


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


def load_player_records(view: str = "general") -> dict[str, Any]:
    """Player archive projection: continuity + NPC + Setting records."""
    if view not in RECORD_VIEWS:
        raise ValueError(f"unknown records view: {view}")
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        return _load_player_records(runtime, view=view)


def _load_player_records(runtime: CampaignRuntime, *, view: str = "general") -> dict[str, Any]:
    with runtime.stores.continuity() as store:
        continuity_items = store.list_all()
    with runtime.stores.npc_profile() as store:
        npc_profiles = store.list_all(active_only=False)
    with runtime.stores.world_setting() as store:
        world_settings = store.list_all()
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
            _npc_profile_record(profile, view=view)
            for profile in npc_profiles
            if view == "advanced" or _npc_has_appeared(profile)
        ],
        "world_settings": [
            _setting_record(setting, view=view)
            for setting in world_settings
            if view == "advanced" or setting.hit_count > 0
        ],
    }


def _npc_has_appeared(profile) -> bool:
    if profile.name_hit_count > 0:
        return True
    return any(count > 0 for count in profile.alias_hit_counts.values())


def _npc_profile_record(profile, *, view: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": profile.npc_id,
        "public": model_data(profile.update_view()),
    }
    if view == "advanced":
        record["director_only"] = {
            key: model_data(getattr(profile, key))
            for key in _NPC_ADVANCED_DIRECTOR_FIELDS
        }
    return record


def _setting_record(setting, *, view: str) -> dict[str, Any]:
    fields = _SETTING_ADVANCED_FIELDS if view == "advanced" else _SETTING_GENERAL_FIELDS
    return {
        "id": setting.setting_id,
        "fields": setting.model_dump(
            mode="json",
            include=fields,
        ),
    }


def update_player_record(
    kind: str,
    record_id: str,
    payload: dict[str, Any] | None,
    view: str = "general",
) -> dict[str, Any]:
    """Update one existing record's editable fields.

    Continuity edits accept only active items and never change status.
    NPC edits are restricted to public fields, plus the director content fields
    when view is "advanced"; system/meta fields are always read-only.
    Setting edits accept only the static asset fields visible in the view.
    """
    runtime = get_campaign_runtime()
    if view not in RECORD_VIEWS:
        raise ValueError(f"unknown records view: {view}")
    if kind not in {"continuity", "npc", "setting"}:
        raise ValueError(f"unknown record kind: {kind}")
    if not record_id:
        raise ValueError("record_id is required")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    with runtime.confirmation_lock:
        if kind == "continuity":
            return _update_continuity(runtime, record_id, payload)
        if kind == "npc":
            return _update_npc_profile(runtime, record_id, payload, view=view)
        return _update_world_setting(runtime, record_id, payload, view=view)


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
    *,
    view: str = "general",
) -> dict[str, Any]:
    allowed = _NPC_PUBLIC_FIELDS | (
        _NPC_ADVANCED_DIRECTOR_FIELDS if view == "advanced" else set()
    )
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(
            f"NPC {view} fields only: " + ", ".join(sorted(allowed))
        )
    with runtime.stores.npc_profile() as store:
        current = store.get_by_id(npc_id)
        if current is None:
            raise ValueError(f"NPC profile not found: {npc_id}")
        nullable = _NPC_NULLABLE_FIELDS | (
            _NPC_ADVANCED_NULLABLE_FIELDS if view == "advanced" else set()
        )
        list_fields = _NPC_LIST_FIELDS | (
            _NPC_ADVANCED_LIST_FIELDS if view == "advanced" else set()
        )
        payload = _normalize_payload(
            payload,
            nullable=nullable,
            list_fields=list_fields,
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
        if view == "advanced":
            for field in _NPC_ADVANCED_DIRECTOR_FIELDS:
                if field in payload:
                    setattr(merged, field, payload[field])
        store.upsert(merged)
        saved = store.get_by_id(npc_id)
        if saved is None:
            raise RuntimeError("NPC profile disappeared after save")
    record = {
        "id": saved.npc_id,
        "public": model_data(saved.update_view()),
    }
    if view == "advanced":
        record["director_only"] = {
            key: model_data(getattr(saved, key))
            for key in _NPC_ADVANCED_DIRECTOR_FIELDS
        }
    return record


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


def _update_world_setting(
    runtime: CampaignRuntime,
    setting_id: str,
    payload: dict[str, Any],
    *,
    view: str = "general",
) -> dict[str, Any]:
    allowed = _SETTING_ADVANCED_FIELDS if view == "advanced" else _SETTING_GENERAL_FIELDS
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(
            f"Setting {view} fields only: " + ", ".join(sorted(allowed))
        )
    with runtime.stores.world_setting() as store:
        current = store.get_by_id(setting_id)
        if current is None:
            raise ValueError(f"Setting not found: {setting_id}")
        payload = _normalize_payload(
            payload,
            nullable=set(),
            list_fields=_SETTING_LIST_FIELDS,
        )
        data = current.model_dump()
        data.update(payload)
        data["name"] = str(data["name"]).strip()
        updated = WorldSetting.model_validate(data)
        store.update(updated)
        saved = store.get_by_id(setting_id)
        if saved is None:
            raise RuntimeError("Setting disappeared after save")
    return {
        "id": saved.setting_id,
        "fields": _setting_record(saved, view=view)["fields"],
    }
