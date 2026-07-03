import json

from .schemas import DirectorEvent


def _recent_archived_projection(recent_archived_event: DirectorEvent | None) -> dict | None:
    if recent_archived_event is None or not recent_archived_event.resolved_by_hook:
        return None
    data = {
        "status": "recently_archived",
        "event_id": recent_archived_event.event_id,
        "title": recent_archived_event.title,
        "resolved_turn": recent_archived_event.resolved_turn
        or recent_archived_event.updated_turn,
        "resolved_by_hook": recent_archived_event.resolved_by_hook,
        "message": (
            f"由于{recent_archived_event.resolved_by_hook}，"
            f"『{recent_archived_event.title}』事件已收束。"
        ),
    }
    if recent_archived_event.resolved_reason:
        data["resolved_reason"] = recent_archived_event.resolved_reason
    return data


def _active_event_projection(event: DirectorEvent) -> dict:
    data = event.semantic_view()
    if event.phase == "crisis":
        data.pop("known_to_player", None)
    if event.phase == "latent":
        data.pop("manifesting_prevention_hooks", None)
        data.pop("crisis_resolution_hooks", None)
    elif event.phase == "manifesting":
        data.pop("crisis_resolution_hooks", None)
    elif event.phase == "crisis":
        data.pop("manifesting_prevention_hooks", None)
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
    data = _active_event_projection(event)
    return json.dumps(data, ensure_ascii=False, indent=2)


def plot_outline_director_event_instructions(
    event: DirectorEvent | None,
    *,
    recent_archived_event: DirectorEvent | None = None,
) -> str:
    if event is None and recent_archived_event is not None and recent_archived_event.resolved_by_hook:
        return (
            "DirectorEvent 最近已收束。director_event_json 中的 recently_archived 只表示最近事件"
            "已经因为玩家行动而解决；不得把它继续当作 active 危机推进，不得复燃同一事件。"
            "可以自然表现余波、NPC 反应或世界恢复。"
        )
    if event is None or event.status != "active":
        return ""
    common = (
        "DirectorEvent 是玩家不可见的幕后事件，不得原文暴露、写成任务目标或替玩家决定回应。"
    )
    if event.phase != "crisis":
        common += "不得重复 known_to_player 中已有的信息。\n"
    else:
        common += "\n"
    if event.phase == "latent":
        return common + (
            "当前为潜伏阶段。只有当前行动、地点、NPC 或结果与事件存在自然交点时，"
            "才可通过一个仍能被普通原因解释的局部异常体现事件边缘；没有自然交点时可以不使用。"
            "不得强行插入无关传闻，也不得完整揭露事件主体、目的或规模。\n"
            "在 director_event_note 中简述本回合使用方式；未使用时说明缺少自然交点。"
        )
    if event.phase == "manifesting":
        return common + (
            "当前为显现阶段。事件已成为正在改变局部世界的现实背景。主动考虑其对治安、交通、"
            "物价、组织行动、NPC 决策或环境状态造成的影响；重点表现既有世界影响、反馈和阻力，"
            "而不是继续投放暧昧线索。若当前场景确实隔绝或无法自然受到影响，可以不体现。\n"
            "必须让局势露出可被玩家利用的抓手；这些抓手可以呼应"
            " manifesting_prevention_hooks，但不得写成任务清单或替玩家决定行动。\n"
            "在 director_event_note 中说明体现了何种世界影响，或说明本场景为何未受影响。"
        )
    return common + (
        "当前为危机阶段。事件必须对现实局势产生实质影响，例如风险、阻碍、机会、"
        "冲突、环境变化或原计划受扰。影响必须同时保留可操作抓手；不得只增加惩罚或阻碍。"
        "玩家可以对抗、利用、逃避或旁观，但世界不能忽略危机。\n"
        "必须在 director_event_note 中说明事件如何实质影响了本回合。"
    )


def turn_resolution_director_event_instructions(
    event: DirectorEvent | None,
    *,
    recent_archived_event: DirectorEvent | None = None,
) -> str:
    if event is None and recent_archived_event is not None and recent_archived_event.resolved_by_hook:
        return (
            "DirectorEvent 最近已收束。director_event_json 中的 recently_archived 是已解决事件的"
            "短期上下文；不得把它作为新的风险来源或 active 危机，只可在结果解释中承认其余波。"
        )
    if event is None or event.status != "active":
        return ""
    common = (
        "DirectorEvent 是玩家不可见的幕后事件，不得原文暴露、写成任务目标、改变程序判定的"
        " outcome_band。"
    )
    if event.phase != "crisis":
        common += "不得重复 known_to_player 中已有信息。"
    if event.phase == "latent":
        return common + (
            "当前为潜伏阶段。仅当玩家行动或判定结果与事件存在直接而自然的因果交点时，"
            "才可将一个局部异常作为结果解释的支点；否则不要使用 DirectorEvent。"
        )
    if event.phase == "manifesting":
        return common + (
            "当前为显现阶段。若事件造成的公开世界变化能够影响本次行动，结果解释应考虑这种"
            "治安、交通、资源、组织行动或环境影响；不要退回到重复投放神秘线索。"
            "如果玩家行动命中 manifesting_prevention_hooks 的方向，应体现事件被削弱、延缓或阻断。"
        )
    return common + (
        "当前为危机阶段。行动结果解释和 selected_type 必须考虑危机造成的现实压力与后果，"
        "但仍须尊重玩家行动及 outcome_band；不得把危机写成单向惩罚。"
        "如果玩家行动命中 crisis_resolution_hooks 的方向，应体现危机被收束、转向或失去继续扩大的条件。"
    )
