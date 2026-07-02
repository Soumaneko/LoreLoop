from dataclasses import dataclass

from app.core.llm import LLMGateway
from app.domain.action.schemas import Anchor
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.backfiller import NPCProfileBackfiller
from app.domain.npc_profile.extractor import NPCProfileExtractor
from app.domain.npc_profile.schemas import (
    NPCExtractionResult,
    NPCProfile,
    RetrievedNPC,
)
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.updater import NPCProfileUpdater
from app.domain.scene_summary.schemas import SceneSummary


@dataclass
class UpdateNPCProfilesResult:
    extraction: NPCExtractionResult
    saved_ids: list[str]


def sync_npc_profile_absence_from_current_state(
    *,
    npc_store: NPCProfileStore,
    current_state: CurrentState,
) -> list[str]:
    matched_names = _gather_current_state_npc_names(current_state)
    saved_ids: list[str] = []
    for profile in npc_store.list_all():
        previous_absent_turns = profile.absent_turns
        if _profile_matches_any_name(profile, matched_names):
            profile.absent_turns = 0
        else:
            profile.absent_turns += 1
        if profile.absent_turns != previous_absent_turns:
            saved_ids.append(npc_store.upsert(profile))
    return saved_ids


def gather_npc_profiles_from_state_and_anchors(
    *,
    current_state: CurrentState,
    anchors: list[Anchor],
    npc_store: NPCProfileStore,
    limit: int = 20,
) -> list[NPCProfile]:
    names = _gather_names(current_state, anchors)
    selected: list[NPCProfile] = []
    seen: set[str] = set()
    for name in names:
        profile = npc_store.get_by_name(name)
        if profile is None or profile.npc_id in seen:
            continue
        selected.append(profile)
        seen.add(profile.npc_id)
        if len(selected) >= limit:
            break
    return selected


def update_npc_profiles_from_extraction(
    *,
    scene_text: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    gateway: LLMGateway,
    npc_store: NPCProfileStore,
    existing_profiles: list[NPCProfile],
    extraction: NPCExtractionResult,
    recent_summaries: list[SceneSummary] | None = None,
    trace_id: str | None = None,
) -> UpdateNPCProfilesResult:
    _record_existing_npc_mentions(npc_store, extraction, existing_profiles)
    existing_profiles = [
        npc_store.get_by_id(profile.npc_id) or profile
        for profile in existing_profiles
    ]
    extraction = _normalize_extraction_matches(extraction, existing_profiles)
    _apply_alias_matches(npc_store, extraction.alias_matches, existing_profiles)
    existing_profiles = [
        npc_store.get_by_id(profile.npc_id) or profile
        for profile in existing_profiles
    ]

    profiles_by_index = {
        index: profile
        for index, profile in enumerate(existing_profiles)
    }
    matched_indices = {
        npc.matched_npc_index
        for npc in extraction.retrieved_npcs
        if npc.source != "new" and npc.matched_npc_index is not None
    }
    matched_names = {
        npc.matched_npc_name
        for npc in extraction.retrieved_npcs
        if npc.source != "new" and npc.matched_npc_name
    }
    relevant_profiles = [
        profile
        for index, profile in profiles_by_index.items()
        if index in matched_indices or profile.name in matched_names
    ]
    new_npc_hints = [
        npc
        for npc in extraction.retrieved_npcs
        if npc.source == "new"
    ]

    updater = NPCProfileUpdater(gateway)
    updated = updater.update(
        scene_text=scene_text,
        current_state=current_state,
        relevant_profiles=relevant_profiles,
        new_npc_hints=new_npc_hints,
        core_memory=core_memory,
        recent_summaries=recent_summaries,
        trace_id=trace_id,
    )

    saved_ids: list[str] = []
    existing_ids = {profile.npc_id for profile in existing_profiles}
    backfiller = NPCProfileBackfiller(gateway)
    for profile in updated:
        saved_id = npc_store.upsert(profile)
        saved_ids.append(saved_id)
        if _should_backfill_new_npc(profile, existing_ids):
            refreshed = npc_store.get_by_id(saved_id) or profile
            backfilled = backfiller.backfill(
                profile=refreshed,
                existing_profiles=[
                    npc
                    for npc in npc_store.list_all()
                    if npc.npc_id != saved_id
                ],
                scene_text=scene_text,
                current_state=current_state,
                core_memory=core_memory,
                recent_summaries=recent_summaries,
                trace_id=trace_id,
            )
            npc_store.upsert(backfilled)
    return UpdateNPCProfilesResult(extraction=extraction, saved_ids=saved_ids)


def _should_backfill_new_npc(profile: NPCProfile, existing_ids: set[str]) -> bool:
    if profile.npc_id in existing_ids:
        return False
    if profile.source_director_npc_id:
        return False
    return not all(
        [
            profile.core_motivation,
            profile.hidden_secret,
            profile.conflict_style,
            profile.social_style,
        ]
    )


def update_npc_profiles_workflow(
    *,
    scene_text: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    gateway: LLMGateway,
    npc_store: NPCProfileStore,
    recent_summaries: list[SceneSummary] | None = None,
    trace_id: str | None = None,
) -> UpdateNPCProfilesResult:
    existing = npc_store.list_all()
    extractor = NPCProfileExtractor(gateway)
    extraction = extractor.extract(
        scene_text=scene_text,
        existing_profiles=existing,
        core_memory=core_memory,
        trace_id=trace_id,
    )
    return update_npc_profiles_from_extraction(
        scene_text=scene_text,
        current_state=current_state,
        core_memory=core_memory,
        gateway=gateway,
        npc_store=npc_store,
        existing_profiles=existing,
        extraction=extraction,
        recent_summaries=recent_summaries,
        trace_id=trace_id,
    )


def _normalize_extraction_matches(
    extraction: NPCExtractionResult,
    existing_profiles: list[NPCProfile],
) -> NPCExtractionResult:
    return NPCExtractionResult(
        alias_matches=[
            normalized
            for match in extraction.alias_matches
            if (normalized := _normalize_alias_match(match, existing_profiles)) is not None
        ],
        retrieved_npcs=[
            normalized
            for npc in extraction.retrieved_npcs
            if (normalized := _normalize_retrieved_npc(npc, existing_profiles)) is not None
        ],
    )


def _normalize_alias_match(match, existing_profiles: list[NPCProfile]):
    profile_index = _resolve_matched_profile_index(
        requested_index=match.matched_npc_index,
        matched_name=match.matched_npc_name,
        extracted_text=match.extracted_text,
        existing_profiles=existing_profiles,
    )
    if profile_index is None:
        return None
    profile = existing_profiles[profile_index]
    return match.model_copy(
        update={
            "matched_npc_index": profile_index,
            "matched_npc_name": profile.name,
        }
    )


def _normalize_retrieved_npc(
    npc: RetrievedNPC,
    existing_profiles: list[NPCProfile],
):
    if npc.source == "new":
        return npc.model_copy(update={"matched_npc_index": None, "matched_npc_name": None})

    profile_index = _resolve_matched_profile_index(
        requested_index=npc.matched_npc_index,
        matched_name=npc.matched_npc_name,
        extracted_text=npc.name,
        existing_profiles=existing_profiles,
    )
    if profile_index is None:
        return npc.model_copy(
            update={
                "source": "new",
                "matched_npc_index": None,
                "matched_npc_name": None,
            }
        )
    profile = existing_profiles[profile_index]
    return npc.model_copy(
        update={
            "name": profile.name,
            "matched_npc_index": profile_index,
            "matched_npc_name": profile.name,
        }
    )


def _resolve_matched_profile_index(
    *,
    requested_index: int | None,
    matched_name: str | None,
    extracted_text: str | None,
    existing_profiles: list[NPCProfile],
) -> int | None:
    if requested_index is not None and 0 <= requested_index < len(existing_profiles):
        profile = existing_profiles[requested_index]
        if _matches_profile_name_or_alias(matched_name, profile) or _matches_profile_name_or_alias(extracted_text, profile):
            return requested_index

    for value in (matched_name, extracted_text):
        if not value:
            continue
        for index, profile in enumerate(existing_profiles):
            if _matches_profile_name_or_alias(value, profile):
                return index
    return None


def _record_existing_npc_mentions(
    npc_store: NPCProfileStore,
    extraction: NPCExtractionResult,
    existing_profiles: list[NPCProfile],
) -> None:
    for npc in extraction.retrieved_npcs:
        if npc.source == "new":
            continue
        index = _resolve_matched_profile_index(
            requested_index=npc.matched_npc_index,
            matched_name=npc.matched_npc_name,
            extracted_text=npc.name,
            existing_profiles=existing_profiles,
        )
        if index is None:
            continue
        npc_store.record_mention(existing_profiles[index].npc_id, npc.name)


def _apply_alias_matches(
    npc_store: NPCProfileStore,
    alias_matches: list,
    existing_profiles: list[NPCProfile],
) -> None:
    for match in alias_matches:
        if match.matched_npc_index is None or not match.new_alias:
            continue
        if 0 <= match.matched_npc_index < len(existing_profiles):
            npc_store.append_alias(
                existing_profiles[match.matched_npc_index].npc_id,
                match.new_alias,
                initial_hit_count=1,
            )


def _matches_profile_name_or_alias(value: str | None, profile: NPCProfile) -> bool:
    key = _norm(value)
    if not key:
        return False
    return key == _norm(profile.name) or key in {_norm(alias) for alias in profile.aliases}


def _profile_matches_any_name(profile: NPCProfile, names: set[str]) -> bool:
    variants = {_norm(profile.name), *[_norm(alias) for alias in profile.aliases]}
    variants.discard("")
    return bool(variants & names)


def _gather_current_state_npc_names(current_state: CurrentState) -> set[str]:
    names = [
        *current_state.scene.participants,
        *[
            npc.name
            for npc in current_state.npcs
            if npc.absent_turns == 0
        ],
    ]
    return {
        _norm(name)
        for name in names
        if name and name.strip()
    }


def _gather_names(current_state: CurrentState, anchors: list[Anchor]) -> list[str]:
    names: list[str] = []
    names.extend(current_state.scene.participants)
    names.append(current_state.player.name)
    for npc in current_state.npcs:
        names.append(npc.name)
    for anchor in anchors:
        if anchor.kind == "character":
            names.append(anchor.normalized or anchor.text)
            names.append(anchor.text)
    return _merge_unique(names)


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


def _norm(value: str | None) -> str:
    return str(value or "").strip().casefold()
