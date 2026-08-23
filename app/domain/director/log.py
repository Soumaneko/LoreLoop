from __future__ import annotations

from app.domain.director.schemas import DirectorDecisionBatch, DirectorGuidance


def summarize_decision(decision: DirectorDecisionBatch, stores) -> str:
    """Programmatically assemble one-line summaries of a Director decision.

    Names are resolved through the campaign stores so the log stays readable;
    unresolved ids fall back to their raw values.
    """
    lines = []
    for request in decision.requests:
        lines.append(_summarize_request(request, stores))
    return "\n".join(lines) if lines else "无措施"


def summarize_executions(executions, stores) -> str:
    """Summarize the ACTUAL Director confirmation results from the execution batch.

    The confirmation decision only records request intent (e.g. "create_event
    with 2 actors"); the created event id/title and actor names live in each
    execution's ``output`` and are what the next generation turn must know.
    """
    lines = []
    for execution in (executions.executions if executions is not None else []):
        output = execution.output or {}
        skill = execution.skill
        if skill == "create_npc":
            name = output.get("name") or _npc_name(
                stores, output.get("npc_id")
            )
            lines.append(f"创建NPC「{name}」")
        elif skill == "create_event":
            event_id = output.get("event_id")
            title = _event_title(stores, event_id)
            role = output.get("role") or ""
            actors = output.get("actor_names") or []
            actor_text = "、".join(str(actor) for actor in actors)
            lines.append(
                f"创建事件「{title}」({event_id}, {role}, 演员:{actor_text})"
            )
        elif skill == "upgrade_disclosure":
            name = _npc_name(stores, output.get("npc_id"))
            new_level = output.get("new_disclosure_level")
            lines.append(
                f"披露升级「{name}」→{new_level}"
                if new_level
                else f"披露升级「{name}」"
            )
        elif skill == "bind_event_actor":
            event = _event_title(stores, output.get("event_id"))
            npc = _npc_name(stores, output.get("npc_id"))
            lines.append(f"绑定演员「{event}←{npc}」")
        else:
            lines.append(skill)
    return "\n".join(lines) if lines else "无措施"


def assemble_director_log_entry(
    *,
    turn_index: int,
    phase: str,
    decision: DirectorDecisionBatch,
    stores,
    executions=None,
) -> dict:
    measures = summarize_decision(decision, stores)
    if executions is not None and getattr(executions, "executions", None):
        measures = summarize_executions(executions, stores)
    return {
        "turn_index": turn_index,
        "phase": phase,
        "reasoning": decision.reasoning,
        "measures": measures,
    }


def _summarize_request(request, stores) -> str:
    skill = request.skill
    arguments = request.arguments or {}
    if skill == "introduce_npc":
        if "spontaneous" in arguments and isinstance(arguments["spontaneous"], dict):
            name = arguments["spontaneous"].get("name") or "即兴角色"
        else:
            name = _npc_name(stores, arguments.get("npc_id"))
        view_mode = arguments.get("view_mode")
        return f"引入NPC「{name}」({view_mode})" if view_mode else f"引入NPC「{name}」"
    if skill == "upgrade_disclosure":
        name = _npc_name(stores, arguments.get("npc_id"))
        new_level = arguments.get("new_disclosure_level")
        return f"披露升级「{name}」→{new_level}" if new_level else f"披露升级「{name}」"
    if skill == "present_event":
        title = _event_title(stores, arguments.get("event_id"))
        hook = arguments.get("hook_id")
        return f"呈现事件「{title}」" + (f"(hook:{hook})" if hook else "")
    if skill == "intrude_engaged_event":
        title = _event_title(stores, arguments.get("event_id"))
        return f"引入事件「{title}」"
    if skill == "create_npc":
        name = _structured_name(request.guidance) or "未知"
        return f"创建NPC「{name}」依托{_setting_kind(arguments)}"
    if skill == "create_event":
        role = arguments.get("role") or ""
        actors = arguments.get("actors") or []
        return f"创建事件({role}) 演员{len(actors)} 依托{_setting_kind(arguments)}"
    if skill == "bind_event_actor":
        event = _event_title(stores, arguments.get("event_id"))
        npc = _npc_name(stores, arguments.get("npc_id"))
        return f"绑定演员「{event}←{npc}」"
    return skill


def _structured_name(guidance) -> str:
    if isinstance(guidance, DirectorGuidance):
        return guidance.name
    if isinstance(guidance, dict):
        return str(guidance.get("name") or "")
    return ""


def _setting_kind(arguments) -> str:
    requirements = arguments.get("setting_requirements") or []
    if requirements:
        kind = requirements[0].get("kind")
        if kind:
            return str(kind)
    return ""


def _npc_name(stores, npc_id) -> str:
    npc_id = str(npc_id or "")
    if not npc_id:
        return "?"
    try:
        with stores.director_npc as store:
            draft = store.get_by_id(npc_id)
            if draft is not None and draft.status != "retired":
                return draft.name
    except Exception:
        pass
    try:
        with stores.npc_profile as store:
            profile = store.get_by_id(npc_id)
            if profile is not None:
                return profile.name
    except Exception:
        pass
    return npc_id


def _event_title(stores, event_id) -> str:
    event_id = str(event_id or "")
    if not event_id:
        return "?"
    try:
        with stores.director_event as store:
            event = store.get_by_id(event_id)
            if event is not None:
                return event.title
    except Exception:
        pass
    return event_id
