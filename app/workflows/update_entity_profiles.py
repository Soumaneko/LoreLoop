from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfile
from app.domain.entity.store import EntityStore
from app.domain.entity.updater import EntityProfileUpdater
from app.domain.retrieval.schemas import CandidateRetrievalResult


def update_entity_profiles_workflow(
    scene_text: str,
    current_state: CurrentState,
    gateway: LLMGateway,
    entity_store: EntityStore,
    *,
    trace_id: str | None = None,
) -> list[str]:
    existing = entity_store.list_all()
    updater = EntityProfileUpdater(gateway)
    updated = updater.update(
        scene_text=scene_text,
        current_state=current_state,
        existing_profiles=existing,
        trace_id=trace_id,
    )

    saved_ids: list[str] = []
    for profile in updated:
        saved_ids.append(entity_store.upsert(profile))
    return saved_ids


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
