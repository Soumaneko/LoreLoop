from dataclasses import dataclass

from app.core.llm import LLMGateway
from app.domain.action.schemas import Anchor
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import (
    EntityExtractionResult,
    EntityProfile,
)
from app.domain.entity.extractor import EntityExtractor
from app.domain.entity.store import EntityStore
from app.domain.entity.updater import EntityProfileUpdater, apply_semantic_update


@dataclass
class UpdateEntityProfilesResult:
    extraction: EntityExtractionResult
    saved_ids: list[str]


def update_entity_profiles_workflow(
    scene_text: str,
    current_state: CurrentState,
    gateway: LLMGateway,
    entity_store: EntityStore,
    *,
    trace_id: str | None = None,
) -> UpdateEntityProfilesResult:
    existing = entity_store.list_all()
    extractor = EntityExtractor(gateway)
    extraction = extractor.extract(
        scene_text=scene_text,
        existing_profiles=existing,
        trace_id=trace_id,
    )

    return update_entity_profiles_from_extraction(
        scene_text=scene_text,
        current_state=current_state,
        gateway=gateway,
        entity_store=entity_store,
        existing_profiles=existing,
        extraction=extraction,
        trace_id=trace_id,
    )


def update_entity_profiles_from_extraction(
    *,
    scene_text: str,
    current_state: CurrentState,
    gateway: LLMGateway,
    entity_store: EntityStore,
    existing_profiles: list[EntityProfile],
    extraction: EntityExtractionResult,
    trace_id: str | None = None,
) -> UpdateEntityProfilesResult:
    extraction = _normalize_extraction_matches(extraction, existing_profiles)
    _apply_alias_matches(entity_store, extraction.alias_matches)

    profiles_by_index = {
        index: profile
        for index, profile in enumerate(existing_profiles)
    }
    matched_indices = {
        entity.matched_entity_index
        for entity in extraction.retrieved_entities
        if entity.source != "new" and entity.matched_entity_index is not None
    }
    matched_names = {
        entity.matched_entity_name
        for entity in extraction.retrieved_entities
        if entity.source != "new" and entity.matched_entity_name
    }
    relevant_profiles = [
        profile
        for index, profile in profiles_by_index.items()
        if index in matched_indices or profile.name in matched_names
    ]
    new_entity_hints = [
        entity
        for entity in extraction.retrieved_entities
        if entity.source == "new"
    ]

    updater = EntityProfileUpdater(gateway)
    updated = updater.update(
        scene_text=scene_text,
        current_state=current_state,
        relevant_profiles=relevant_profiles,
        new_entity_hints=new_entity_hints,
        trace_id=trace_id,
    )

    saved_ids: list[str] = []
    for profile in updated:
        saved_ids.append(_upsert_with_exact_duplicate_guard(entity_store, profile))
    return UpdateEntityProfilesResult(extraction=extraction, saved_ids=saved_ids)


def _normalize_extraction_matches(
    extraction: EntityExtractionResult,
    existing_profiles: list[EntityProfile],
) -> EntityExtractionResult:
    return EntityExtractionResult(
        alias_matches=[
            normalized
            for match in extraction.alias_matches
            if (normalized := _normalize_alias_match(match, existing_profiles)) is not None
        ],
        retrieved_entities=[
            _normalize_retrieved_entity(entity, existing_profiles)
            for entity in extraction.retrieved_entities
        ],
    )


def _normalize_alias_match(match, existing_profiles: list[EntityProfile]):
    profile_index = _resolve_matched_profile_index(
        requested_index=match.matched_entity_index,
        matched_name=match.matched_entity_name,
        extracted_text=match.extracted_text,
        entity_type=match.matched_entity_type,
        existing_profiles=existing_profiles,
    )
    if profile_index is None:
        return None

    profile = existing_profiles[profile_index]
    return match.model_copy(update={
        "matched_entity_index": profile_index,
        "matched_entity_name": profile.name,
        "matched_entity_type": profile.entity_type,
    })


def _normalize_retrieved_entity(
    entity,
    existing_profiles: list[EntityProfile],
):
    if entity.source == "new":
        return entity.model_copy(update={
            "matched_entity_index": None,
            "matched_entity_name": None,
        })

    profile_index = _resolve_matched_profile_index(
        requested_index=entity.matched_entity_index,
        matched_name=entity.matched_entity_name,
        extracted_text=entity.name,
        entity_type=entity.entity_type,
        existing_profiles=existing_profiles,
    )
    if profile_index is None:
        return entity.model_copy(update={
            "source": "new",
            "matched_entity_index": None,
            "matched_entity_name": None,
        })

    profile = existing_profiles[profile_index]
    return entity.model_copy(update={
        "name": profile.name,
        "entity_type": profile.entity_type,
        "matched_entity_index": profile_index,
        "matched_entity_name": profile.name,
    })


def _resolve_matched_profile_index(
    *,
    requested_index: int | None,
    matched_name: str | None,
    extracted_text: str | None,
    entity_type: str | None,
    existing_profiles: list[EntityProfile],
) -> int | None:
    if requested_index is not None and 0 <= requested_index < len(existing_profiles):
        profile = existing_profiles[requested_index]
        if _matches_profile_name_or_alias(matched_name, profile):
            return requested_index

    for candidate in [matched_name, extracted_text]:
        profile_index = _find_profile_index_by_name_or_alias(
            candidate,
            entity_type=entity_type,
            existing_profiles=existing_profiles,
        )
        if profile_index is not None:
            return profile_index

    return None


def _find_profile_index_by_name_or_alias(
    value: str | None,
    *,
    entity_type: str | None,
    existing_profiles: list[EntityProfile],
) -> int | None:
    key = _normalize_key(value)
    if not key:
        return None
    for index, profile in enumerate(existing_profiles):
        if profile.entity_type == entity_type and key in _profile_name_alias_keys(profile):
            return index
    for index, profile in enumerate(existing_profiles):
        if key in _profile_name_alias_keys(profile):
            return index
    return None


def _matches_profile_name_or_alias(value: str | None, profile: EntityProfile) -> bool:
    key = _normalize_key(value)
    return bool(key and key in _profile_name_alias_keys(profile))


def _profile_name_alias_keys(profile: EntityProfile) -> set[str]:
    return {
        key
        for value in [profile.name, *profile.aliases]
        if (key := _normalize_key(value))
    }


def _normalize_key(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _upsert_with_exact_duplicate_guard(
    entity_store: EntityStore,
    profile: EntityProfile,
) -> str:
    existing = _find_exact_existing_in_store(entity_store, profile)
    if existing is None or existing.entity_id == profile.entity_id:
        return entity_store.upsert(profile)

    merged = existing.model_copy(deep=True)
    apply_semantic_update(merged, profile.semantic_view())
    merged.source_scene_ids = _merge_unique([
        *existing.source_scene_ids,
        *profile.source_scene_ids,
    ])
    merged.source_memory_ids = _merge_unique([
        *existing.source_memory_ids,
        *profile.source_memory_ids,
    ])
    merged.confidence = max(existing.confidence, profile.confidence)
    return entity_store.upsert(merged)


def _find_exact_existing_in_store(
    entity_store: EntityStore,
    profile: EntityProfile,
) -> EntityProfile | None:
    for name in _merge_unique([profile.name, *profile.aliases]):
        existing = entity_store.get_by_name(name)
        if existing is not None and existing.entity_type == profile.entity_type:
            return existing
    return None


def _apply_alias_matches(entity_store: EntityStore, alias_matches) -> None:
    for match in alias_matches:
        if not match.new_alias:
            continue
        profile = entity_store.get_by_name(match.matched_entity_name)
        if profile is not None:
            entity_store.append_alias(profile.entity_id, match.new_alias)


def gather_entity_profiles_from_state_and_anchors(
    *,
    current_state: CurrentState,
    anchors: list[Anchor],
    entity_store: EntityStore,
    limit: int = 20,
) -> list[EntityProfile]:
    names = _gather_names(current_state, anchors)
    selected: list[EntityProfile] = []
    seen: set[str] = set()
    for name in names:
        profile = entity_store.get_by_name(name)
        if profile is None or profile.entity_id in seen:
            continue
        selected.append(profile)
        seen.add(profile.entity_id)
        if len(selected) >= limit:
            break
    return selected


def _gather_names(
    current_state: CurrentState,
    anchors: list[Anchor],
) -> list[str]:
    names: list[str] = []
    names.append(current_state.scene.location)
    names.extend(current_state.scene.participants)
    names.append(current_state.player.name)
    for npc in current_state.npcs:
        names.append(npc.name)
        if npc.location:
            names.append(npc.location)

    resources = current_state.resources
    for collection in [
        resources.inventory,
        resources.key_items,
        resources.fixed_assets,
        resources.temporary_assets,
    ]:
        for item in collection:
            names.append(_resource_name(item))

    for anchor in anchors:
        if anchor.kind in {"character", "location", "item", "organization"}:
            names.append(anchor.normalized or anchor.text)
            names.append(anchor.text)

    return _unique_nonempty(names)


def _resource_name(item) -> str:
    if isinstance(item, str):
        return item
    if hasattr(item, "name"):
        return str(item.name)
    if isinstance(item, dict):
        return str(item.get("name", ""))
    return str(item)


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value).strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def _merge_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        clean = str(value).strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            merged.append(clean)
    return merged
