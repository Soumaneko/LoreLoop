"""Player-facing operational notices; no model text or story secrets are projected."""

NOTICE_MESSAGES = {
    "scene_facts_degraded": "场景事实整理未完成，本轮世界状态和人物档案更新可能不完整。",
    "npc_mentions_degraded": "人物出场识别未完成，部分人物档案可能更新失败。可在左侧【人物档案】手动补充。",
    "canon_query_fallback": "历史查询规划未完成，正文质量可能受到影响。如有问题可【编辑正文】或重新尝试。",
    "rewrite_strategy_fallback": "自动修文未完成，原文已保留。如有需要可【编辑正文】或重新尝试。",
    "confirmation_maintenance_schedule_failed": "回合已确认，但后台摘要整理未能安排；下次确认回合时会再次尝试安排。",
    "npc_update_partial": "部分人物档案更新失败。可在左侧【人物档案】手动补充。",
    "context_extract_failed": "早期记忆提取未完成，正文质量可能受到影响。如有问题可【编辑正文】或重新尝试。",
    "action_extract_fallback": "行动解析未完成，正文质量可能受到影响。如有问题可【编辑正文】或重新尝试。",
    "roll_modifier_fallback": "骰点加值计算未完成，正文质量可能受到影响。如有问题可【编辑正文】或重新尝试。",
    "director_decision_fallback": "幕后局势推演未完成，本次未选出可用的幕后安排。",
    "director_execution_fallback": "幕后安排未完成，导演系统可能受到影响。可尝试【撤销】以重新确认。",
    "director_event_review_fallback": "幕后事件审查未完成，导演系统可能受到影响。可尝试【撤销】以重新确认。",
    "narrative_consistency_fallback": "【后校验】未完成，正文质量可能受到影响。如有问题可【编辑正文】或重新尝试。",
    "narrative_revision_fallback": "自动修文未完成，正文质量可能受到影响。如有问题可【编辑正文】或重新尝试。",
    "scene_summary_fallback": "摘要出现问题。可尝试【撤销】以重新确认。",
    "scene_summary_condense_skipped": "本次摘要压缩已跳过，原有摘要仍保留；下次确认回合时会再次尝试。",
    "state_update_fallback": "世界状态更新未完成，未来剧情可能出现问题。可尝试【撤销】以重新确认。",
    "continuity_fallback": "连续性记录提取未完成，未来剧情可能出现问题。可尝试【撤销】以重新确认。",
    "npc_batch_fallback": "人物档案更新未完成，未来剧情可能出现问题。可尝试【撤销】以重新确认。",
    "action_preview_fallback": "行动建议未生成。",
}


def notices_from_event(step: str, data: dict) -> list[dict[str, str]]:
    codes = list(data.get("warning_codes") or [])
    codes.extend(item.get("code") for item in data.get("warnings", []) if isinstance(item, dict))
    if step == "context_extract_failed":
        codes.append("context_extract_failed")
    if step == "narrative_checked" and data.get("validation_skipped"):
        codes.append("narrative_consistency_fallback")
    if step == "feedback_rewrite_strategy_selected" and data.get("reason_code") == "strategy_unavailable":
        codes.append("rewrite_strategy_fallback")
    if step == "npc_profile_updated" and any(
        str(code).startswith(("invalid_", "missing_", "unknown_", "unresolved_")) for code in codes
    ):
        codes.append("npc_update_partial")
    return [{"code": code, "message": NOTICE_MESSAGES[code]}
            for code in dict.fromkeys(codes) if code in NOTICE_MESSAGES]
