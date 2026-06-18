from dataclasses import dataclass

from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import (
    EntityExtractionResult,
    EntityProfile,
)
from app.domain.entity.extractor import EntityExtractor
from app.domain.entity.store import EntityStore
from app.domain.entity.updater import EntityProfileUpdater, apply_semantic_update
from app.domain.retrieval.schemas import CandidateRetrievalResult


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
    _apply_alias_matches(entity_store, extraction.alias_matches)

    matched_ids = {
        entity.matched_entity_id
        for entity in extraction.retrieved_entities
        if entity.source != "new" and entity.matched_entity_id
    }
    matched_names = {
        entity.matched_entity_name
        for entity in extraction.retrieved_entities
        if entity.source != "new" and entity.matched_entity_name
    }
    relevant_profiles = [
        profile
        for profile in existing_profiles
        if profile.entity_id in matched_ids or profile.name in matched_names
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
        if match.matched_entity_id:
            entity_store.append_alias(match.matched_entity_id, match.new_alias)
            continue
        profile = entity_store.get_by_name(match.matched_entity_name)
        if profile is not None:
            entity_store.append_alias(profile.entity_id, match.new_alias)


def gather_relevant_entity_profiles_for_turn(
    *,
    current_state: CurrentState,
    action_parse_result: ActionParseResult,
    retrieved_memories: CandidateRetrievalResult,
    entity_store: EntityStore,
    limit: int = 20,
) -> list[EntityProfile]:
    names = _gather_names(current_state, action_parse_result, retrieved_memories)
    selected: list[EntityProfile] = []
    seen: set[str] = set()
    for name in names:
        for profile in entity_store.find_by_name_fuzzy(name):
            if profile.entity_id in seen:
                continue
            selected.append(profile)
            seen.add(profile.entity_id)
            break
        if len(selected) >= limit:
            break
    return selected


def _gather_names(
    current_state: CurrentState,
    action_parse_result: ActionParseResult,
    retrieved_memories: CandidateRetrievalResult,
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

    for anchor in action_parse_result.anchors:
        if anchor.kind in {"character", "location", "item", "organization"}:
            names.append(anchor.normalized or anchor.text)
            names.append(anchor.text)

    for candidate in retrieved_memories.candidates:
        names.append(candidate.subject)
        names.extend(candidate.entities)
    for note in retrieved_memories.pending_notes:
        names.extend(note.entities)

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
