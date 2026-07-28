from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from app.core.llm.token_usage import get_trace_token_usage
from app.domain.core_memory import (
    CoreMemory,
    InitConfig,
    StoryDirectionAgent,
    StoryDirectionResult,
    build_core_memory,
    build_initial_current_state,
)
from app.domain.current_state import CurrentState
from app.runtime.serialization import model_data
from app.runtime.session import PendingOpening, get_campaign_runtime

from .initialization_state import InitializationState


def initialization_gateway(state: InitializationState):
    return state["runtime_services"].gateway(session_type="init")


def build_core_memory_from_payload(payload: dict[str, Any]):
    config = InitConfig.model_validate(payload)
    core_memory = build_core_memory(config)
    return config, core_memory


def preview_core_memory(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        config, core_memory = build_core_memory_from_payload(payload)
    except ValidationError as exc:
        return {
            "valid": False,
            "validation_errors": exc.errors(include_url=False),
            "config": payload,
            "core_memory": None,
        }
    return {
        "valid": True,
        "validation_errors": [],
        "config": model_data(config),
        "core_memory": model_data(core_memory),
    }


def generate_story_direction_stream(payload: dict[str, Any]):
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        config, core_memory = build_core_memory_from_payload(payload)
        trace_id = str(uuid.uuid4())
        yield {
            "step": "story_direction_started",
            "data": {"trace_id": trace_id},
        }
        story_direction = StoryDirectionAgent(
            runtime.services.gateway(session_type="init")
        ).generate(
            config=config,
            base_core_memory=core_memory,
            trace_id=trace_id,
        )
        result = {
            "config": model_data(config),
            "core_memory": model_data(core_memory),
            "story_direction": model_data(story_direction),
            "trace_id": trace_id,
        }
        yield {"step": "story_direction_done", "data": result}
        return result


def prepare_initialization_state(payload: dict[str, Any]) -> InitializationState:
    runtime = get_campaign_runtime()
    services = runtime.services
    config_payload = payload.get("config")
    if not isinstance(config_payload, dict):
        raise ValueError("config is required")
    story_direction_payload = payload.get("story_direction")
    if not isinstance(story_direction_payload, dict):
        raise ValueError("story_direction is required")

    config, core_memory = build_core_memory_from_payload(config_payload)
    story_direction = StoryDirectionResult.model_validate(story_direction_payload)
    state: InitializationState = {
        "events": [],
        "runtime_services": services,
        "trace_id": str(uuid.uuid4()),
        "config": config,
        "core_memory": core_memory,
        "current_state": build_initial_current_state(config),
        "story_direction": story_direction,
    }
    core_memory, current_state = apply_story_direction(state, story_direction)
    state["core_memory"] = core_memory
    state["current_state"] = current_state
    return state


def apply_story_direction(
    state: InitializationState,
    story_direction: StoryDirectionResult,
) -> tuple[CoreMemory, CurrentState]:
    core_memory = CoreMemory(
        items=[
            *state["core_memory"].items,
            *story_direction.to_core_memory_items(),
        ]
    )
    current_state = state["current_state"]
    currency_unit = (story_direction.currency_unit or "").strip()
    if currency_unit:
        current_state = current_state.model_copy(
            update={
                "resources": current_state.resources.model_copy(
                    update={"money_unit": currency_unit}
                )
            }
        )
    return core_memory, current_state


def finalize_initialization_state(
    state: InitializationState,
) -> InitializationState:
    stores = state["runtime_services"].stores
    stores.core_memory().save(state["core_memory"])
    with stores.current_state() as state_store:
        state_store.save(state["current_state"])

    turn_id = f"opening:{uuid.uuid4()}"
    get_campaign_runtime().pending_openings.add(PendingOpening(
        turn_id=turn_id,
        config=state["config"],
        current_state=state["current_state"],
        scene_result=state["opening_scene"],
        token_usage={
            "generation": get_trace_token_usage(state["trace_id"]),
            "confirmation": None,
        },
    ))
    return {
        **state,
        "turn_id": turn_id,
        "status": "awaiting_confirm",
        "events": [
            *state.get("events", []),
            {
                "step": "initialization_pending",
                "data": {"turn_id": turn_id, "status": "awaiting_confirm"},
            },
        ],
    }


def initialization_result(state: InitializationState) -> dict[str, Any]:
    return {
        "turn_id": state["turn_id"],
        "config": model_data(state["config"]),
        "core_memory": model_data(state["core_memory"]),
        "current_state": model_data(state["current_state"]),
        "initial_world_settings": model_data(state["initial_world_settings"]),
        "opening_scene": model_data(state["opening_scene"]),
        "status": state["status"],
    }
