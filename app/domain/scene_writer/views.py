import json

from app.domain.plot_outline.schemas import PlotOutlineResult


_BEAT_EXCLUDE: set[str] = {"linked_action_ids", "linked_roll_ids", "linked_outcome_ids"}


def project_outline(result: PlotOutlineResult) -> str:
    data = result.model_dump(mode="json")
    data["beats"] = [
        {k: v for k, v in beat.items() if k not in _BEAT_EXCLUDE}
        for beat in data["beats"]
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)
