from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import ValidationError

from app.core.llm.config import create_gateway
from app.domain.core_memory import (
    CoreMemory,
    InitConfig,
    StoryDirectionResult,
    build_core_memory,
    build_initial_current_state,
)
from app.domain.current_state import CurrentState
from app.engine.serialization import model_data
from app.engine.session import PENDING_OPENINGS, PendingOpening
from app.engine.stores import core_memory_store, current_state_store

from .initialization_state import InitializationState


def initialization_gateway():
    gateway = create_gateway()
    gateway.set_trace_context(session_type="init")
    return gateway


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


def prepare_initialization_state(payload: dict[str, Any]) -> InitializationState:
    config, core_memory = build_core_memory_from_payload(payload)
    return {
        "events": [],
        "trace_id": str(uuid.uuid4()),
        "config": config,
        "core_memory": core_memory,
        "current_state": build_initial_current_state(config),
    }


def initialization_route(
    state: InitializationState,
) -> Literal["story_direction", "opening_scene"]:
    expectation = state["config"].story_expectation or ""
    return "story_direction" if expectation.strip() else "opening_scene"


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
    core_memory_store().save(state["core_memory"])
    with current_state_store() as state_store:
        state_store.save(state["current_state"])

    turn_id = f"opening:{uuid.uuid4()}"
    PENDING_OPENINGS[turn_id] = PendingOpening(
        turn_id=turn_id,
        config=state["config"],
        current_state=state["current_state"],
        scene_result=state["opening_scene"],
    )
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
        "opening_scene": model_data(state["opening_scene"]),
        "status": state["status"],
    }
