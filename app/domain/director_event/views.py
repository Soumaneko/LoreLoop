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
    }
    if recent_resolved_event.resolved_reason:
        data["resolved_reason"] = recent_resolved_event.resolved_reason
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
                    "bridge_anchors": event.bridge_anchors,
                    "progress_index": event.progress_index,
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

    text = _context_text(
        user_input=user_input,
        current_state=current_state,
        context_facts=context_facts,
    )
    entry_index, entry = _matching_entry_point(event, text)
    current_milestone = event.milestones[event.progress_index]
    known_hint = {
        "event_id": event.event_id,
        "title": event.title,
        "exposure": event.exposure,
        "bridge_anchors": event.bridge_anchors,
        "progress_index": event.progress_index,
        "current_milestone": current_milestone,
        "next_progress_clock": (
            event.next_progress_clock.model_dump(mode="json")
            if event.next_progress_clock is not None
            else None
        ),
    }
    if entry is None:
        instruction = EventUseInstruction(
            mode="none",
            reason="本回合稳定锚点没有命中导演事件入口。",
        )
        event_hint = (
            known_hint
            if event.exposure != "hidden"
            else None
        )
        return json.dumps(
            {
                "event_use_instruction": instruction.model_dump(mode="json"),
                "event_hint": event_hint,
            },
            ensure_ascii=False,
            indent=2,
        )

    mode = "intersect" if event.exposure == "foreshadowed" else "foreshadow"
    allowed_effect = current_milestone if event.exposure != "hidden" else entry.visible_sign
    instruction = EventUseInstruction(
        mode=mode,
        entry_point_id=f"entry_{entry_index + 1}",
        allowed_effect=allowed_effect,
        reason="当前输入、场景或上下文事实命中导演事件入口锚点。",
    )
    event_hint = (
        {
            **known_hint,
            "visible_sign": entry.visible_sign,
        }
        if event.exposure != "hidden"
        else {
            "visible_sign": entry.visible_sign,
        }
    )
    return json.dumps(
        {
            "event_use_instruction": instruction.model_dump(mode="json"),
            "event_hint": event_hint,
        },
        ensure_ascii=False,
        indent=2,
    )


def plot_outline_director_event_instructions(
    event: DirectorEvent | None,
    *,
    recent_resolved_event: DirectorEvent | None = None,
) -> str:
    if event is None and _recent_resolved_projection(recent_resolved_event) is not None:
        return (
            "director_event_json 中的 event_use_instruction.mode=aftermath 只允许自然余波，"
            "不得把已收束事件继续当作 active 事件推进，不得复燃同一事件。"
        )
    if event is None or event.status != "active":
        return ""
    if event.exposure == "engaged":
        return (
            "玩家已经介入该事件；"
            "foreground 模式只提供当前 milestone 投影；"
            "只在当前行动、NPC 或结果自然相关时体现其压力、机会或后果。"
        )
    return (
        "DirectorEvent 的使用必须服从 director_event_json.event_use_instruction。"
        "mode=none 时不得使用；mode=foreshadow 时只能展示 allowed_effect 中的 visible_sign，不得形成任务；"
        "mode=intersect 时只可使用 allowed_effect 中的当前 milestone 投影改变当前场景条件；"
        "具体前台影响必须融入 planned_events、must_establish_facts 或 must_not_establish_facts。"
    )
