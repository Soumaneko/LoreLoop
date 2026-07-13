import json

from app.domain.context_facts.schemas import ContextFactSet
from app.domain.current_state.schemas import CurrentState

from .schemas import DirectorEvent, EventUseInstruction


def _recent_resolved_projection(recent_resolved_event: DirectorEvent | None) -> dict | None:
    if recent_resolved_event is None:
        return None
    resolved_turn = recent_resolved_event.resolved_turn or recent_resolved_event.updated_turn
    if resolved_turn is None:
        return None
    data = {
        "status": "recently_resolved",
        "event_id": recent_resolved_event.event_id,
        "title": recent_resolved_event.title,
        "resolved_turn": resolved_turn,
        "message": f"『{recent_resolved_event.title}』事件最近已收束。",
        "player_contact_state": recent_resolved_event.player_contact_state,
    }
    return data


def project_director_event(
    event: DirectorEvent | None,
    *,
    recent_resolved_event: DirectorEvent | None = None,
) -> str:
    if event is None:
        data = _recent_resolved_projection(recent_resolved_event)
        if data is not None:
            return json.dumps(data, ensure_ascii=False, indent=2)
        return "null"
    data = event.semantic_view()
    if event.exposure == "engaged":
        data.pop("entry_points", None)
    return json.dumps(data, ensure_ascii=False, indent=2)


def project_director_events(events: list[DirectorEvent]) -> str:
    return json.dumps(
        [json.loads(project_director_event(event)) for event in events],
        ensure_ascii=False,
        indent=2,
    )


def _context_text(
    *,
    user_input: str,
    current_state: CurrentState,
    context_facts: ContextFactSet | None = None,
) -> str:
    fact_text = " ".join(fact.fact for fact in (context_facts.facts if context_facts else []))
    scene = current_state.scene
    player = current_state.player
    participants = " ".join(scene.participants or [])
    return " ".join(
        [
            user_input or "",
            scene.location or "",
            participants,
            player.position or "",
            fact_text,
        ]
    )


def _matches_anchor(text: str, anchors: list[str]) -> bool:
    return any(anchor and anchor in text for anchor in anchors)


def _matching_entry_point(event: DirectorEvent, text: str):
    for index, entry in enumerate(event.entry_points):
        if _matches_anchor(text, entry.trigger_anchors):
            return index, entry
    return None, None


def project_plot_director_event(
    event: DirectorEvent | None,
    *,
    recent_resolved_event: DirectorEvent | None = None,
    user_input: str,
    current_state: CurrentState,
    context_facts: ContextFactSet | None = None,
) -> str:
    if event is None:
        data = _recent_resolved_projection(recent_resolved_event)
        if data is None:
            return json.dumps({"event_use_instruction": {"mode": "none", "reason": "没有 active 或 recently resolved 导演事件。"}}, ensure_ascii=False, indent=2)
        return json.dumps(
            {
                "event_use_instruction": {
                    "mode": "aftermath",
                    "reason": "最近事件已经收束，只允许自然余波，不得复燃。",
                },
                "recent_resolved_event": data,
            },
            ensure_ascii=False,
            indent=2,
        )

    if event.status != "active":
        return json.dumps({"event_use_instruction": {"mode": "none", "reason": "没有 active 导演事件。"}}, ensure_ascii=False, indent=2)

    if event.exposure == "engaged":
        instruction = EventUseInstruction(
            mode="foreground",
            allowed_effect=event.milestones[event.progress_index],
            reason="玩家已经直接介入该事件，Plot 可在当前行动相关时把当前阶段作为前台局势处理。",
        )
        return json.dumps(
            {
                "event_use_instruction": instruction.model_dump(mode="json"),
                "event_hint": {
                    "event_id": event.event_id,
                    "title": event.title,
                    "exposure": event.exposure,
                    "canonical_premise": event.premise,
                    "bridge_anchors": event.bridge_anchors,
                    "progress_index": event.progress_index,
                    "player_contact_state": event.player_contact_state,
                    "current_milestone": event.milestones[event.progress_index],
                    "next_progress_clock": (
                        event.next_progress_clock.model_dump(mode="json")
                        if event.next_progress_clock is not None
                        else None
                    ),
                },
            },
            ensure_ascii=False,
            indent=2,
        )

    if event.exposure == "foreshadowed":
        current_milestone = event.milestones[event.progress_index]
        instruction = EventUseInstruction(
            mode="intersect",
            allowed_effect=current_milestone,
            reason="玩家已经接触事件线索但尚未正式介入，本轮可按当前 milestone 回应玩家的相关行动。",
        )
        return json.dumps(
            {
                "event_use_instruction": instruction.model_dump(mode="json"),
                "event_hint": {
                    "event_id": event.event_id,
                    "title": event.title,
                    "exposure": event.exposure,
                    "player_contact_state": event.player_contact_state,
                    "current_milestone": current_milestone,
                },
            },
            ensure_ascii=False,
            indent=2,
        )

    text = _context_text(
        user_input=user_input,
        current_state=current_state,
        context_facts=context_facts,
    )
    entry_index, entry = _matching_entry_point(event, text)
    if entry is None:
        instruction = EventUseInstruction(
            mode="none",
            reason="本回合稳定锚点没有命中导演事件入口。",
        )
        return json.dumps(
            {
                "event_use_instruction": instruction.model_dump(mode="json"),
                "event_hint": None,
            },
            ensure_ascii=False,
            indent=2,
        )

    instruction = EventUseInstruction(
        mode="foreshadow",
        entry_point_id=f"entry_{entry_index + 1}",
        allowed_effect=entry.visible_sign,
        reason="当前输入、场景或上下文事实命中导演事件入口锚点。",
    )
    return json.dumps(
        {
            "event_use_instruction": instruction.model_dump(mode="json"),
            "event_hint": {"visible_sign": entry.visible_sign},
        },
        ensure_ascii=False,
        indent=2,
    )


def project_plot_director_events(
    events: list[DirectorEvent],
    *,
    user_input: str,
    current_state: CurrentState,
    context_facts: ContextFactSet | None = None,
) -> str:
    projected = []
    for event in events:
        projection = json.loads(
            project_plot_director_event(
                event,
                user_input=user_input,
                current_state=current_state,
                context_facts=context_facts,
            )
        )
        projected.append({"role": event.role, **projection})
    return json.dumps(projected, ensure_ascii=False, indent=2)
