import json

from .schemas import DirectorEvent


def project_director_event(event: DirectorEvent | None) -> str:
    if event is None:
        return "null"
    return json.dumps(event.semantic_view(), ensure_ascii=False, indent=2)


def is_hidden_active_director_event(event: DirectorEvent | None) -> bool:
    return (
        event is not None
        and event.status == "active"
        and event.exposure_phase == "hidden"
    )


def plot_outline_director_event_instructions(event: DirectorEvent | None) -> str:
    if not is_hidden_active_director_event(event):
        return ""
    return (
        "DirectorEvent 是幕后叙事方向，玩家不可见。使用规则：\n"
        "- 本回合必须在不违背规则的前提下，至少用一个 beat 或可感知的世界反应"
        "（痕迹、传闻、NPC 行动、环境变化、预兆、声响、目击等）体现它。\n"
        "- 转化为可感知的世界反应，不得原文暴露或写成任务目标。\n"
        "- **必须在 `director_event_note` 中用 1 句话说明本回合如何处理了 DirectorEvent**。"
        "DirectorEvent 为 null 时填\"无活跃导演事件\"。"
    )


def turn_resolution_director_event_instructions(event: DirectorEvent | None) -> str:
    if not is_hidden_active_director_event(event):
        return ""
    return (
        "DirectorEvent 是幕后叙事方向，玩家不可见，不得原文暴露，也不得写成任务目标。"
        "本回合必须使用它。\n"
        "- 若不违背玩家行动和判定结果，至少在 selected_type 或一个 resolved_effect 中"
        "以隐性压力、预兆、巧合、阻碍或边缘线索体现它。"
    )
