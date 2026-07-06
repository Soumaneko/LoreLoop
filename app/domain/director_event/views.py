import json

from .schemas import DirectorEvent


def _recent_archived_projection(recent_archived_event: DirectorEvent | None) -> dict | None:
    if recent_archived_event is None:
        return None
    resolved_turn = recent_archived_event.resolved_turn or recent_archived_event.updated_turn
    if resolved_turn is None:
        return None
    data = {
        "status": "recently_archived",
        "event_id": recent_archived_event.event_id,
        "title": recent_archived_event.title,
        "resolved_turn": resolved_turn,
        "message": f"『{recent_archived_event.title}』事件最近已收束。",
    }
    if recent_archived_event.resolved_reason:
        data["resolved_reason"] = recent_archived_event.resolved_reason
    return data


def project_director_event(
    event: DirectorEvent | None,
    *,
    recent_archived_event: DirectorEvent | None = None,
) -> str:
    if event is None:
        data = _recent_archived_projection(recent_archived_event)
        if data is not None:
            return json.dumps(data, ensure_ascii=False, indent=2)
        return "null"
    data = event.semantic_view()
    if event.player_involved:
        data.pop("entry_points", None)
    return json.dumps(data, ensure_ascii=False, indent=2)


def plot_outline_director_event_instructions(
    event: DirectorEvent | None,
    *,
    recent_archived_event: DirectorEvent | None = None,
) -> str:
    if event is None and _recent_archived_projection(recent_archived_event) is not None:
        return (
            "DirectorEvent 最近已收束。director_event_json 中的 recently_archived 只表示最近事件"
            "已经结束；不得把它继续当作 active 事件推进，不得复燃同一事件。"
            "可以自然表现余波、NPC 反应或世界恢复。"
        )
    if event is None or event.status != "active":
        return ""
    if event.player_involved:
        return (
            "玩家已经介入该事件；"
            "根据 backstage_premise 与 driving_tension 理解当前局势，"
            "只在当前行动、NPC 或结果自然相关时体现其压力、机会或后果；不得把后台事实直接说破。"
        )
    return (
        "DirectorEvent 是玩家不可见的后台事件，不得原文暴露、写成任务目标或替玩家决定回应。"
        "只有当前行动、地点、NPC、结果或场景气氛与 entry_points 存在自然交点时，"
        "才可选择一个入口，把它转译为前台可感知的压力、机会、阻碍或异常。"
        "没有自然交点时不要强行使用。使用时必须保留玩家选择空间，不得把后台事实直接说破。"
        "具体前台影响必须融入 planned_events、must_establish_facts 或 must_not_establish_facts；"
        "director_event_reason 只写使用或未使用的原因。"
    )


def turn_resolution_director_event_instructions(
    event: DirectorEvent | None,
    *,
    recent_archived_event: DirectorEvent | None = None,
) -> str:
    if event is None and _recent_archived_projection(recent_archived_event) is not None:
        return (
            "DirectorEvent 最近已收束。director_event_json 中的 recently_archived 是已结束事件的"
            "短期上下文；不得把它作为新的风险来源，只可在结果解释中承认其余波。"
        )
    if event is None or event.status != "active":
        return ""
    if event.player_involved:
        return (
            "DirectorEvent 是玩家不可见的后台事件，但玩家已经介入该事件；"
            "不得再寻找或制造 entry point，不得改变程序判定的 outcome_band。"
            "仅当本次行动或结果与该事件已有局势直接相关时，才可把 driving_tension 转译为结果解释中的压力、机会或阻碍。"
        )
    return (
        "DirectorEvent 是玩家不可见的后台事件，不得原文暴露、写成任务目标、改变程序判定的"
        " outcome_band。仅当本次行动或结果与 entry_points 中某个入口存在直接且自然的因果交点时，"
        "才可把该入口转译为结果解释中的现实压力、机会或阻碍；否则不要使用 DirectorEvent。"
    )
