import json

from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.short_term_memory.schemas import ShortTermScene


_PLANNED_EVENT_EXCLUDE: set[str] = {"linked_outcome_ids"}
_OUTLINE_EXCLUDE: set[str] = {"planning_reasoning"}


def project_outline(result: PlotOutlineResult) -> str:
    data = {
        key: value
        for key, value in result.model_dump(mode="json").items()
        if key not in _OUTLINE_EXCLUDE
    }
    data["planned_events"] = [
        {k: v for k, v in event.items() if k not in _PLANNED_EVENT_EXCLUDE}
        for event in data["planned_events"]
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)


def project_recent_scene(scene: ShortTermScene | None) -> str:
    if scene is None:
        return "null"
    data = {
        "scene_id": scene.scene_id,
        "turn_index": scene.turn_index,
        "scene_text": scene.scene_text,
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
