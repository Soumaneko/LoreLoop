from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.director_event.schemas import DirectorEvent


def project_director_event_identities(events: list[DirectorEvent]) -> str:
    return json.dumps(
        [
            {
                "role": event.role,
                "premise": event.premise,
                "milestones": event.milestones,
            }
            for event in events
        ],
        ensure_ascii=False,
        indent=2,
    )
