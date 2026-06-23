import json

from app.domain.turn_resolution.schemas import TurnResolutionResult
from app.domain.views import project_retrieved_memories

__all__ = ["project_retrieved_memories", "project_turn_resolution"]


def project_turn_resolution(result: TurnResolutionResult) -> str:
    return json.dumps(
        result.model_dump(
            mode="json",
            exclude={"available_types", "previous_types", "roll_filter_notes", "cooldown_filter_notes"},
        ),
        ensure_ascii=False,
        indent=2,
    )
