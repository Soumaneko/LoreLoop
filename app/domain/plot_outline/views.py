import json

from app.domain.turn_resolution.schemas import TurnResolutionResult
from app.domain.views import project_retrieved_memories

__all__ = ["project_retrieved_memories", "project_turn_resolution"]


def project_turn_resolution(result: TurnResolutionResult) -> str:
    return json.dumps(
        result.model_dump(
            mode="json",
            exclude={
                "resolution_reasoning": True,
                "available_types": True,
                "previous_types": True,
                "roll_filter_notes": True,
                "cooldown_filter_notes": True,
                "action_resolutions": {"__all__": {"effect_reasoning"}},
            },
        ),
        ensure_ascii=False,
        indent=2,
    )
