from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.llm import LLMGateway
from app.domain.action.schemas import Anchor
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.backfiller import NPCProfileBackfiller
from app.domain.npc_profile.extractor import NPCProfileExtractor
from app.domain.npc_profile.schemas import NPCExtractionResult, NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.updater import NPCProfileUpdater
from app.domain.scene_summary.schemas import SceneSummary

if TYPE_CHECKING:
    from app.domain.director_event.schemas import DirectorEvent


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
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    gateway: LLMGateway,
    npc_store: NPCProfileStore,
    existing_profiles: list[NPCProfile],
    extraction: NPCExtractionResult,
    director_events: list[DirectorEvent] | None = None,
    recent_summaries: list[SceneSummary] | None = None,
    trace_id: str | None = None,
) -> UpdateNPCProfilesResult:
    updater = NPCProfileUpdater(gateway)
    update_result = updater.update(
        scene_facts=scene_facts,
        current_state=current_state,
        existing_profiles=existing_profiles,
        extraction=extraction,
        core_memory=core_memory,
        director_events=director_events,
        recent_summaries=recent_summaries,
        trace_id=trace_id,
    )

    saved_ids: list[str] = []
    existing_ids = {profile.npc_id for profile in existing_profiles}
    backfiller = NPCProfileBackfiller(gateway)
    for profile in update_result.profiles:
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
                scene_facts=scene_facts,
                current_state=current_state,
                core_memory=core_memory,
                recent_summaries=recent_summaries,
                trace_id=trace_id,
            )
            npc_store.upsert(backfilled)
    for npc_id, surface in update_result.exact_mentions:
        npc_store.record_mention(npc_id, surface)
    for npc_id, alias in update_result.aliases_to_add:
        npc_store.append_alias(npc_id, alias, initial_hit_count=1)
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
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    gateway: LLMGateway,
    npc_store: NPCProfileStore,
    recent_summaries: list[SceneSummary] | None = None,
    director_events: list[DirectorEvent] | None = None,
    trace_id: str | None = None,
) -> UpdateNPCProfilesResult:
    existing = npc_store.list_all()
    extractor = NPCProfileExtractor(gateway)
    extraction = extractor.extract(
        scene_facts=scene_facts,
        trace_id=trace_id,
    )
    return update_npc_profiles_from_extraction(
        scene_facts=scene_facts,
        current_state=current_state,
        core_memory=core_memory,
        gateway=gateway,
        npc_store=npc_store,
        existing_profiles=existing,
        extraction=extraction,
        director_events=director_events,
        recent_summaries=recent_summaries,
        trace_id=trace_id,
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
    names = current_state.scene.participant_names
    return {
        _norm(name)
        for name in names
        if name and name.strip()
    }


def _gather_names(current_state: CurrentState, anchors: list[Anchor]) -> list[str]:
    names: list[str] = []
    names.extend(current_state.scene.participant_names)
    names.append(current_state.player.name)
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
