from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.core.text import normalize_key
from app.domain.confirmed_history import HistoryDocument
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.schemas import (
    HIGH_RISK_LONG_TERM_FIELDS,
    LONG_TERM_FIELD_ORDER,
    TEMPORARY_PROFILE_FIELDS,
    ExistingNPCProfileLongTermPatch,
    ExistingNPCProfileTemporaryPatch,
    KnownAbility,
    LLMKnownAbility,
    NPCExtractionResult,
    NPCMention,
    NPCNewProfileOutput,
    NPCProfile,
    NPCProfileCandidateResult,
    NPCProfileUpdateBatch,
    NPCProfileUpdateInput,
    profile_from_update,
    utc_now,
)
from app.domain.scene_summary.schemas import SceneSummary


@dataclass(frozen=True)
class NPCProfileUpdateWarning:
    npc_name: str
    field_name: str
    code: str
    message: str
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class NPCProfileUpdateResult:
    profiles: list[NPCProfile] = field(default_factory=list)
    exact_mentions: list[tuple[str, str]] = field(default_factory=list)
    aliases_to_add: list[tuple[str, str]] = field(default_factory=list)
    turn_observations: list[tuple[str, str]] = field(default_factory=list)
    long_term_warnings: list[NPCProfileUpdateWarning] = field(default_factory=list)


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
        evidence_documents: dict[str, HistoryDocument] | None = None,
        turn_index: int | None = None,
        recent_summaries: list[SceneSummary] | None = None,
        trace_id: str | None = None,
    ) -> NPCProfileUpdateResult:
        if not scene_facts.strip() or not extraction.mentions:
            return NPCProfileUpdateResult()
        if evidence_documents is not None and turn_index is None:
            raise ValueError(
                "turn_index is required when evidence_documents are provided"
            )

        from app.domain.views import project_current_state, project_scene_summaries

        scene_paragraph_evidence = [
            document
            for document in (evidence_documents or {}).values()
            if document.source_type == "scene_paragraph"
        ]
        valid_evidence = {
            document.document_id: document
            for document in scene_paragraph_evidence
        }

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
                    "scene_paragraph_evidence_json": json.dumps(
                        [
                            {
                                "document_id": document.document_id,
                                "text": document.text,
                            }
                            for document in scene_paragraph_evidence
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
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
                                "last_known_location": None,
                            },
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": schema_json(NPCProfileUpdateBatch),
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
        long_term_warnings: list[NPCProfileUpdateWarning] = []
        profiles, aliases_to_add = _merge_results(
            candidates,
            existing_profiles,
            validated,
            current_state.scene.scene_id,
            valid_evidence=valid_evidence,
            turn_index=turn_index,
            warnings=long_term_warnings,
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
            long_term_warnings=long_term_warnings,
        )


def apply_existing_npc_update(
    target: NPCProfile,
    patch: ExistingNPCProfileTemporaryPatch,
) -> None:
    if patch.last_known_location is not None:
        target.last_known_location = patch.last_known_location
    if patch.attitude_to_player is not None:
        target.attitude_to_player = patch.attitude_to_player
    if patch.current_intent is not None:
        target.current_intent = patch.current_intent


def apply_existing_npc_long_term_update(
    target: NPCProfile,
    patch: ExistingNPCProfileLongTermPatch,
    *,
    valid_evidence: dict[str, HistoryDocument],
    turn_index: int | None,
    warnings: list[NPCProfileUpdateWarning],
) -> None:
    """Apply a long-term patch, gating every high-risk field on its evidence.

    A high-risk field written without a matching ``field_evidence`` entry, or
    citing a document id that is not a confirmed scene paragraph of the current
    turn, is dropped. The gate is structural only: whether the paragraph truly
    supports the field is the LLM's instruction-level responsibility.
    ``known_abilities`` is an append-only delta; it merges new abilities by
    name and never removes existing ones.
    """
    for field_name in LONG_TERM_FIELD_ORDER:
        if field_name == "known_abilities":
            delta = patch.known_abilities
            if delta is None or not delta.add:
                continue
            valid, invalid = _field_evidence_refs(
                patch,
                field_name,
                valid_evidence,
                turn_index=turn_index,
            )
            if invalid:
                _drop_field_warning(
                    target,
                    field_name,
                    invalid,
                    "known_abilities",
                    warnings,
                )
                continue
            if not valid:
                _missing_evidence_warning(
                    target,
                    field_name,
                    warnings,
                )
                continue
            _merge_known_abilities(target, delta.add)
            continue
        value = getattr(patch, field_name)
        if value is None:
            continue
        valid, invalid = _field_evidence_refs(
            patch,
            field_name,
            valid_evidence,
            turn_index=turn_index,
        )
        if invalid:
            _drop_field_warning(
                target,
                field_name,
                invalid,
                str(value),
                warnings,
            )
            continue
        if not valid:
            _missing_evidence_warning(
                target,
                field_name,
                warnings,
            )
            continue
        if value != getattr(target, field_name):
            setattr(target, field_name, value)


def _field_evidence_refs(
    patch: ExistingNPCProfileLongTermPatch,
    field_name: str,
    valid_evidence: dict[str, HistoryDocument],
    *,
    turn_index: int | None,
) -> tuple[list[str], list[str]]:
    """Split the field's cited evidence into (valid, invalid) document ids.

    A citation is invalid when the id is unknown, is not a scene paragraph, or
    belongs to a different turn. The interface guarantees ``turn_index`` is set
    whenever evidence is provided, so the comparison fails closed instead of
    accepting documents from an unknown turn.
    """
    cited = patch.field_evidence.get(field_name)
    if not cited:
        return [], []
    valid: list[str] = []
    invalid: list[str] = []
    for evidence_id in cited:
        document = valid_evidence.get(evidence_id)
        if (
            document is not None
            and document.source_type == "scene_paragraph"
            and document.turn_index == turn_index
        ):
            valid.append(evidence_id)
        else:
            invalid.append(evidence_id)
    return valid, invalid


def _drop_field_warning(
    target: NPCProfile,
    field_name: str,
    invalid: list[str],
    attempted_value: str,
    warnings: list[NPCProfileUpdateWarning],
) -> None:
    warnings.append(NPCProfileUpdateWarning(
        npc_name=target.name,
        field_name=field_name,
        code="long_term_field_evidence_dropped",
        message=(
            "long-term field update dropped: evidence references are not "
            f"confirmed scene paragraphs of this turn: {sorted(set(invalid))}"
        ),
        evidence_ids=tuple(sorted(set(invalid))),
    ))


def _missing_evidence_warning(
    target: NPCProfile,
    field_name: str,
    warnings: list[NPCProfileUpdateWarning],
) -> None:
    warnings.append(NPCProfileUpdateWarning(
        npc_name=target.name,
        field_name=field_name,
        code="long_term_field_evidence_missing",
        message=(
            "long-term field update dropped: field was written without any "
            "field_evidence entry"
        ),
    ))


def _merge_known_abilities(
    target: NPCProfile,
    additions: list[LLMKnownAbility],
) -> None:
    existing = list(target.known_abilities)
    existing_keys = {normalize_key(ability.name) for ability in existing}
    for ability in additions:
        if len(existing) >= 5:
            break
        persisted = KnownAbility.model_validate(ability.model_dump())
        key = normalize_key(persisted.name)
        if not key or key in existing_keys:
            continue
        existing_keys.add(key)
        existing.append(persisted)
    target.known_abilities = existing


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

        key = normalize_key(mention.name)
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
        "last_known_location": profile.last_known_location,
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
                "temporary_patch": None,
                "long_term_patch": None,
                "new_profile": None,
            }))
        elif result.resolution == "new":
            if (
                result.new_profile is None
                or normalize_key(result.new_profile.name) != normalize_key(candidate.name)
            ):
                continue
            valid.append(result.model_copy(update={
                "matched_npc_index": None,
                "persist_alias": False,
                "temporary_patch": None,
                "long_term_patch": None,
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
    *,
    valid_evidence: dict[str, HistoryDocument],
    turn_index: int | None,
    warnings: list[NPCProfileUpdateWarning],
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
                    cast(NPCNewProfileOutput, result.new_profile).to_update_input(),
                    source_scene_ids=[scene_id],
                    updated_at=now,
                )
            )
            continue

        target = (
            candidate.existing_profile
            if result.resolution == "existing"
            else existing_profiles[cast(int, result.matched_npc_index)]
        )
        assert target is not None
        if result.resolution == "alias_match" and result.persist_alias:
            aliases_to_add.append((target.npc_id, candidate.name))
        if result.temporary_patch is None and result.long_term_patch is None:
            continue
        merged = updated_existing.get(target.npc_id) or target.model_copy(deep=True)
        if result.temporary_patch is not None:
            apply_existing_npc_update(merged, result.temporary_patch)
        if result.long_term_patch is not None:
            apply_existing_npc_long_term_update(
                merged,
                result.long_term_patch,
                valid_evidence=valid_evidence,
                turn_index=turn_index,
                warnings=warnings,
            )
        if not _patch_changes_profile(merged, target):
            continue
        if scene_id not in merged.source_scene_ids:
            merged.source_scene_ids.append(scene_id)
        merged.updated_at = now
        updated_existing[target.npc_id] = merged

    return [*updated_existing.values(), *created], list(dict.fromkeys(aliases_to_add))


def _exact_profile_index(name: str, existing_profiles: list[NPCProfile]) -> int | None:
    key = normalize_key(name)
    for index, profile in enumerate(existing_profiles):
        if (
            key == normalize_key(profile.name)
            or key in {normalize_key(alias) for alias in profile.aliases}
            or key in {normalize_key(alias) for alias in profile.transient_aliases}
        ):
            return index
    return None


def _patch_changes_profile(
    merged: NPCProfile,
    original: NPCProfile,
) -> bool:
    """Model-to-model change detection over every updater-modifiable field.

    Compares Pydantic objects directly; never mixes ``model_dump()`` dicts with
    model instances (e.g. ``known_abilities``), which would misjudge no-ops.
    """
    fields = set(TEMPORARY_PROFILE_FIELDS) | set(HIGH_RISK_LONG_TERM_FIELDS)
    return any(
        getattr(merged, field) != getattr(original, field)
        for field in fields
    )


def _append_unique(values: list[str], value: str) -> None:
    clean = str(value).strip()
    if clean and clean not in values:
        values.append(clean)
