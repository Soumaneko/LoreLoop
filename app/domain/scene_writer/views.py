import json

from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.short_term_memory.schemas import ShortTermScene


_BEAT_EXCLUDE: set[str] = {"linked_action_ids", "linked_roll_ids", "linked_outcome_ids"}


def project_outline(result: PlotOutlineResult) -> str:
    data = result.model_dump(mode="json")
    data["beats"] = [
        {k: v for k, v in beat.items() if k not in _BEAT_EXCLUDE}
        for beat in data["beats"]
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
    if scene.narrative_type:
        data["narrative_type"] = scene.narrative_type
    return json.dumps(data, ensure_ascii=False, indent=2)
