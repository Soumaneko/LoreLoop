import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import (
    EntityProfile,
    EntityProfileUpdateBatch,
    EntityProfileUpdateInput,
    RetrievedEntity,
    profile_from_semantic,
    utc_now,
)


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
        trace_id: str | None = None,
    ) -> list[EntityProfile]:
        if not scene_text.strip():
            return []

        from app.domain.views import project_current_state

        result = cast(
            EntityProfileUpdateBatch,
            self._gateway.run(
                task="entity_profile_update",
                input_data={
                    "scene_text": scene_text,
                    "current_state_summary_json": project_current_state(current_state),
                    "relevant_profiles_json": json.dumps(
                        [
                            profile.semantic_view().model_dump(mode="json")
                            for profile in relevant_profiles
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "new_entity_hints_json": json.dumps(
                        [
                            hint.model_dump(mode="json")
                            for hint in (new_entity_hints or [])
                        ],
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
        return self._merge(relevant_profiles, result.entities, current_state.scene.scene_id)

    def _merge(
        self,
        old_profiles: list[EntityProfile],
        returned: list[EntityProfileUpdateInput],
        scene_id: str,
    ) -> list[EntityProfile]:
        result: list[EntityProfile] = []
        now = utc_now()
        for update in returned:
            existing = _find_existing(update, old_profiles)
            if existing is not None:
                merged = existing.model_copy(deep=True)
                apply_semantic_update(merged, update)
                if scene_id not in merged.source_scene_ids:
                    merged.source_scene_ids.append(scene_id)
                merged.confidence = min(1.0, merged.confidence + 0.05)
                merged.updated_at = now
                result.append(merged)
            else:
                result.append(
                    profile_from_semantic(
                        update,
                        source_scene_ids=[scene_id],
                        confidence=0.5,
                        updated_at=now,
                    )
                )
        return result


def _find_existing(
    update: EntityProfileUpdateInput,
    profiles: list[EntityProfile],
) -> EntityProfile | None:
    update_names = {_norm(update.name), *[_norm(alias) for alias in update.aliases]}
    update_names.discard("")
    for profile in profiles:
        profile_names = {_norm(profile.name), *[_norm(alias) for alias in profile.aliases]}
        profile_names.discard("")
        if update_names & profile_names:
            return profile
    return None


def apply_semantic_update(
    target: EntityProfile,
    source: EntityProfileUpdateInput,
) -> None:
    target.entity_type = source.entity_type
    if source.name.strip():
        target.name = source.name
    target.aliases = _merge_unique([*target.aliases, *source.aliases])
    if source.summary is not None:
        target.summary = source.summary
    if source.role is not None:
        target.role = source.role
    if source.personality_traits:
        target.personality_traits = source.personality_traits
    if source.known_abilities:
        target.known_abilities = source.known_abilities
    if source.faction is not None:
        target.faction = source.faction
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
