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
  records: null,
  recordsDrawerOpen: false,
  readerPreferences: {
    font_size: "16.5",
    font_family: "serif",
    theme: "dark",
  },
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
  if (!(mode in modeLabels)) {
    throw new Error(`Unknown player mode: ${mode}`);
  }
  state.mode = mode;
}

export function activePending() {
  return state.playerState?.pending?.[0] || null;
}

export function selectedVariant() {
  const pending = activePending();
  return pending?.variants?.[state.selectedPendingIndex] || null;
}

export function readDraft(variant) {
  return variant?.saved_scene_text ?? variant?.scene_text ?? "";
}

export function hasDraft(variant) {
  return variant?.saved_scene_text !== null
    && variant?.saved_scene_text !== undefined;
}

export function applySavedDraft(variant, payload) {
  if (!variant || !payload) return;
  variant.scene_text = payload.scene_text ?? variant.scene_text;
  variant.saved_scene_text = payload.saved_scene_text ?? null;
}
