import json

from .schemas import DirectorEvent


def project_director_event(
    event: DirectorEvent,
) -> str:
    data = event.semantic_view()
    current_offset = event.progress_index - 1
    if event.route_mode == "intervention" and event.intervention_route is not None:
        past_milestones = event.milestones[:event.progress_index]
        current_milestone = event.intervention_route.goal
    else:
        past_milestones = event.milestones[:current_offset]
        current_milestone = event.milestones[current_offset]
    for key in (
        "milestones",
        "milestone_interval_steps",
        "next_progress_clock",
        "progress_index",
        "player_contacts",
        "last_presented_turn",
        "route_mode",
        "intervention_route",
    ):
        data.pop(key, None)
    data["already_happened"] = [
        *past_milestones,
        *(
            [
                f"主角接触记录（第{record.turn_index}回合）：{record.contact}"
                for record in event.player_contacts
            ]
            or ["主角尚未接触该事件的任何前台征兆。"]
        ),
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
