import uuid
from typing import Any

from pydantic import ValidationError

from app.core.llm.config import create_gateway
from app.domain.core_memory import (
    CoreMemory,
    InitConfig,
    StoryDirectionAgent,
    build_core_memory,
    build_initial_current_state,
)
from app.domain.scene_writer.opening import OpeningSceneAgent

from .serialization import model_data
from .session import PENDING_OPENINGS, PendingOpening
from .stores import core_memory_store, current_state_store


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


def apply_core_memory_init(payload: dict[str, Any]) -> dict[str, Any]:
    config, core_memory = build_core_memory_from_payload(payload)
    current_state = build_initial_current_state(config)
    trace_id = str(uuid.uuid4())
    gateway = create_gateway()
    gateway.set_trace_context(session_type="init")
    if (config.story_expectation or "").strip():
        story_direction = StoryDirectionAgent(gateway).generate(
            config=config,
            base_core_memory=core_memory,
            trace_id=trace_id,
        )
        core_memory = CoreMemory(
            items=[*core_memory.items, *story_direction.to_core_memory_items()]
        )
        currency_unit = (story_direction.currency_unit or "").strip()
        if currency_unit:
            current_state = current_state.model_copy(
                update={
                    "resources": current_state.resources.model_copy(
                        update={"money_unit": currency_unit}
                    )
                }
            )

    opening_scene = OpeningSceneAgent(gateway).generate(
        config=config,
        current_state=current_state,
        core_memory=core_memory,
        trace_id=trace_id,
    )

    core_memory_store().save(core_memory)
    with current_state_store() as state_store:
        state_store.save(current_state)

    turn_id = f"opening:{uuid.uuid4()}"
    PENDING_OPENINGS[turn_id] = PendingOpening(
        turn_id=turn_id,
        config=config,
        current_state=current_state,
        scene_result=opening_scene,
    )
    return {
        "turn_id": turn_id,
        "config": model_data(config),
        "core_memory": model_data(core_memory),
        "current_state": model_data(current_state),
        "opening_scene": model_data(opening_scene),
        "status": "awaiting_confirm",
    }
