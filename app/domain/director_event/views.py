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
            "title": event.title,
            "stage": stage,
            "macro_intent": event.macro_intent,
            "subject": event.subject,
            "actors": [actor.name for actor in event.actors],
        }
    return json.dumps(data, ensure_ascii=False, indent=2)


def project_director_events(
    events: list[DirectorEvent],
    *,
    effective_stage: str | None = None,
) -> str:
    return json.dumps(
        [
            json.loads(
                project_director_event(
                    event,
                    effective_stage=effective_stage,
                )
            )
            for event in events
        ],
        ensure_ascii=False,
        indent=2,
    )


def project_creation_settings(
    settings: list[dict], selected_fact_ids: list[str],
) -> list[dict]:
    """Keep all public facts and only selected hidden facts, preserving source IDs."""
    selected = set(selected_fact_ids)
    projected = []
    for setting in settings:
        public_facts = list(setting["public_facts"])
        hidden_facts = [
            fact
            for index, fact in enumerate(setting["hidden_facts"], start=len(public_facts) + 1)
            if (fact.get("fact_id") if isinstance(fact, dict) else f"fact_{index}") in selected
        ]
        projected.append({**setting, "public_facts": public_facts, "hidden_facts": hidden_facts})
    return projected
