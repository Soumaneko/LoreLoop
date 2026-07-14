import json

from .schemas import DirectorEvent, DirectorEventConsumption


def project_director_event(
    event: DirectorEvent,
) -> str:
    data = event.semantic_view()
    past_milestones = event.milestones[:event.progress_index]
    current_milestone = event.milestones[event.progress_index]
    for key in (
        "milestones",
        "milestone_interval_steps",
        "next_progress_clock",
        "progress_index",
        "player_contact_state",
        "last_presented_turn",
    ):
        data.pop(key, None)
    data["already_happened"] = [
        *past_milestones,
        f"主角当前接触情况：{event.player_contact_state}",
    ]
    data["currently_happening"] = current_milestone
    if event.exposure != "hidden":
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
                "player_contact_state": consumption.player_contact_state,
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
