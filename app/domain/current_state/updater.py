import json
import re
from typing import cast

from app.core.llm import LLMGateway
from app.domain.current_state.schemas import CurrentState


def _increment_scene_id(scene_id: str) -> str:
    m = re.search(r"(\d+)$", scene_id)
    if m:
        num = int(m.group(1)) + 1
        width = len(m.group(1))
        return scene_id[: m.start()] + str(num).zfill(width)
    return scene_id + "_1"


class StateUpdater:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def update(
        self,
        current_state: CurrentState,
        scene_text: str,
        *,
        trace_id: str | None = None,
    ) -> CurrentState:
        state_for_llm = current_state.model_copy(deep=True)
        state_data = state_for_llm.model_dump()
        state_data["scene"].pop("scene_id", None)

        schema = CurrentState.model_json_schema()
        scene_def = schema["$defs"]["SceneState"]
        scene_def["properties"].pop("scene_id", None)
        if "scene_id" in scene_def.get("required", []):
            scene_def["required"].remove("scene_id")

        result = cast(
            CurrentState,
            self.llm.run(
                task="state_update",
                input_data={
                    "current_state_json": json.dumps(state_data, ensure_ascii=False, indent=2),
                    "scene_text": scene_text,
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=CurrentState,
                trace_id=trace_id,
            ),
        )

        if result.scene.location != current_state.scene.location:
            result.scene.scene_id = _increment_scene_id(current_state.scene.scene_id)
        else:
            result.scene.scene_id = current_state.scene.scene_id

        return result
