from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.schemas import (
    ExistingNPCProfilePatch,
    NPCExtractionResult,
    NPCMention,
    NPCProfile,
    NPCProfileCandidateResult,
    NPCProfileUpdateBatch,
    NPCProfileUpdateInput,
    profile_from_update,
    utc_now,
)
from app.domain.scene_summary.schemas import SceneSummary


@dataclass
class NPCProfileUpdateResult:
    profiles: list[NPCProfile] = field(default_factory=list)
    exact_mentions: list[tuple[str, str]] = field(default_factory=list)
    aliases_to_add: list[tuple[str, str]] = field(default_factory=list)
    turn_observations: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _NPCUpdateCandidate:
    candidate_index: int
    name: str
    context_spans: list[str]
    mention_names: list[str] = field(default_factory=list)
    existing_profile: NPCProfile | None = None
    existing_profile_index: int | None = None


class NPCProfileUpdater:
    def __init__(self, gateway: LLMGateway):
        self._gateway = gateway

    def update(
        self,
        scene_facts: str,
        current_state: CurrentState,
        existing_profiles: list[NPCProfile],
        extraction: NPCExtractionResult,
        *,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary] | None = None,
        trace_id: str | None = None,
    ) -> NPCProfileUpdateResult:
        if not scene_facts.strip() or not extraction.mentions:
            return NPCProfileUpdateResult()

        from app.domain.views import project_current_state, project_scene_summaries

        candidates = _build_update_candidates(existing_profiles, extraction.mentions)
        exact_mentions = [
            (candidate.existing_profile.npc_id, mention_name)
            for candidate in candidates
            if candidate.existing_profile is not None
            for mention_name in candidate.mention_names
        ]

        result = cast(
            NPCProfileUpdateBatch,
            self._gateway.run(
                task="npc_profile_update",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "scene_facts_json": scene_facts,
                    "current_state_summary_json": project_current_state(current_state),
                    "recent_summaries_json": project_scene_summaries(recent_summaries or []),
                    "npc_candidates_json": json.dumps(
                        [_candidate_for_prompt(candidate) for candidate in candidates],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "existing_npc_options_json": json.dumps(
                        [
                            *(_existing_option_for_prompt(i, p) for i, p in enumerate(existing_profiles)),
                            {
                                "npc_index": len(existing_profiles),
                                "name": current_state.player.name,
                                "aliases": [],
                                "role": None,
                                "public_summary": None,
                                "appearance": None,
                                "faction": None,
                                "current_location": None,
                            },
                        ],
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
        validated = _validate_candidate_results(
            result.results,
            candidates,
            existing_profiles,
        )
        profiles, aliases_to_add = _merge_results(
            candidates,
            existing_profiles,
            validated,
            current_state.scene.scene_id,
        )
        turn_observations = [
            (candidates[result.candidate_index].name, result.turn_observation)
            for result in validated
            if result.turn_observation
        ]
        return NPCProfileUpdateResult(
            profiles=profiles,
            exact_mentions=exact_mentions,
            aliases_to_add=aliases_to_add,
            turn_observations=turn_observations,
        )


def apply_existing_npc_update(
    target: NPCProfile,
    patch: ExistingNPCProfilePatch,
) -> None:
    if patch.public_summary is not None:
        target.public_summary = patch.public_summary
    if patch.current_location is not None:
        target.current_location = patch.current_location
    if patch.attitude_to_player is not None:
        target.attitude_to_player = patch.attitude_to_player
    if patch.current_intent is not None:
        target.current_intent = patch.current_intent


def _build_update_candidates(
    existing_profiles: list[NPCProfile],
    mentions: list[NPCMention],
) -> list[_NPCUpdateCandidate]:
    exact_by_profile_id: dict[str, _NPCUpdateCandidate] = {}
    unresolved_by_name: dict[str, _NPCUpdateCandidate] = {}
    ordered: list[_NPCUpdateCandidate] = []

    for mention in mentions:
        profile_index = _exact_profile_index(mention.name, existing_profiles)
        if profile_index is not None:
            profile = existing_profiles[profile_index]
            candidate = exact_by_profile_id.get(profile.npc_id)
            if candidate is None:
                candidate = _NPCUpdateCandidate(
                    candidate_index=len(ordered),
                    name=mention.name,
                    context_spans=[],
                    existing_profile=profile,
                    existing_profile_index=profile_index,
                )
                exact_by_profile_id[profile.npc_id] = candidate
                ordered.append(candidate)
            _append_unique(candidate.context_spans, mention.context_span)
            _append_unique(candidate.mention_names, mention.name)
            continue

        key = _norm(mention.name)
        candidate = unresolved_by_name.get(key)
        if candidate is None:
            candidate = _NPCUpdateCandidate(
                candidate_index=len(ordered),
                name=mention.name,
                context_spans=[],
            )
            unresolved_by_name[key] = candidate
            ordered.append(candidate)
        _append_unique(candidate.context_spans, mention.context_span)

    return ordered


def _candidate_for_prompt(candidate: _NPCUpdateCandidate) -> dict:
    if candidate.existing_profile is not None:
        return {
            "candidate_index": candidate.candidate_index,
            "source": "existing",
            "mention_names": candidate.mention_names,
            "context_spans": candidate.context_spans,
            "matched_npc_index": candidate.existing_profile_index,
            **candidate.existing_profile.update_view().model_dump(mode="json"),
        }
    return {
        "candidate_index": candidate.candidate_index,
        "source": "unresolved",
        "name": candidate.name,
        "context_spans": candidate.context_spans,
    }


def _existing_option_for_prompt(index: int, profile: NPCProfile) -> dict:
    return {
        "npc_index": index,
        "name": profile.name,
        "aliases": [
            *profile.aliases,
            *list(profile.transient_aliases.keys()),
        ],
        "role": profile.role,
        "public_summary": profile.public_summary,
        "appearance": profile.appearance,
        "faction": profile.faction,
        "current_location": profile.current_location,
    }


def _validate_candidate_results(
    returned: list[NPCProfileCandidateResult],
    candidates: list[_NPCUpdateCandidate],
    existing_profiles: list[NPCProfile],
) -> list[NPCProfileCandidateResult]:
    valid: list[NPCProfileCandidateResult] = []
    seen: set[int] = set()
    for result in returned:
        if result.candidate_index in seen:
            continue
        if result.candidate_index < 0 or result.candidate_index >= len(candidates):
            continue
        candidate = candidates[result.candidate_index]

        if candidate.existing_profile is not None:
            if result.resolution != "existing":
                continue
            valid.append(result.model_copy(update={
                "matched_npc_index": candidate.existing_profile_index,
                "persist_alias": False,
                "new_profile": None,
            }))
            seen.add(result.candidate_index)
            continue

        if result.resolution == "ignore":
            valid.append(result.model_copy(update={
                "matched_npc_index": None,
                "persist_alias": False,
                "profile_patch": None,
                "new_profile": None,
            }))
        elif result.resolution == "new":
            if (
                result.new_profile is None
                or _norm(result.new_profile.name) != _norm(candidate.name)
            ):
                continue
            valid.append(result.model_copy(update={
                "matched_npc_index": None,
                "persist_alias": False,
                "profile_patch": None,
            }))
        elif result.resolution == "alias_match":
            index = result.matched_npc_index
            if index is None or index < 0 or index >= len(existing_profiles):
                continue
            valid.append(result.model_copy(update={"new_profile": None}))
        else:
            continue
        seen.add(result.candidate_index)
    return valid


def _merge_results(
    candidates: list[_NPCUpdateCandidate],
    existing_profiles: list[NPCProfile],
    returned: list[NPCProfileCandidateResult],
    scene_id: str,
) -> tuple[list[NPCProfile], list[tuple[str, str]]]:
    updated_existing: dict[str, NPCProfile] = {}
    created: list[NPCProfile] = []
    aliases_to_add: list[tuple[str, str]] = []
    now = utc_now()

    for result in returned:
        candidate = candidates[result.candidate_index]
        if result.resolution == "ignore":
            continue
        if result.resolution == "new":
            created.append(
                profile_from_update(
                    cast(NPCProfileUpdateInput, result.new_profile),
                    source_scene_ids=[scene_id],
                    confidence=0.5,
                    updated_at=now,
                )
            )
            continue

        target = (
            candidate.existing_profile
            if result.resolution == "existing"
            else existing_profiles[cast(int, result.matched_npc_index)]
        )
        if result.resolution == "alias_match" and result.persist_alias:
            aliases_to_add.append((target.npc_id, candidate.name))
        if result.profile_patch is None:
            continue
        merged = updated_existing.get(target.npc_id) or target.model_copy(deep=True)
        if not _patch_changes_profile(merged, result.profile_patch):
            continue
        apply_existing_npc_update(merged, result.profile_patch)
        if scene_id not in merged.source_scene_ids:
            merged.source_scene_ids.append(scene_id)
        merged.confidence = min(1.0, merged.confidence + 0.05)
        merged.updated_at = now
        updated_existing[target.npc_id] = merged

    return [*updated_existing.values(), *created], list(dict.fromkeys(aliases_to_add))


def _exact_profile_index(name: str, existing_profiles: list[NPCProfile]) -> int | None:
    key = _norm(name)
    for index, profile in enumerate(existing_profiles):
        if (
            key == _norm(profile.name)
            or key in {_norm(alias) for alias in profile.aliases}
            or key in {_norm(alias) for alias in profile.transient_aliases}
        ):
            return index
    return None


def _patch_changes_profile(
    profile: NPCProfile,
    patch: ExistingNPCProfilePatch,
) -> bool:
    return any(
        value is not None and value != getattr(profile, field_name)
        for field_name, value in patch.model_dump().items()
    )


def _append_unique(values: list[str], value: str) -> None:
    clean = str(value).strip()
    if clean and clean not in values:
        values.append(clean)


def _norm(value: str) -> str:
    return str(value).strip().casefold()
