import json

from .schemas import DirectorEvent


def project_director_event(event: DirectorEvent | None) -> str:
    if event is None:
        return "null"
    return json.dumps(event.semantic_view(), ensure_ascii=False, indent=2)
