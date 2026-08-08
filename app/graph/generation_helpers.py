from __future__ import annotations

import uuid
from typing import Any

from app.core.cancellation import commit_if_not_cancelled
from app.core.llm.token_usage import get_trace_token_usage
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director.schemas import DirectorStageResult
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.narrative_composer.schemas import NarrativeComposerContext
from app.domain.roll.schemas import Difficulty
from app.domain.turn_resolution import build_player_turn_resolution
from app.domain.turn_resolution.schemas import TurnResolutionResult
from app.runtime.serialization import model_data
from app.runtime.services import RuntimeServices
from app.runtime.session import CampaignRuntime, PendingTurn, get_campaign_runtime

from .generation_state import GenerationEvent, GenerationState
from app.domain.npc_profile.selection import select_context_profiles


def assemble_narrative_context(
    *,
    user_input: str,
    current_state: CurrentState,
    turn_resolution: TurnResolutionResult,
    core_memory: CoreMemory,
    context_facts: ContextFactSet | None = None,
    npc_profiles: list[NPCProfileDirectorView] | None = None,
    director_stage: DirectorStageResult | None = None,
    engaged_events: list | None = None,
) -> NarrativeComposerContext:
    return NarrativeComposerContext(
        user_input=user_input,
        current_state=current_state,
        turn_resolution=turn_resolution,
        core_memory=core_memory,
        context_facts=context_facts
        or ContextFactSet(turn_focus="No Context Facts", facts=[]),
        npc_profiles=npc_profiles or [],
        director_stage=director_stage,
        engaged_events=engaged_events or [],
    )


def gateway_for(state: GenerationState):
    turn_index = state.get("turn_index") or state.get("next_turn_index")
    return state["runtime_services"].gateway(
        turn_index=turn_index,
        session_type="generation",
    )


def prepare_generation_state(
    user_input: str,
    difficulty: Difficulty = "困难",
    max_chars: int = 1500,
    *,
    runtime: CampaignRuntime | None = None,
) -> GenerationState:
    runtime = runtime or get_campaign_runtime()
    services = runtime.services
    stores = services.stores
    trace_id = str(uuid.uuid4())
    core_memory = stores.core_memory().load_required()

    with stores.current_state() as state_store:
        current_state = state_store.load_or_default()

    with stores.short_term() as short_store:
        short_term_memory = short_store.get_recent()

    turn_index = (
        short_term_memory.recent_scenes[-1].turn_index + 1
        if short_term_memory.recent_scenes
        else 1
    )

    with stores.scene_summary() as summary_store:
        scene_summaries = summary_store.list_active()
        recent_summaries = summary_store.get_recent(5)

    state: GenerationState = {
        "events": [],
        "runtime_services": services,
        "runtime": runtime,
        "user_input": user_input,
        "difficulty": difficulty,
        "max_chars": max_chars,
        "trace_id": trace_id,
        "core_memory": core_memory,
        "current_state": current_state,
        "short_term_memory": short_term_memory,
        "turn_index": turn_index,
        "next_turn_index": turn_index,
        "scene_summaries": scene_summaries,
        "recent_summaries": recent_summaries,
    }

    with stores.npc_profile() as npc_store:
        selection = select_context_profiles(
            user_input=user_input,
            current_state=current_state,
            npc_store=npc_store,
        )

    state.update({
        "matched_anchors": selection.anchors,
        "action_npc_profiles": selection.npc_profiles,
        "action_npc_director_views": [
            profile.director_view()
            for profile in selection.npc_profiles
        ],
    })
    state["events"] = [
        state_loaded_event(state),
        profiles_loaded_event(state),
    ]
    return state


def finalize_pending_turn(
    state: GenerationState,
    *,
    group_id: str | None = None,
    version_index: int = 1,
) -> GenerationState:
    turn_id = str(uuid.uuid4())
    group_id = group_id or turn_id
    runtime = state.get("runtime") or get_campaign_runtime()
    player_resolution = state.get("player_resolution")
    if player_resolution is None:
        player_resolution = build_player_turn_resolution(
            state["action_parse_result"],
            state["roll_results"],
            state["turn_resolution"],
        )
    token_usage = {
        "generation": get_trace_token_usage(state["trace_id"]),
        "confirmation": None,
    }
    pending = PendingTurn(
        turn_id=turn_id,
        group_id=group_id,
        version_index=version_index,
        user_input=state["user_input"],
        difficulty=state["difficulty"],
        max_chars=state["max_chars"],
        experience_mode=state["experience_mode"],
        state_before=state["current_state"],
        context=state["context"],
        outline=state["outline"],
        scene_result=state["scene_result"],
        director_stage=state.get("director_stage"),
        player_resolution=player_resolution,
        turn_index=state["next_turn_index"],
        short_term_memory=state["short_term_memory"],
        token_usage=token_usage,
        director_log_entry=state.get("director_log_entry"),
    )
    commit_if_not_cancelled(lambda: runtime.pending_turns.add(pending))
    finalized = {
        **state,
        "turn_id": turn_id,
        "group_id": group_id,
        "version_index": version_index,
        "turn_index": state["next_turn_index"],
        "status": "awaiting_confirm",
        "pending_turn_status": "awaiting_confirm",
        "player_resolution": player_resolution,
        "token_usage": token_usage,
    }
    finalized["events"] = [
        *state.get("events", []),
        scene_written_event(finalized),
    ]
    return finalized


def state_loaded_event(state: dict[str, Any]) -> GenerationEvent:
    return {
        "step": "state_loaded",
        "data": {
            "trace_id": state["trace_id"],
            "state_before": model_data(state["current_state"]),
            "core_memory": model_data(state["core_memory"]),
            "short_term_memory": model_data(state["short_term_memory"]),
            "scene_summaries": model_data(state["scene_summaries"]),
            "recent_summaries": model_data(state["recent_summaries"]),
        },
    }


def profiles_loaded_event(state: dict[str, Any]) -> GenerationEvent:
    return {
        "step": "npc_profiles_loaded",
        "data": {
            "anchors": model_data(state.get("matched_anchors", [])),
            "npc_profiles": model_data(
                state.get("action_npc_director_views", [])
            ),
        },
    }


def scene_written_event(state: dict[str, Any]) -> GenerationEvent:
    return {
        "step": "scene_written",
        "data": {
            "turn_id": state["turn_id"],
            "group_id": state["group_id"],
            "version_index": state["version_index"],
            "trace_id": state["trace_id"],
            "turn_index": state["turn_index"],
            "token_usage": state.get("token_usage"),
            "composer_npc_profiles": model_data(
                state.get("composer_npc_public_views", [])
            ),
            "recent_scene": model_data(state.get("recent_scene")),
            "scene_result": model_data(state["scene_result"]),
            "context": model_data(state["context"]),
            "status": "awaiting_confirm",
        },
    }
