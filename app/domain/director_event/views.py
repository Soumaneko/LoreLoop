import json

from .schemas import DirectorEvent


def project_director_event(
    event: DirectorEvent,
    *,
    effective_stage: str | None = None,
) -> str:
    stage = effective_stage or event.stage
    if stage == "hidden":
        data = {
            "event_id": event.event_id,
            "role": event.role,
            "title": event.title,
            "stage": "hidden",
            "hooks": [
                {
                    "hook_id": hook.hook_id,
                    "kind": hook.kind,
                    "content": hook.content,
                    "consumed": hook.consumed,
                }
                for hook in event.hook_pool
                if not hook.consumed
            ],
        }
    else:
        data = {
            "event_id": event.event_id,
            "role": event.role,
            "title": event.title,
            "stage": stage,
            "macro_intent": event.macro_intent,
            "subject": event.subject,
            "actors": [actor.name for actor in event.actors],
        }
    return json.dumps(data, ensure_ascii=False, indent=2)


def project_director_events(events: list[DirectorEvent]) -> str:
    return json.dumps(
        [json.loads(project_director_event(event)) for event in events],
        ensure_ascii=False,
        indent=2,
    )
