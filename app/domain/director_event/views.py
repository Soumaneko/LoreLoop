import json

from .schemas import DirectorEvent, DirectorEventConsumption


def project_director_event(
    event: DirectorEvent,
) -> str:
    data = event.semantic_view()
    data.pop("next_progress_clock", None)
    if event.exposure == "engaged":
        data.pop("entry_points", None)
    return json.dumps(data, ensure_ascii=False, indent=2)


def project_director_events(events: list[DirectorEvent]) -> str:
    return json.dumps(
        [json.loads(project_director_event(event)) for event in events],
        ensure_ascii=False,
        indent=2,
    )


def project_director_event_consumptions(
    consumptions: list[DirectorEventConsumption],
) -> str:
    return json.dumps(
        [
            {
                "allowed_facts": consumption.allowed_facts,
                "immutable_constraints": consumption.immutable_constraints,
                "forbidden_inferences": consumption.forbidden_inferences,
            }
            for consumption in consumptions
            if consumption.use_event
        ],
        ensure_ascii=False,
        indent=2,
    )
