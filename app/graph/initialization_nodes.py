from __future__ import annotations

from app.domain.scene_writer.opening import OpeningSceneAgent
from app.runtime.serialization import model_data

from .initialization_helpers import initialization_gateway
from .initialization_state import InitializationState


def opening_scene_node(state: InitializationState) -> InitializationState:
    opening_scene = OpeningSceneAgent(initialization_gateway(state)).generate(
        config=state["config"],
        current_state=state["current_state"],
        core_memory=state["core_memory"],
        trace_id=state["trace_id"],
    )
    return {
        "opening_scene": opening_scene,
        "events": [
            {
                "step": "opening_scene_done",
                "data": {"opening_scene": model_data(opening_scene)},
            }
        ],
    }
