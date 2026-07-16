import json

from .schemas import TurnResolutionResult


def project_turn_resolution(result: TurnResolutionResult) -> str:
    return json.dumps(
        result.model_dump(
            mode="json",
            exclude={
                "resolution_reasoning": True,
                "action_resolutions": {"__all__": {"effect_reasoning"}},
            },
        ),
        ensure_ascii=False,
        indent=2,
    )
