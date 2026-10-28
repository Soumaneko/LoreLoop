export async function readRefreshSnapshot({ request, loadRecords, loadEvents, saveId, earliestTurn }) {
  const [saves, modelConfig, recovery] = await Promise.all([
    request("/api/player/saves"),
    request("/api/player/model-config"),
    request("/api/player/operations/recoverable"),
  ]);
  if (saveId != null && saves.active !== saveId) throw new Error("当前存档已改变，请重新选择存档后刷新。");
  if (!modelConfig.configured) throw new Error("模型服务尚未配置，请先检查模型设置。");
  // Refresh never resumes or acknowledges operations, which can write saved progress.
  if (recovery.operations?.length) throw new Error("有尚未处理的操作，请先核实或恢复该操作，再刷新状态。");
  const playerState = await request("/api/player/state");
  while (earliestTurn != null && playerState.history_pagination?.has_more
    && playerState.history?.length && playerState.history[0].turn_index > earliestTurn) {
    const before = playerState.history[0].turn_index;
    const page = await request(`/api/player/history?before_turn=${encodeURIComponent(before)}&limit=20`);
    const earlier = (page.history || []).filter(turn => turn.turn_index < before);
    if (!earlier.length) throw new Error("较早的正文未能完整读取，请重试刷新。");
    playerState.history = [...earlier, ...playerState.history];
    playerState.history_pagination = page.pagination;
  }
  const creator = playerState.experience_mode === "creator";
  const [records, events] = await Promise.all([
    loadRecords(creator ? "advanced" : "general"),
    creator ? loadEvents() : Promise.resolve(null),
  ]);
  return { saves, modelConfig, playerState, records, events: events?.events || null };
}
