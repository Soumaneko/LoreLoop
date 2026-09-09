export const PROGRESS_LABELS = {
  state_loaded: "正在读取世界状态",
  npc_profiles_loaded: "正在载入人物档案",
  action_parsed: "正在解析行动意图",
  context_facts_done: "前情已检索 · 正在推演因果",
  rolls_done: "检定已结算 · 正在推演",
  action_judgments_done: "机制判定达成 · 正在构思正文",
  scene_stream_started: "正在书写剧情正文",
  canon_query_started: "正在核对历史事实",
  canon_queries_generated: "历史查询已规划",
  canon_evidence_retrieved: "历史证据已比对",
  narrative_check_started: "正在检查前后逻辑",
  narrative_checked: "前后逻辑检查完成",
  narrative_revision_started: "发现逻辑偏差，正在自动修订",
  narrative_revised: "正文修订完成",
  feedback_rewrite_started: "正在理解修改意见",
  feedback_rewrite_strategy_selected: "修改方式已经确定",
  opening_rewrite_started: "正在重新生成开场正文",
  feedback_source_rewrite_done: "局部修订完成",
  feedback_recompose_done: "重新构思完成",
  confirm_started: "正在读取最终正文",
  scene_facts_done: "场景事实已提取 · 正在更新世界状态",
  state_update_done: "世界状态已更新 · 正在生成回合摘要",
  scene_summarized: "回合摘要已生成 · 正在进入并行确认收尾",
  continuity_projected: "未决事项已整理",
  npc_profile_updated: "正在更新人物信息",
  action_preview_done: "下一步方向已经生成",
  npc_profiles_updated: "人物信息已更新",
  campaign_setup_started: "正在生成公开世界惯例",
  initialization_started: "正在准备开场",
  opening_foundation_ready: "私有开局基础已经准备完成",
  opening_settings_started: "正在建立世界设定",
  opening_settings_done: "世界设定已经建立",
  opening_scene_done: "开场正文已经生成",
  initialization_pending: "正在保存待确认开场",
};

export function progressLabel(step) {
  return PROGRESS_LABELS[step] || "";
}

// The confirmation second wave runs three nodes concurrently, so sibling
// completion events arrive in arbitrary order. The tracker below turns them
// into an accurate "done vs still waiting" label instead of leaving a stale
// sibling label on screen while the wave is stuck on its slowest node.
export const CONFIRMATION_TAIL_STEPS = [
  "action_preview_done",
  "continuity_projected",
  "npc_profile_updated",
];

const CONFIRMATION_TAIL_NAMES = {
  action_preview_done: "下一步方向",
  continuity_projected: "未决事项",
  npc_profile_updated: "人物信息",
};

export function isConfirmationTailStep(step) {
  return CONFIRMATION_TAIL_STEPS.includes(step);
}

export function tailProgressLabel(seenSteps) {
  const seen = new Set(seenSteps || []);
  const done = CONFIRMATION_TAIL_STEPS.filter((step) => seen.has(step)).map(
    (step) => CONFIRMATION_TAIL_NAMES[step],
  );
  const pending = CONFIRMATION_TAIL_STEPS.filter((step) => !seen.has(step)).map(
    (step) => CONFIRMATION_TAIL_NAMES[step],
  );
  if (!pending.length) return "并行确认收尾完成 · 正在写入旅程";
  if (!done.length) return `正在并行确认收尾 · 等待${pending.join("、")}`;
  return `已完成${done.join("、")} · 正在等待${pending.join("、")}`;
}
