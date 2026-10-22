from __future__ import annotations

from pydantic import ValidationError

from typing import Any

from app.core.client_error import ClientValidationError
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
    "entities",
    "location",
}
_CONTINUITY_NULLABLE_FIELDS = {"location"}
_NPC_PUBLIC_FIELDS = {
    "name",
    "role",
    "appearance",
    "personality_trait",
    "known_abilities",
    "faction",
    "last_known_location",
    "attitude_to_player",
}
_NPC_NULLABLE_FIELDS = {
    "role",
    "appearance",
    "personality_trait",
    "faction",
    "last_known_location",
    "attitude_to_player",
}
_NPC_LIST_FIELDS = {"known_abilities"}
_NPC_DIRECTOR_FIELDS = {
    "core_motivation",
    "hidden_secret",
    "conflict_style",
    "social_style",
    "director_notes",
    "source_director_npc_id",
    "source_setting_ids",
    "created_turn",
    "introduced_turn",
    "disclosure_level",
}
_NPC_ADVANCED_DIRECTOR_FIELDS = {
    "core_motivation",
    "hidden_secret",
    "conflict_style",
    "social_style",
    "director_notes",
}
_NPC_ADVANCED_NULLABLE_FIELDS = {
    "core_motivation",
    "hidden_secret",
    "conflict_style",
    "social_style",
    "director_notes",
}
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
                    item for item in value if _list_item_kept(item)
                ]
            else:
                normalized[field] = value
        elif field in nullable and isinstance(value, str) and not value.strip():
            normalized[field] = None
        else:
            normalized[field] = value
    return normalized


def _list_item_kept(item: Any) -> bool:
    """Keep a list item unless it is a blank string or a nameless ability dict."""
    if isinstance(item, dict):
        return bool(str(item.get("name", "")).strip())
    return bool(str(item).strip())


def _maintenance_running(runtime: CampaignRuntime) -> bool:
    counts = runtime.jobs.status_counts()
    return bool(counts.get("queued") or counts.get("running"))


def load_player_records(view: str = "general") -> dict[str, Any]:
    """Player archive projection: continuity + NPC + Setting records."""
    if view not in RECORD_VIEWS:
        raise ClientValidationError(f"unknown records view: {view}")
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        return _load_player_records(runtime, view=view)


def _load_player_records(runtime: CampaignRuntime, *, view: str = "general") -> dict[str, Any]:
    with runtime.uow() as uow:
        with uow.continuity as store:
            continuity_items = store.list_all()
        with uow.npc_profile as store:
            npc_profiles = store.list_all(active_only=False)
        with uow.world_setting as store:
            world_settings = store.list_all()
    return {
        "view": view,
        "maintenance_running": _maintenance_running(runtime),
        "runtime_notices": runtime.history.runtime_notices(),
        "continuity": [
            {
                "id": item.id,
                "status": item.status,
                "created_turn": item.created_turn,
                "updated_turn": item.updated_turn,
                "closed_turn": item.closed_turn,
                "subject": item.subject,
                "premise": item.premise,
                "current_state": item.current_state,
                "current_state_text": item.current_state or "",
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


def load_player_events() -> dict[str, Any]:
    """Return Director Events for the owner's backstage browser."""
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        with runtime.uow() as uow:
            with uow.director_event as store:
                events = store.list_all()
    return {
        "events": [
            {
                "event_id": event.event_id,
                "title": event.title,
                "stage": event.stage,
                "subject": event.subject,
                "intent": event.intent,
                "method": event.method,
                "macro_intent": event.macro_intent,
                "hook_pool": [
                    hook.model_dump(mode="json") for hook in event.hook_pool
                ],
                "actors": [
                    actor.model_dump(mode="json") for actor in event.actors
                ],
                "source_setting_ids": list(event.source_setting_ids),
                "world_impact": (
                    event.world_impact.model_dump(mode="json")
                    if event.world_impact is not None
                    else None
                ),
                "deadline_clock": event.deadline_clock.model_dump(mode="json"),
                "final_world_impact": (
                    event.final_world_impact.model_dump(mode="json")
                    if event.final_world_impact is not None
                    else None
                ),
                "resolved_reason": event.resolved_reason,
                "resolved_turn": event.resolved_turn,
                "created_turn": event.created_turn,
                "updated_turn": event.updated_turn,
                "created_at": event.created_at,
                "updated_at": event.updated_at,
            }
            for event in events
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
    # ``last_scene_*`` 是程序 + evidence 驱动的只读派生态：随 load/update
    # 统一投影给展示层，但绝不并入 update_view（编辑面不可写）。
    record["public"]["last_scene_summary"] = profile.last_scene_summary
    record["public"]["last_scene_turn"] = profile.last_scene_turn
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
        raise ClientValidationError(f"unknown records view: {view}")
    if kind not in {"continuity", "npc", "setting"}:
        raise ClientValidationError(f"unknown record kind: {kind}")
    if not record_id:
        raise ClientValidationError("record_id is required")
    if not isinstance(payload, dict):
        raise ClientValidationError("payload must be an object")
    with runtime.confirmation_lock:
        if kind == "continuity":
            result = _update_continuity(runtime, record_id, payload)
        elif kind == "npc":
            result = _update_npc_profile(runtime, record_id, payload, view=view)
        else:
            result = _update_world_setting(runtime, record_id, payload, view=view)
        runtime.invalidate_generation_cache()
        return result


def _latest_confirmed_turn(runtime: CampaignRuntime) -> int:
    with runtime.uow() as uow:
        with uow.short_term as store:
            entries = store.list_navigation_entries()
    if not entries:
        return 0
    return max(turn_index for turn_index, _user_input in entries)


def _update_continuity(
    runtime: CampaignRuntime,
    item_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with runtime.uow() as uow:
        store = uow.continuity
        current = store.get(item_id)
        if current is None:
            raise ClientValidationError(f"continuity item not found: {item_id}")
        unknown = set(payload) - _CONTINUITY_EDITABLE_FIELDS
        if unknown:
            raise ClientValidationError(
                "continuity edit only supports: "
                + ", ".join(sorted(_CONTINUITY_EDITABLE_FIELDS))
            )
        if current.status != "active":
            raise ClientValidationError("only active continuity items can be edited")
        payload = _normalize_payload(
            payload,
            nullable=_CONTINUITY_NULLABLE_FIELDS,
            list_fields={"entities"},
        )
        turn_index = _latest_confirmed_turn(runtime)
        with store.transaction():
            updated = store.edit_active_metadata(
                item_id,
                subject=payload.get("subject", current.subject),
                entities=payload.get("entities", current.entities),
                location=payload.get("location", current.location),
                deadline=current.deadline,
                turn_index=turn_index,
            )
    return {
        "id": updated.id,
        "status": updated.status,
        "created_turn": updated.created_turn,
        "updated_turn": updated.updated_turn,
        "closed_turn": updated.closed_turn,
        "subject": updated.subject,
        "premise": updated.premise,
        "current_state": updated.current_state,
        "current_state_text": updated.current_state or "",
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
        raise ClientValidationError(
            f"NPC {view} fields only: " + ", ".join(sorted(allowed))
        )
    with runtime.uow() as uow:
        store = uow.npc_profile
        current = store.get_by_id(npc_id)
        if current is None:
            raise ClientValidationError(f"NPC profile not found: {npc_id}")
        nullable = _NPC_NULLABLE_FIELDS | (
            _NPC_ADVANCED_NULLABLE_FIELDS if view == "advanced" else set()
        )
        list_fields = _NPC_LIST_FIELDS
        payload = _normalize_payload(
            payload,
            nullable=nullable,
            list_fields=list_fields,
        )
        update = current.update_view()
        data = update.model_dump()
        for field in _NPC_PUBLIC_FIELDS:
            if field in payload:
                data[field] = payload[field]
        name = data.get("name")
        if name is not None:
            name = str(name).strip()
            if not name:
                raise ClientValidationError("名称不能为空：请补充名称后再保存。")
            data["name"] = name
        data["aliases"] = [
            alias.strip()
            for alias in data.get("aliases", [])
            if alias and alias.strip()
        ]
        try:
            validated = NPCProfileUpdateInput.model_validate(data)
        except ValidationError as exc:
            raise ClientValidationError("人物档案字段格式不合法，请检查名称、列表和字段长度。") from exc
        _validate_npc_name_conflicts(store, npc_id, validated)
        merged = profile_from_update(
            validated,
            npc_id=current.npc_id,
            source_scene_ids=list(current.source_scene_ids),
            updated_at=None,
            active=current.active,
            name_hit_count=current.name_hit_count,
            alias_hit_counts=dict(current.alias_hit_counts),
        )
        _preserve_npc_director_fields(merged, current)
        # ``last_scene_*`` 是程序 + evidence 驱动写入的派生态，普通字段编辑
        # 不得清空它们；UpdateInput 刻意不含这两个字段，因此这里显式保留。
        merged.last_scene_summary = current.last_scene_summary
        merged.last_scene_turn = current.last_scene_turn
        if view == "advanced":
            for field in _NPC_ADVANCED_DIRECTOR_FIELDS:
                if field in payload:
                    setattr(merged, field, payload[field])
        store.upsert(merged)
        saved = store.get_by_id(npc_id)
        if saved is None:
            raise RuntimeError("NPC profile disappeared after save")
    # 统一走 ``_npc_profile_record`` 投影：load 与 update 响应结构一致，
    # 都携带只读 last-scene pair（编辑面仍不可写）。
    return _npc_profile_record(saved, view=view)


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
            raise ClientValidationError(
                "人物名称或别名与其他档案冲突，请更换名称或别名。"
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
        raise ClientValidationError(
            f"Setting {view} fields only: " + ", ".join(sorted(allowed))
        )
    with runtime.uow() as uow:
        store = uow.world_setting
        current = store.get_by_id(setting_id)
        if current is None:
            raise ClientValidationError(f"Setting not found: {setting_id}")
        payload = _normalize_payload(
            payload,
            nullable=set(),
            list_fields=_SETTING_LIST_FIELDS,
        )
        data = current.model_dump()
        data.update(payload)
        data["name"] = str(data["name"]).strip()
        try:
            updated = WorldSetting.model_validate(data)
        except ValidationError as exc:
            raise ClientValidationError("世界设定字段格式不合法，请检查名称、列表和字段长度。") from exc
        store.update(updated)
        saved = store.get_by_id(setting_id)
        if saved is None:
            raise RuntimeError("Setting disappeared after save")
    return {
        "id": saved.setting_id,
        "fields": _setting_record(saved, view=view)["fields"],
    }
