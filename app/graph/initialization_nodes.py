from __future__ import annotations

from app.domain.core_memory import StoryDirectionAgent
from app.domain.scene_writer.opening import OpeningSceneAgent
from app.runtime.serialization import model_data

from .initialization_helpers import apply_story_direction, initialization_gateway
from .initialization_state import InitializationState


def story_direction_node(state: InitializationState) -> InitializationState:
    story_direction = StoryDirectionAgent(initialization_gateway()).generate(
        config=state["config"],
        base_core_memory=state["core_memory"],
        trace_id=state["trace_id"],
    )
    core_memory, current_state = apply_story_direction(state, story_direction)
    return {
        "story_direction": story_direction,
        "core_memory": core_memory,
        "current_state": current_state,
        "events": [
            {
                "step": "story_direction_done",
                "data": {
                    "story_direction": model_data(story_direction),
                    "core_memory": model_data(core_memory),
                },
            }
        ],
    }


def opening_scene_node(state: InitializationState) -> InitializationState:
    opening_scene = OpeningSceneAgent(initialization_gateway()).generate(
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
