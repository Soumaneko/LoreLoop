export const state = {
  mode: "booting",
  playerState: null,
  modelConfig: null,
  saves: [],
  activeSave: null,
  selectedPendingIndex: 0,
  suggestions: [],
  lastError: null,
  historyLoading: false,
  historyPagingEnabled: false,
};

export const modeLabels = {
  booting: "载入中",
  needs_model: "等待模型配置",
  needs_init: "等待启程",
  idle: "等待行动",
  generating: "世界回应中",
  pending: "等待确认",
  confirming: "正在确认",
  switching: "切换旅程",
  error: "需要处理",
};

export function setMode(mode) {
  state.mode = mode;
}

export function activePending() {
  return state.playerState?.pending?.[0] || null;
}

export function selectedVariant() {
  const pending = activePending();
  return pending?.variants?.[state.selectedPendingIndex] || null;
}

export function draftKey(turnId) {
  return `tvg:draft:${state.activeSave || "default"}:${turnId}`;
}

export function readDraft(variant) {
  if (!variant?.turn_id) return variant?.scene_text || "";
  return localStorage.getItem(draftKey(variant.turn_id)) ?? variant.scene_text ?? "";
}

export function hasDraft(variant) {
  if (!variant?.turn_id) return false;
  const draft = localStorage.getItem(draftKey(variant.turn_id));
  return draft !== null && draft !== (variant.scene_text ?? "");
}

export function writeDraft(variant, value) {
  if (!variant?.turn_id) return;
  if (value === (variant.scene_text ?? "")) {
    clearDraft(variant);
    return;
  }
  localStorage.setItem(draftKey(variant.turn_id), value);
}

export function clearDraft(variant) {
  if (variant?.turn_id) localStorage.removeItem(draftKey(variant.turn_id));
}

export function clearDrafts(pending) {
  for (const variant of pending?.variants || []) {
    clearDraft(variant);
  }
}
