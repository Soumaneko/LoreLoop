from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict


class ConfirmationEvent(TypedDict, total=False):
    step: str
    data: dict[str, Any]


class ConfirmationState(TypedDict, total=False):
    events: Annotated[list[ConfirmationEvent], add]
    pipeline_input: Any
    write_db: bool
    scene_text: str | None
    pending: Any
    turn_id: str
    trace_id: str
    effective_scene_text: str
    cache: dict[str, Any]
    director_npc_profile_id: str | None
    confirmed_director_npc_draft: Any
    director_npc_restock_scheduled: bool
    state_before_update: Any
    confirmation_scene_text: str
    existing_entity_profiles: list[Any]
    existing_npc_profiles: list[Any]
    core_memory: Any
    scene_summaries: list[Any]
    recent_summaries: list[Any]
    scene_facts: Any
    scene_facts_json: str
    updated_state: Any
    pending_memory_result: Any
    entity_extraction: Any
    npc_extraction: Any
    action_preview: Any
    updated_entity_ids: list[str]
    updated_npc_profile_ids: list[str]
    director_event_result: Any
