from dataclasses import dataclass

from app.core.llm import LLMGateway
from app.domain.action.schemas import Anchor
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import (
    EntityExtractionResult,
    EntityProfile,
)
from app.domain.entity.extractor import EntityExtractor
from app.domain.entity.quality import (
    is_acceptable_alias,
    is_persistent_entity_candidate,
    is_acceptable_retrieved_entity,
)
from app.domain.entity.store import EntityStore
from app.domain.entity.updater import EntityProfileUpdater, apply_semantic_update
from app.domain.scene_summary.schemas import SceneSummary


@dataclass
class UpdateEntityProfilesResult:
    extraction: EntityExtractionResult
    saved_ids: list[str]


def update_entity_profiles_workflow(
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    gateway: LLMGateway,
    entity_store: EntityStore,
    *,
    recent_summaries: list[SceneSummary] | None = None,
    trace_id: str | None = None,
) -> UpdateEntityProfilesResult:
    existing = entity_store.list_all()
    extractor = EntityExtractor(gateway)
    extraction = extractor.extract(
        scene_facts=scene_facts,
        existing_profiles=existing,
        core_memory=core_memory,
        trace_id=trace_id,
    )

    return update_entity_profiles_from_extraction(
        scene_facts=scene_facts,
        current_state=current_state,
        core_memory=core_memory,
        gateway=gateway,
        entity_store=entity_store,
        existing_profiles=existing,
        extraction=extraction,
        recent_summaries=recent_summaries,
        trace_id=trace_id,
    )


def update_entity_profiles_from_extraction(
    *,
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    gateway: LLMGateway,
    entity_store: EntityStore,
    existing_profiles: list[EntityProfile],
    extraction: EntityExtractionResult,
    recent_summaries: list[SceneSummary] | None = None,
    trace_id: str | None = None,
) -> UpdateEntityProfilesResult:
    _record_existing_entity_mentions(entity_store, extraction, existing_profiles)
    existing_profiles = [
        entity_store.get_by_id(profile.entity_id) or profile
        for profile in existing_profiles
    ]
    extraction = _normalize_extraction_matches(extraction, existing_profiles)
    _apply_alias_matches(entity_store, extraction.alias_matches)
    existing_profiles = [
        entity_store.get_by_id(profile.entity_id) or profile
        for profile in existing_profiles
    ]

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
        scene_facts=scene_facts,
        current_state=current_state,
        relevant_profiles=relevant_profiles,
        new_entity_hints=new_entity_hints,
        core_memory=core_memory,
        recent_summaries=recent_summaries,
        trace_id=trace_id,
    )

    saved_ids: list[str] = []
    for profile in updated:
        saved_ids.append(_upsert_with_exact_duplicate_guard(entity_store, profile))
    return UpdateEntityProfilesResult(extraction=extraction, saved_ids=saved_ids)


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


def _normalize_extraction_matches(
    extraction: EntityExtractionResult,
    existing_profiles: list[EntityProfile],
) -> EntityExtractionResult:
    return EntityExtractionResult(
        alias_matches=[
            normalized
            for match in extraction.alias_matches
            if (normalized := _normalize_alias_match(match, existing_profiles)) is not None
            and is_acceptable_alias(normalized)
        ],
        retrieved_entities=[
            normalized
            for entity in extraction.retrieved_entities
            if is_acceptable_retrieved_entity(
                normalized := _normalize_retrieved_entity(entity, existing_profiles)
            )
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
            entity_store.append_alias(
                profile.entity_id,
                match.new_alias,
                initial_hit_count=1,
            )


def _record_existing_entity_mentions(
    entity_store: EntityStore,
    extraction: EntityExtractionResult,
    existing_profiles: list[EntityProfile],
) -> None:
    seen: set[tuple[str, str]] = set()

    for entity in extraction.retrieved_entities:
        if entity.source == "new":
            continue
        profile_index = _resolve_matched_profile_index(
            requested_index=entity.matched_entity_index,
            matched_name=entity.matched_entity_name,
            extracted_text=entity.name,
            entity_type=entity.entity_type,
            existing_profiles=existing_profiles,
        )
        if profile_index is None:
            continue
        profile = existing_profiles[profile_index]
        if not is_persistent_entity_candidate(profile.entity_type, profile.name):
            continue
        surface = str(entity.name).strip()
        key = (profile.entity_id, _normalize_key(surface))
        if surface and key not in seen:
            seen.add(key)
            entity_store.record_mention(profile.entity_id, surface)

    for match in extraction.alias_matches:
        if match.new_alias:
            continue
        profile_index = _resolve_matched_profile_index(
            requested_index=match.matched_entity_index,
            matched_name=match.matched_entity_name,
            extracted_text=match.extracted_text,
            entity_type=match.matched_entity_type,
            existing_profiles=existing_profiles,
        )
        if profile_index is None:
            continue
        profile = existing_profiles[profile_index]
        if not is_persistent_entity_candidate(profile.entity_type, profile.name):
            continue
        surface = str(match.extracted_text).strip()
        key = (profile.entity_id, _normalize_key(surface))
        if surface and key not in seen:
            seen.add(key)
            entity_store.record_mention(profile.entity_id, surface)


def _gather_names(
    current_state: CurrentState,
    anchors: list[Anchor],
) -> list[str]:
    names: list[str] = []
    names.append(current_state.scene.location)
    names.extend(current_state.scene.participant_names)
    names.append(current_state.player.name)

    resources = current_state.resources
    for collection in [
        resources.inventory,
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
