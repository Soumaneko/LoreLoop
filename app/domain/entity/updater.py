import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.entity.schemas import (
    EntityProfile,
    EntityProfileIndexedUpdate,
    EntityProfileUpdateBatch,
    EntityProfileUpdateInput,
    RetrievedEntity,
    profile_from_semantic,
    utc_now,
)
from app.domain.entity.quality import is_acceptable_profile_update


class _EntityUpdateCandidate:
    def __init__(
        self,
        *,
        entity_index: int,
        entity_type: str,
        name: str,
        existing_profile: EntityProfile | None = None,
        new_hint: RetrievedEntity | None = None,
    ):
        self.entity_index = entity_index
        self.entity_type = entity_type
        self.name = name
        self.existing_profile = existing_profile
        self.new_hint = new_hint


class EntityProfileUpdater:
    def __init__(self, gateway: LLMGateway):
        self._gateway = gateway

    def update(
        self,
        scene_text: str,
        current_state: CurrentState,
        relevant_profiles: list[EntityProfile],
        new_entity_hints: list[RetrievedEntity] | None = None,
        *,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary] | None = None,
        trace_id: str | None = None,
    ) -> list[EntityProfile]:
        if not scene_text.strip():
            return []

        from app.domain.views import project_current_state, project_scene_summaries

        candidates = _build_update_candidates(relevant_profiles, new_entity_hints or [])

        result = cast(
            EntityProfileUpdateBatch,
            self._gateway.run(
                task="entity_profile_update",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "scene_text": scene_text,
                    "current_state_summary_json": project_current_state(current_state),
                    "recent_summaries_json": project_scene_summaries(recent_summaries or []),
                    "entity_candidates_json": json.dumps(
                        [_candidate_for_prompt(candidate) for candidate in candidates],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        EntityProfileUpdateBatch.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=EntityProfileUpdateBatch,
                trace_id=trace_id,
            ),
        )
        validated = _validate_indexed_updates(result.entities, candidates)
        return self._merge(candidates, validated, current_state.scene.scene_id)

    def _merge(
        self,
        candidates: list[_EntityUpdateCandidate],
        returned: list[EntityProfileIndexedUpdate],
        scene_id: str,
    ) -> list[EntityProfile]:
        result: list[EntityProfile] = []
        now = utc_now()
        for update in returned:
            candidate = candidates[update.entity_index]
            profile_update = update.profile
            existing = candidate.existing_profile
            if existing is not None:
                merged = existing.model_copy(deep=True)
                apply_semantic_update(merged, profile_update)
                if scene_id not in merged.source_scene_ids:
                    merged.source_scene_ids.append(scene_id)
                merged.confidence = min(1.0, merged.confidence + 0.05)
                merged.updated_at = now
                result.append(merged)
            else:
                result.append(
                    profile_from_semantic(
                        profile_update,
                        source_scene_ids=[scene_id],
                        confidence=0.5,
                        updated_at=now,
                    )
                )
        return result


def _build_update_candidates(
    relevant_profiles: list[EntityProfile],
    new_entity_hints: list[RetrievedEntity],
) -> list[_EntityUpdateCandidate]:
    candidates: list[_EntityUpdateCandidate] = []
    for index, profile in enumerate(relevant_profiles):
        candidates.append(
            _EntityUpdateCandidate(
                entity_index=index,
                entity_type=profile.entity_type,
                name=profile.name,
                existing_profile=profile,
            )
        )
    offset = len(candidates)
    for index, hint in enumerate(new_entity_hints):
        candidates.append(
            _EntityUpdateCandidate(
                entity_index=offset + index,
                entity_type=hint.entity_type,
                name=hint.name,
                new_hint=hint,
            )
        )
    return candidates


def _candidate_for_prompt(candidate: _EntityUpdateCandidate) -> dict:
    if candidate.existing_profile is not None:
        return {
            "entity_index": candidate.entity_index,
            "source": "existing",
            **candidate.existing_profile.semantic_view().model_dump(mode="json"),
        }
    return {
        "entity_index": candidate.entity_index,
        "source": "new_hint",
        "name": candidate.name,
        "entity_type": candidate.entity_type,
        "context_span": candidate.new_hint.context_span if candidate.new_hint else None,
    }


def _validate_indexed_updates(
    returned: list[EntityProfileIndexedUpdate],
    candidates: list[_EntityUpdateCandidate],
) -> list[EntityProfileIndexedUpdate]:
    valid: list[EntityProfileIndexedUpdate] = []
    seen: set[int] = set()
    for update in returned:
        if update.entity_index in seen:
            continue
        if update.entity_index < 0 or update.entity_index >= len(candidates):
            continue
        candidate = candidates[update.entity_index]
        profile = update.profile
        if profile.entity_type != candidate.entity_type:
            continue
        if (
            candidate.existing_profile is not None
            and _norm(profile.name)
            not in {
                _norm(candidate.existing_profile.name),
                *[_norm(alias) for alias in candidate.existing_profile.aliases],
            }
        ):
            continue
        if not is_acceptable_profile_update(profile):
            continue
        if candidate.new_hint is not None and _norm(profile.name) != _norm(candidate.name):
            continue
        valid.append(update)
        seen.add(update.entity_index)
    return valid


def apply_semantic_update(
    target: EntityProfile,
    source: EntityProfileUpdateInput,
) -> None:
    target.entity_type = source.entity_type
    if source.name.strip():
        target.name = source.name
    existing_alias_keys = {_norm(alias) for alias in target.aliases}
    target.aliases = _merge_unique([*target.aliases, *source.aliases])
    for alias in source.aliases:
        clean = str(alias).strip()
        if clean and _norm(clean) not in existing_alias_keys:
            target.alias_hit_counts[clean] = max(
                target.alias_hit_counts.get(clean, 0),
                1,
            )
    if source.summary is not None:
        target.summary = source.summary
    if source.item_type is not None:
        target.item_type = source.item_type
    if source.item_source is not None:
        target.item_source = source.item_source
    if source.location_type is not None:
        target.location_type = source.location_type
    if source.features:
        target.features = source.features
    if source.parent_location is not None:
        target.parent_location = source.parent_location
    if source.description is not None:
        target.description = source.description


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
