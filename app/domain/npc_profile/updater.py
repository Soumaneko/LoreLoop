from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.schemas import (
    NPCProfile,
    NPCProfileIndexedUpdate,
    NPCProfileUpdateBatch,
    NPCProfileUpdateInput,
    RetrievedNPC,
    profile_from_update,
    utc_now,
)
from app.domain.scene_summary.schemas import SceneSummary

if TYPE_CHECKING:
    from app.domain.director_event.schemas import DirectorEvent


class _NPCUpdateCandidate:
    def __init__(
        self,
        *,
        npc_index: int,
        name: str,
        existing_profile: NPCProfile | None = None,
        new_hint: RetrievedNPC | None = None,
    ):
        self.npc_index = npc_index
        self.name = name
        self.existing_profile = existing_profile
        self.new_hint = new_hint


class NPCProfileUpdater:
    def __init__(self, gateway: LLMGateway):
        self._gateway = gateway

    def update(
        self,
        scene_facts: str,
        current_state: CurrentState,
        relevant_profiles: list[NPCProfile],
        new_npc_hints: list[RetrievedNPC] | None = None,
        *,
        core_memory: CoreMemory,
        director_event: DirectorEvent | None = None,
        recent_summaries: list[SceneSummary] | None = None,
        trace_id: str | None = None,
    ) -> list[NPCProfile]:
        if not scene_facts.strip():
            return []

        from app.domain.views import project_current_state, project_scene_summaries

        candidates = _build_update_candidates(relevant_profiles, new_npc_hints or [])

        result = cast(
            NPCProfileUpdateBatch,
            self._gateway.run(
                task="npc_profile_update",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "scene_facts_json": scene_facts,
                    "director_event_identity_json": json.dumps(
                        (
                            {
                                "premise": director_event.premise,
                                "milestones": director_event.milestones,
                            }
                            if director_event is not None
                            else None
                        ),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "current_state_summary_json": project_current_state(current_state),
                    "recent_summaries_json": project_scene_summaries(recent_summaries or []),
                    "npc_candidates_json": json.dumps(
                        [_candidate_for_prompt(candidate) for candidate in candidates],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        NPCProfileUpdateBatch.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=NPCProfileUpdateBatch,
                trace_id=trace_id,
            ),
        )
        validated = _validate_indexed_updates(result.npcs, candidates)
        return self._merge(candidates, validated, current_state.scene.scene_id)

    def _merge(
        self,
        candidates: list[_NPCUpdateCandidate],
        returned: list[NPCProfileIndexedUpdate],
        scene_id: str,
    ) -> list[NPCProfile]:
        result: list[NPCProfile] = []
        now = utc_now()
        for update in returned:
            candidate = candidates[update.npc_index]
            profile_update = update.profile
            existing = candidate.existing_profile
            if existing is not None:
                merged = existing.model_copy(deep=True)
                apply_existing_npc_update(merged, profile_update)
                if scene_id not in merged.source_scene_ids:
                    merged.source_scene_ids.append(scene_id)
                merged.confidence = min(1.0, merged.confidence + 0.05)
                merged.updated_at = now
                result.append(merged)
            else:
                result.append(
                    profile_from_update(
                        profile_update,
                        source_scene_ids=[scene_id],
                        confidence=0.5,
                        updated_at=now,
                    )
                )
        return result


def apply_npc_update(target: NPCProfile, source: NPCProfileUpdateInput) -> None:
    if source.name.strip():
        target.name = source.name
    existing_alias_keys = {_norm(alias) for alias in target.aliases}
    target.aliases = _merge_unique([*target.aliases, *source.aliases])
    for alias in source.aliases:
        clean = str(alias).strip()
        if clean and _norm(clean) not in existing_alias_keys:
            target.alias_hit_counts[clean] = max(target.alias_hit_counts.get(clean, 0), 1)
    if source.public_summary is not None:
        target.public_summary = source.public_summary
    if source.role is not None:
        target.role = source.role
    if source.appearance is not None:
        target.appearance = source.appearance
    if source.personality_traits:
        target.personality_traits = source.personality_traits
    if source.known_abilities:
        target.known_abilities = source.known_abilities
    if source.faction is not None:
        target.faction = source.faction
    if source.description is not None:
        target.description = source.description
    if source.current_location is not None:
        target.current_location = source.current_location
    if source.attitude_to_player is not None:
        target.attitude_to_player = source.attitude_to_player
    if source.current_intent is not None:
        target.current_intent = source.current_intent


def apply_existing_npc_update(target: NPCProfile, source: NPCProfileUpdateInput) -> None:
    if source.public_summary is not None:
        target.public_summary = source.public_summary
    if source.current_location is not None:
        target.current_location = source.current_location
    if source.attitude_to_player is not None:
        target.attitude_to_player = source.attitude_to_player
    if source.current_intent is not None:
        target.current_intent = source.current_intent


def _build_update_candidates(
    relevant_profiles: list[NPCProfile],
    new_npc_hints: list[RetrievedNPC],
) -> list[_NPCUpdateCandidate]:
    candidates: list[_NPCUpdateCandidate] = []
    for index, profile in enumerate(relevant_profiles):
        candidates.append(
            _NPCUpdateCandidate(
                npc_index=index,
                name=profile.name,
                existing_profile=profile,
            )
        )
    offset = len(candidates)
    for index, hint in enumerate(new_npc_hints):
        candidates.append(
            _NPCUpdateCandidate(
                npc_index=offset + index,
                name=hint.name,
                new_hint=hint,
            )
        )
    return candidates


def _candidate_for_prompt(candidate: _NPCUpdateCandidate) -> dict:
    if candidate.existing_profile is not None:
        return {
            "npc_index": candidate.npc_index,
            "source": "existing",
            **candidate.existing_profile.update_view().model_dump(mode="json"),
        }
    return {
        "npc_index": candidate.npc_index,
        "source": "new_hint",
        "name": candidate.name,
        "context_span": candidate.new_hint.context_span if candidate.new_hint else None,
    }


def _validate_indexed_updates(
    returned: list[NPCProfileIndexedUpdate],
    candidates: list[_NPCUpdateCandidate],
) -> list[NPCProfileIndexedUpdate]:
    valid: list[NPCProfileIndexedUpdate] = []
    seen: set[int] = set()
    for update in returned:
        if update.npc_index in seen:
            continue
        if update.npc_index < 0 or update.npc_index >= len(candidates):
            continue
        candidate = candidates[update.npc_index]
        profile = update.profile
        if candidate.existing_profile is not None:
            update = update.model_copy(
                update={
                    "profile": profile.model_copy(
                        update={"name": candidate.existing_profile.name}
                    )
                }
            )
        if candidate.new_hint is not None and _norm(profile.name) != _norm(candidate.name):
            continue
        valid.append(update)
        seen.add(update.npc_index)
    return valid


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


def _norm(value: str) -> str:
    return str(value).strip().casefold()
