import json

from .schemas import TurnResolutionResult


def project_turn_resolution(result: TurnResolutionResult) -> str:
    data = result.model_dump(
        mode="json",
        exclude={
            "resolution_reasoning": True,
            "action_resolutions": {"__all__": {"effect_reasoning"}},
        },
    )
    for resolution in data.get("action_resolutions", []):
        source = resolution.get("resolution_source")
        resolution["resolution_mode"] = (
            "routine" if source == "automatic" else "roll"
        )
    return json.dumps(data, ensure_ascii=False, indent=2)
