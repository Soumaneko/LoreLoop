from __future__ import annotations

from app.domain.scene_writer.opening import OpeningSceneAgent
from app.domain.world_setting.initial_creator import InitialWorldSettingCreator
from app.runtime.serialization import model_data

from .initialization_helpers import initialization_gateway
from .initialization_state import InitializationState


def initial_world_setting_node(
    state: InitializationState,
) -> InitializationState:
    with state["runtime_services"].stores.world_setting() as store:
        anchors = store.load_initial_anchors()
        reused = bool(anchors)
        if not anchors:
            result = InitialWorldSettingCreator(
                initialization_gateway(state)
            ).create(
                core_memory=state["core_memory"],
                story_direction=state["story_direction"],
                trace_id=state["trace_id"],
            )
            anchors = store.save_initial_anchors(
                [setting.to_anchor() for setting in result.settings]
            )
    return {
        "initial_world_settings": anchors,
        "events": [
            {
                "step": "initial_world_settings_ready",
                "data": {
                    "reused": reused,
                    "settings": model_data(anchors),
                },
            }
        ],
    }


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
