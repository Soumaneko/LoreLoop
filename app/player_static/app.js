import { acknowledgeOperation, cancelOperation, downloadSave, importSaveArchive, request, stream, subscribe } from "./api.js";
import {
  enhanceCustomSelect,
  syncCustomSelect,
  syncCustomSelects,
} from "./custom_select.js";
import { createReaderPreferencesController } from "./reader_preferences.js";
import {
  installClientDiagnostics,
  reportClientError,
} from "./client_diagnostics.js";
import {
  activePending,
  applySavedDraft,
  hasDraft,
  modeLabels,
  readDraft,
  selectedVariant,
  setMode,
  state,
} from "./state.js";
import { migrateStorageKey } from "./storage_migration.js";

const $ = (id) => document.getElementById(id);
const els = {
  worldTime: $("worldTime"), worldLocation: $("worldLocation"), statusPill: $("statusPill"),
  story: $("story"), welcome: $("welcome"), history: $("history"),
  activePlayerAction: $("activePlayerAction"),
  streamingCard: $("streamingCard"), streamingText: $("streamingText"), streamingLabel: $("streamingLabel"),
  stopGenerationBtn: $("stopGenerationBtn"),
  pendingCard: $("pendingCard"), suggestions: $("suggestions"), suggestionList: $("suggestionList"),
  turnNavigator: $("turnNavigator"), turnNavigatorList: $("turnNavigatorList"),
  turnNavigatorLatest: $("turnNavigatorLatest"),
  errorCard: $("errorCard"), errorMessage: $("errorMessage"), dismissErrorBtn: $("dismissErrorBtn"),
  playerInput: $("playerInput"), generateBtn: $("generateBtn"), inputHint: $("inputHint"),
  composerModeBadge: $("composerModeBadge"),
  drawer: $("drawer"), leftDrawer: $("leftDrawer"), drawerBackdrop: $("drawerBackdrop"), openDrawerBtn: $("openDrawerBtn"), closeDrawerBtn: $("closeDrawerBtn"), openLeftDrawerBtn: $("openLeftDrawerBtn"), closeLeftDrawerBtn: $("closeLeftDrawerBtn"),
  saveSelect: $("saveSelect"), newSaveBtn: $("newSaveBtn"), manageSavesBtn: $("manageSavesBtn"), playerSummary: $("playerSummary"), sceneSummary: $("sceneSummary"), resourceSummary: $("resourceSummary"),
  modelSettingsBtn: $("modelSettingsBtn"), modelConfigDialog: $("modelConfigDialog"), modelConfigForm: $("modelConfigForm"),
  closeModelConfigBtn: $("closeModelConfigBtn"), modelConfigIntro: $("modelConfigIntro"), modelApiKey: $("modelApiKey"),
  modelKeyHint: $("modelKeyHint"), modelBaseUrl: $("modelBaseUrl"), modelName: $("modelName"),
  modelThinking: $("modelThinking"), modelTestResult: $("modelTestResult"), testModelBtn: $("testModelBtn"), saveModelBtn: $("saveModelBtn"),
  feedbackRewriteDialog: $("feedbackRewriteDialog"), feedbackRewriteForm: $("feedbackRewriteForm"),
  feedbackRewriteInput: $("feedbackRewriteInput"), feedbackRewriteCancelBtn: $("feedbackRewriteCancelBtn"),
  difficultySelect: $("difficultySelect"), diceDisplaySelect: $("diceDisplaySelect"), fontSizeSelect: $("fontSizeSelect"), fontFamilySelect: $("fontFamilySelect"), themeSelect: $("themeSelect"), undoBtn: $("undoBtn"), redoBtn: $("redoBtn"),
  codexDialog: $("codexDialog"), codexTitle: $("codexTitle"), codexContent: $("codexContent"), closeCodexBtn: $("closeCodexBtn"),
  codexConfirmDialog: $("codexConfirmDialog"), codexConfirmEyebrow: $("codexConfirmEyebrow"), codexConfirmTitle: $("codexConfirmTitle"), codexConfirmMessage: $("codexConfirmMessage"), codexConfirmCancelBtn: $("codexConfirmCancelBtn"), codexConfirmOkBtn: $("codexConfirmOkBtn"),
  saveDialog: $("saveDialog"), saveForm: $("saveForm"), saveNameInput: $("saveNameInput"), saveCancelBtn: $("saveCancelBtn"),
  saveManagerDialog: $("saveManagerDialog"), closeSaveManagerBtn: $("closeSaveManagerBtn"), saveCardList: $("saveCardList"),
  importSaveBtn: $("importSaveBtn"), importSaveInput: $("importSaveInput"), importSaveForm: $("importSaveForm"),
  importSaveName: $("importSaveName"), importSaveFileName: $("importSaveFileName"), cancelImportSaveBtn: $("cancelImportSaveBtn"),
  saveManagerError: $("saveManagerError"),
  initDialog: $("initDialog"), initForm: $("initForm"), initSubmitBtn: $("initSubmitBtn"), initError: $("initError"),
  initConfigFields: $("initConfigFields"), initOriginalFields: $("initOriginalFields"), initFanficFields: $("initFanficFields"),
  initFanfic: $("initFanfic"), initAbilitySystem: $("initAbilitySystem"), initCustomAbilityWrap: $("initCustomAbilityWrap"),
  initConfigActions: $("initConfigActions"), storyDirectionActions: $("storyDirectionActions"), storyDirectionStage: $("storyDirectionStage"),
  storyDirectionBtn: $("storyDirectionBtn"), regenerateStoryDirectionBtn: $("regenerateStoryDirectionBtn"), editInitConfigBtn: $("editInitConfigBtn"),
  storyDirectionContent: $("storyDirectionContent"), storyDirectionCurrency: $("storyDirectionCurrency"),
  addCoreMemoryBtn: $("addCoreMemoryBtn"), extraCoreMemoryList: $("extraCoreMemoryList"), initStageHint: $("initStageHint"),
  toast: $("toast"),
};

const progressLabels = {
  state_loaded: "正在唤醒世界",
  entity_profiles_loaded: "正在理解场景",
  action_parsed: "正在理解你的行动",
  rolls_done: "命运已经掷下",
  turn_resolution_done: "正在推演结果",
  scene_stream_started: "世界正在回应",
  canon_query_started: "正在核对历史事实",
  canon_queries_generated: "历史查询已规划",
  canon_evidence_retrieved: "历史证据已检索",
  narrative_check_started: "正在检查前后逻辑",
  narrative_checked: "正在检查前后逻辑",
  narrative_revision_started: "发现明确问题，正在重新编写",
  narrative_revised: "修订完成",
  feedback_rewrite_started: "正在理解修改意见",
  feedback_rewrite_strategy_selected: "修改方式已经确定",
  feedback_source_rewrite_done: "局部修订完成",
  feedback_recompose_done: "重新构思完成",
  confirm_started: "正在读取最终正文",
  scene_facts_done: "正在提取场景事实",
  state_update_done: "正在更新世界状态",
  memory_draft_extract_done: "正在整理本轮记忆",
  entity_extract_done: "正在识别重要实体",
  npc_extract_done: "正在整理人物信息",
  memory_draft_metadata_filled: "正在准备确认结果",
  action_preview_done: "下一步方向已经生成",
  entity_profiles_updated: "正在写入旅程",
  story_direction_started: "正在规划长期故事方向",
  initialization_started: "正在准备开场",
  initial_world_settings_ready: "初始世界设定已经建立",
  opening_scene_done: "开场已经生成",
  initialization_pending: "正在保存待确认开场",
};
let streamBuffer = "";
let streamFrame = 0;
let followStream = true;
let initConfigSnapshot = null;
let saveManagerBusy = false;
let selectedImportFile = null;
let modelConfigBusy = false;
let modelConnectionVerified = false;
let activeGenerationRun = null;
let historyLoadPromise = null;
let historyLoadSaveName = null;
let navigationRenderSignature = "";
let navigationObserver = null;
let navigationObservedAnchors = [];
let navigationObservedKey = null;
let navigationActiveKey = null;
let navigationButtons = new Map();
let navigationJumpSequence = 0;
let pinnedNavigationKey = null;
let navigationSettleTimer = 0;
let navigationScrollEndHandler = null;
let highlightCacheSignature = "";
let highlightCache = null;
let highlightCacheSource = null;
const highlightProfiles = new WeakMap();
let highlightPointerState = null;
const HISTORY_PAGE_SIZE = 5;
const HISTORY_NAVIGATION_LOAD_SIZE = 20;
const PLAYER_SHELL_CACHE_KEY = "loreloop:player-shell";
const CONFIRMATION_IN_FLIGHT_KEY = "loreloop:confirmation-in-flight";
const GENERATION_IN_FLIGHT_KEY = "loreloop:generation-in-flight";
const LEGACY_PLAYER_SHELL_CACHE_KEY = "tvg:player-shell";
const LEGACY_CONFIRMATION_IN_FLIGHT_KEY = "tvg:confirmation-in-flight";
const LEGACY_GENERATION_IN_FLIGHT_KEY = "tvg:generation-in-flight";

migrateStorageKey(
  sessionStorage,
  LEGACY_PLAYER_SHELL_CACHE_KEY,
  PLAYER_SHELL_CACHE_KEY,
);
migrateStorageKey(
  sessionStorage,
  LEGACY_CONFIRMATION_IN_FLIGHT_KEY,
  CONFIRMATION_IN_FLIGHT_KEY,
);
migrateStorageKey(
  sessionStorage,
  LEGACY_GENERATION_IN_FLIGHT_KEY,
  GENERATION_IN_FLIGHT_KEY,
);

const readerPreferences = createReaderPreferencesController({
  elements: {
    fontSize: els.fontSizeSelect,
    fontFamily: els.fontFamilySelect,
    theme: els.themeSelect,
  },
  request,
  state,
  showToast,
  showError,
});
installClientDiagnostics();

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { els.toast.hidden = true; }, 2600);
}

function showError(error, fallback = "之前的故事没有受到影响，你可以重新尝试。") {
  reportClientError(error, "displayed_error");
  state.lastError = error;
  setMode("error");
  els.errorMessage.textContent = error?.message || fallback;
  els.errorCard.hidden = false;
  renderControls();
  scrollToBottom();
}

function clearError() {
  state.lastError = null;
  els.errorCard.hidden = true;
  const pending = activePending();
  setMode(!state.modelConfig?.configured ? "needs_model" : state.playerState?.needs_init ? "needs_init" : pending ? "pending" : "idle");
  renderControls();
}

function setInputHintText(actionText) {
  if (!els.inputHint) return;
  els.inputHint.replaceChildren(
    node("kbd", "", "Ctrl"),
    document.createTextNode(" / "),
    node("kbd", "", "⌘"),
    document.createTextNode(" + "),
    node("kbd", "", "Enter"),
    document.createTextNode(` ${actionText}`),
  );
}

function renderControls({ updateNavigation = true } = {}) {
  const busy = saveManagerBusy || modelConfigBusy || ["booting", "needs_model", "generating", "confirming", "switching"].includes(state.mode);
  const pending = Boolean(activePending());
  els.statusPill.dataset.mode = state.mode;
  els.statusPill.querySelector("span").textContent = modeLabels[state.mode] || "等待行动";

  if (pending && !busy) {
    if (els.composerModeBadge) els.composerModeBadge.hidden = true;
    els.playerInput.disabled = false;
    els.playerInput.placeholder = "对这段正文有意见？在此输入修改要求（如“写到门前即可停下”）...";
    setInputHintText("提交修改意见");
    els.generateBtn.disabled = !els.playerInput.value.trim();
    const btnSpan = els.generateBtn.querySelector("span");
    if (btnSpan) btnSpan.textContent = "意见重写";
  } else {
    if (els.composerModeBadge) els.composerModeBadge.hidden = true;
    els.playerInput.disabled = busy || state.playerState?.needs_init;
    els.playerInput.placeholder = "你打算怎么做？";
    setInputHintText("执行行动");
    els.generateBtn.disabled = busy || pending || state.playerState?.needs_init || !els.playerInput.value.trim();
    const btnSpan = els.generateBtn.querySelector("span");
    if (btnSpan) btnSpan.textContent = "执行行动";
  }

  els.saveSelect.disabled = busy;
  els.newSaveBtn.disabled = busy;
  els.manageSavesBtn.disabled = busy;
  els.modelSettingsBtn.disabled = modelConfigBusy || ["generating", "confirming", "switching"].includes(state.mode);
  const controls = state.playerState?.history_controls || {};
  const pendingIds = (activePending()?.variants || []).map((variant) => variant.turn_id);
  const canRedoPending = pendingIds.some((turnId) => (controls.redo_pending_turn_ids || []).includes(turnId));
  els.undoBtn.disabled = busy || pending || !controls.can_undo;
  els.redoBtn.disabled = busy || (pending && !canRedoPending) || !controls.can_redo;
  els.difficultySelect.disabled = busy;
  els.diceDisplaySelect.disabled = busy;
  syncCustomSelects([
    els.saveSelect,
    els.difficultySelect,
    els.diceDisplaySelect,
    els.fontSizeSelect,
    els.fontFamilySelect,
    els.themeSelect,
  ]);
  if (updateNavigation) renderTurnNavigator();
}

function renderHeader() {
  const campaign = state.playerState?.campaign || {};
  els.worldTime.textContent = campaign.world_time || "尚未启程";
  els.worldLocation.textContent = campaign.location || "等待世界载入";
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function currentHighlightMatcher() {
  const highlights = state.playerState?.highlights || [];
  const signature = highlights
    .map((item) => `${item.kind || ""}\0${item.surface || ""}`)
    .join("\u0001");
  if (
    highlightCache
    && highlights === highlightCacheSource
    && signature === highlightCacheSignature
  ) {
    return highlightCache;
  }
  const terms = [...highlights].sort((a, b) => b.surface.length - a.surface.length);
  const lookup = new Map(terms.map((item) => [item.surface.toLocaleLowerCase(), item]));
  const pattern = terms.map((item) => escapeRegExp(item.surface)).join("|");
  highlightCacheSignature = signature;
  highlightCacheSource = highlights;
  highlightCache = {
    lookup,
    pattern,
  };
  return highlightCache;
}

function appendHighlightedText(element, text) {
  const { lookup, pattern } = currentHighlightMatcher();
  if (!text || !pattern) {
    element.textContent = text || "";
    return element;
  }
  const matcher = new RegExp(pattern, "giu");
  let cursor = 0;
  for (const match of text.matchAll(matcher)) {
    const start = match.index ?? 0;
    if (start > cursor) element.appendChild(document.createTextNode(text.slice(cursor, start)));
    const value = match[0];
    const highlight = lookup.get(value.toLocaleLowerCase());
    const mark = node("mark", `story-highlight story-highlight-${highlight?.kind || "entity"}`, value);
    if (highlight?.profile) {
      mark.classList.add("story-highlight-clickable");
      mark.tabIndex = 0;
      mark.setAttribute("role", "button");
      mark.setAttribute("aria-label", `查看${value}的典籍条目`);
      highlightProfiles.set(mark, highlight.profile);
    }
    element.appendChild(mark);
    cursor = start + value.length;
  }
  if (cursor < text.length) element.appendChild(document.createTextNode(text.slice(cursor)));
  return element;
}

function highlightedTarget(event) {
  return event.target.closest?.(".story-highlight-clickable") || null;
}

function handleHighlightPointerDown(event) {
  const target = highlightedTarget(event);
  if (!target) return;
  highlightPointerState = {
    target,
    startX: event.clientX,
    startY: event.clientY,
    dragging: false,
  };
  if (event.detail > 1) event.preventDefault();
}

function handleHighlightPointerMove(event) {
  const pointer = highlightPointerState;
  if (!pointer || pointer.target !== highlightedTarget(event)) return;
  if (Math.hypot(
    event.clientX - pointer.startX,
    event.clientY - pointer.startY,
  ) > 4) {
    pointer.dragging = true;
  }
}

function handleHighlightClick(event) {
  const target = highlightedTarget(event);
  const profile = target && highlightProfiles.get(target);
  if (!target || !profile) return;
  const dragged = highlightPointerState?.target === target
    && highlightPointerState.dragging;
  highlightPointerState = null;
  if (dragged) return;
  event.preventDefault();
  window.getSelection()?.removeAllRanges();
  openCodexProfile(profile, target);
}

function handleHighlightKeydown(event) {
  if (!["Enter", " "].includes(event.key)) return;
  const target = highlightedTarget(event);
  const profile = target && highlightProfiles.get(target);
  if (!target || !profile) return;
  event.preventDefault();
  openCodexProfile(profile, target);
}

function renderTurn(turn, { showClosing = false } = {}) {
  const wrap = node("section", "turn");
  wrap.dataset.turnIndex = String(turn.turn_index);
  const defaultActionText = Number(turn.turn_index) === 0 ? "故事从这里开始" : "继续故事";
  wrap.appendChild(node("div", "player-action", turn.user_input || defaultActionText));
  const card = node("article", "narrative-card");
  const kicker = node("div", "narrative-kicker");
  kicker.append(node("span", "quill", "✦"), node("span", "", `第 ${turn.turn_index} 回合`));
  card.append(kicker, appendHighlightedText(node("div", "scene-text"), turn.scene_text));
  const resolution = renderPlayerResolution(turn.player_resolution);
  if (resolution) card.appendChild(resolution);
  if (showClosing && turn.closing_prompt) {
    card.appendChild(node("div", "closing-prompt", turn.closing_prompt));
  }
  wrap.appendChild(card);
  return wrap;
}

const outcomeLabels = {
  critical_failure: "大失败",
  failure: "失败",
  partial_success: "部分成功",
  success: "成功",
  critical_success: "大成功",
};

function renderPlayerResolution(resolution) {
  const mode = state.playerState?.dice_display || "outcome";
  const actions = (resolution?.actions || []).filter(hasVisibleRoll);
  if (mode === "hidden" || !actions.length) return null;
  const panel = node("section", "resolution-panel");
  actions.forEach((action) => {
    const row = node("div", `resolution-row resolution-${action.outcome_band || "success"}`);
    const heading = node("div", "resolution-heading");
    heading.appendChild(node("span", "resolution-action", action.action_text || "本次行动"));
    row.appendChild(heading);

    if (mode === "outcome") {
      const outcomeSeal = node(
        "strong",
        "resolution-outcome resolution-outcome-line",
        outcomeLabels[action.outcome_band] || "已判定",
      );
      outcomeSeal.classList.add("resolution-seal-badge");
      row.appendChild(outcomeSeal);
    }
    if (mode === "full") {
      row.appendChild(renderRollDetail(action));
    }
    panel.appendChild(row);
  });
  return panel;
}

function hasVisibleRoll(action) {
  return action?.resolution_source === "roll"
    && Array.isArray(action.raw_values)
    && action.raw_values.length > 0;
}

function renderRollDetail(action) {
  const values = action.raw_values || [];
  const keptValue = values.length ? Math.max(...values.map(Number)) : null;
  const modifier = Number(action.modifier_total || 0);
  const finalValue = action.final_value == null ? "?" : action.final_value;
  const sides = action.dice_sides == null ? "?" : action.dice_sides;
  const outcome = outcomeLabels[action.outcome_band] || "已判定";
  const resultTone = ["success", "critical_success"].includes(action.outcome_band)
    ? "positive"
    : ["failure", "critical_failure"].includes(action.outcome_band)
      ? "negative"
      : "partial";

  const detail = node("div", "resolution-detail");
  const diceLine = node("div", "resolution-detail-line resolution-dice-line");

  if (values.length) {
    const _rawRollSummary = `[${values.join(", ")}] → ${keptValue}`;
    const chips = node("span", "dice-chips");
    values.forEach((v) => {
      const num = Number(v);
      const isKept = num === keptValue;
      chips.appendChild(node("kbd", `dice-chip ${isKept ? "dice-kept" : ""}`, String(num)));
    });
    diceLine.append(
      chips,
      node("span", "dice-arrow", "➔"),
      node("span", "dice-kept-label", "保留"),
      node("strong", "dice-kept-value", String(keptValue)),
    );
  } else {
    diceLine.textContent = "骰点未记录";
  }
  detail.appendChild(diceLine);

  const modifiers = action.modifiers || [];
  const modifiersContainer = node("div", "resolution-modifiers-container");
  
  if (modifiers.length) {
    modifiers.forEach((item) => {
      const value = Number(item.value || 0);
      const isPos = value > 0;
      const valueText = isPos ? `+${value}` : String(value);
      modifiersContainer.appendChild(node(
        "div",
        `resolution-modifier-reason resolution-modifier-pill resolution-modifier-${isPos ? "positive" : "negative"}`,
        `${item.label} ${valueText}`,
      ));
    });
  } else if (modifier === 0) {
    modifiersContainer.appendChild(node(
      "div",
      "resolution-modifier-reason resolution-modifier-pill",
      "无额外修正",
    ));
  } else {
    modifiersContainer.appendChild(node(
      "div",
      "resolution-modifier-reason resolution-modifier-pill",
      "修正明细未记录，请使用“重新判定”生成完整明细",
    ));
  }
  detail.appendChild(modifiersContainer);

  const _testFallbackSummary = `最终值 ${finalValue}/${sides}　${outcome}`;
  const modText = modifier >= 0 ? `+${modifier}` : String(modifier);
  const mathText = keptValue != null
    ? `${keptValue} (${modText}) = 最终值 ${finalValue} / ${sides}`
    : `最终值 ${finalValue} / ${sides}`;

  const finalRow = node("div", `resolution-detail-line resolution-final-line resolution-final-${resultTone}`);
  finalRow.appendChild(node("span", "resolution-math-text", mathText));

  // 大成功/成功印章徽章，右下角同行展示
  const seal = node(
    "strong",
    "resolution-outcome resolution-outcome-line",
    outcome,
  );
  seal.classList.add("resolution-seal-badge");
  finalRow.appendChild(seal);

  detail.appendChild(finalRow);
  return detail;
}

function renderHistory() {
  els.history.replaceChildren();
  const history = state.playerState?.history || [];
  els.welcome.hidden = history.length > 0 || Boolean(activePending());
  const fragment = document.createDocumentFragment();
  history.forEach((turn, index) => fragment.appendChild(renderTurn(turn, {
    showClosing: index === history.length - 1 && !activePending(),
  })));
  els.history.appendChild(fragment);
}

function navigationEntries() {
  const confirmed = (state.playerState?.history_navigation || []).map((item) => ({
    key: `turn:${item.turn_index}`,
    turnIndex: Number(item.turn_index),
    label: Number(item.turn_index) === 0 ? "开场" : `回合${item.turn_index}`,
    excerpt: item.user_input_excerpt || "",
    status: "confirmed",
  }));
  const pending = activePending();
  if (pending) {
    const variant = selectedVariant() || pending.variants?.[0];
    const turnIndex = Number(variant?.turn_index ?? 0);
    const regenerating = state.mode === "generating" && Boolean(activeGenerationRun);
    confirmed.push({
      key: regenerating ? "streaming" : "pending",
      turnIndex,
      label: pending.kind === "opening" ? "开场" : `回合${turnIndex}`,
      excerpt: pending.user_input || "",
      status: state.mode === "confirming"
        ? "confirming"
        : regenerating
          ? "regenerating"
          : "pending",
    });
  } else if (state.mode === "generating" && activeGenerationRun) {
    const turnIndex = activeGenerationRun.turnIndex ?? nextGeneratedTurnIndex();
    confirmed.push({
      key: "streaming",
      turnIndex,
      label: turnIndex === null ? "正在生成" : `回合${turnIndex}`,
      excerpt: activeGenerationRun.userInput || "",
      status: "generating",
    });
  }
  return confirmed;
}

function navigationItemText(entry) {
  if (entry.status === "pending") return `${entry.label} · 待确认`;
  if (entry.status === "confirming") return `${entry.label} · 正在确认`;
  if (entry.status === "regenerating") return `${entry.label} · 正在生成新版本`;
  if (entry.status === "generating") return `${entry.label} · 正在生成`;
  return entry.label;
}

function renderTurnNavigator() {
  if (!els.turnNavigator || !els.turnNavigatorList) return;
  const entries = navigationEntries();
  const signature = JSON.stringify(entries);
  els.turnNavigator.hidden = entries.length === 0;
  if (signature === navigationRenderSignature) {
    refreshNavigationObserver();
    return;
  }
  navigationRenderSignature = signature;
  navigationButtons = new Map();
  navigationActiveKey = null;
  els.turnNavigatorList.replaceChildren();
  const fragment = document.createDocumentFragment();
  const totalEntries = entries.length;
  for (let i = 0; i < totalEntries; i++) {
    const entry = entries[i];
    const isOpening = entry.turnIndex === 0 || entry.label === "开场";
    const isLatest = i === totalEntries - 1;
    const isMultipleOfFive = entry.turnIndex !== null && entry.turnIndex > 0 && (entry.turnIndex % 5 === 0);
    const showLabel = isOpening || isLatest || isMultipleOfFive;

    const isMilestone = isOpening || isMultipleOfFive;
    const button = node("button", `turn-nav-item turn-nav-${entry.status}${showLabel ? " show-label" : ""}${isMilestone ? " turn-nav-milestone" : ""}`);
    button.type = "button";
    button.dataset.navKey = entry.key;
    if (entry.turnIndex !== null) button.dataset.turnIndex = String(entry.turnIndex);
    const text = navigationItemText(entry);
    button.setAttribute(
      "aria-label",
      entry.excerpt ? `${text}：${entry.excerpt}` : text,
    );
    button.title = entry.excerpt ? `${text}\n${entry.excerpt}` : text;
    button.append(
      node("span", "turn-nav-label", text),
      node("span", "turn-nav-dot"),
    );
    button.onclick = () => {
      if (entry.status === "confirmed") jumpToHistoryTurn(entry.turnIndex);
      else jumpToCurrentTurn(entry.key);
    };
    navigationButtons.set(entry.key, button);
    fragment.appendChild(button);
  }
  els.turnNavigatorList.appendChild(fragment);
  refreshNavigationObserver({ force: true });
}

function visibleNavigationAnchors() {
  const anchors = [...els.history.querySelectorAll(".turn[data-turn-index]")].map(
    (element) => ({
      key: `turn:${element.dataset.turnIndex}`,
      element,
    }),
  );
  if (!els.streamingCard.hidden) {
    anchors.push({ key: "streaming", element: els.streamingCard });
  } else if (!els.pendingCard.hidden) {
    anchors.push({ key: "pending", element: els.pendingCard });
  }
  return anchors;
}

function setNavigationActive(key) {
  const nextKey = pinnedNavigationKey || key;
  if (!nextKey || navigationActiveKey === nextKey) return;
  const previous = navigationButtons.get(navigationActiveKey);
  if (previous) {
    previous.classList.remove("active");
    previous.removeAttribute("aria-current");
  }
  const next = navigationButtons.get(nextKey);
  navigationActiveKey = nextKey;
  if (!next) return;
  next.classList.add("active");
  next.setAttribute("aria-current", "step");
  next.scrollIntoView({ block: "nearest", behavior: "auto" });
}

function navigationKeyAtReadingLine(anchors) {
  if (!anchors.length) return null;
  const readingLine = Math.min(
    Math.max(0, window.innerHeight - 1),
    Math.max(82, window.innerHeight * 0.3),
  );
  let active = anchors[0];
  for (const candidate of anchors) {
    if (candidate.element.getBoundingClientRect().top <= readingLine) {
      active = candidate;
    } else {
      break;
    }
  }
  return active.key;
}

function sameNavigationAnchors(left, right) {
  return left.length === right.length && left.every(
    (anchor, index) => (
      anchor.key === right[index].key
      && anchor.element === right[index].element
    ),
  );
}

function refreshNavigationObserver({ force = false } = {}) {
  if (!els.turnNavigator || els.turnNavigator.hidden) {
    navigationObserver?.disconnect();
    navigationObserver = null;
    navigationObservedAnchors = [];
    return;
  }
  const anchors = visibleNavigationAnchors();
  if (!force && sameNavigationAnchors(anchors, navigationObservedAnchors)) {
    setNavigationActive(navigationObservedKey);
    return;
  }
  navigationObserver?.disconnect();
  navigationObserver = null;
  navigationObservedAnchors = anchors;
  navigationObservedKey = navigationKeyAtReadingLine(anchors);
  setNavigationActive(navigationObservedKey);
  if (!anchors.length) return;

  const readingLine = Math.min(
    Math.max(0, window.innerHeight - 1),
    Math.max(82, Math.round(window.innerHeight * 0.3)),
  );
  const lowerMargin = Math.max(0, window.innerHeight - readingLine - 1);
  const intersecting = new Set();
  const observer = new IntersectionObserver((entries) => {
    if (navigationObserver !== observer) return;
    for (const entry of entries) {
      const key = entry.target.dataset.navigationAnchorKey;
      if (entry.isIntersecting) intersecting.add(key);
      else intersecting.delete(key);
    }
    const active = navigationObservedAnchors.find(
      (anchor) => intersecting.has(anchor.key),
    );
    if (!active) return;
    navigationObservedKey = active.key;
    setNavigationActive(active.key);
  }, {
    root: null,
    rootMargin: `-${readingLine}px 0px -${lowerMargin}px 0px`,
    threshold: 0,
  });
  navigationObserver = observer;
  for (const anchor of anchors) {
    anchor.element.dataset.navigationAnchorKey = anchor.key;
    navigationObserver.observe(anchor.element);
  }
}

function cancelNavigationSettlement() {
  window.clearTimeout(navigationSettleTimer);
  navigationSettleTimer = 0;
  if (navigationScrollEndHandler) {
    window.removeEventListener("scrollend", navigationScrollEndHandler);
    navigationScrollEndHandler = null;
  }
}

function settleNavigationTarget(key) {
  if (pinnedNavigationKey !== key) return;
  cancelNavigationSettlement();
  pinnedNavigationKey = null;
  setNavigationActive(navigationObservedKey);
}

function scrollToNavigationTarget(element, key) {
  cancelNavigationSettlement();
  pinnedNavigationKey = key;
  if ("onscrollend" in window) {
    navigationScrollEndHandler = () => settleNavigationTarget(key);
    window.addEventListener("scrollend", navigationScrollEndHandler, { once: true });
  }
  navigationSettleTimer = window.setTimeout(
    () => settleNavigationTarget(key),
    2000,
  );
  element.scrollIntoView({ block: "start", behavior: "smooth" });
  setNavigationActive(key);
}

async function jumpToHistoryTurn(turnIndex) {
  const jumpSequence = ++navigationJumpSequence;
  let target = els.history.querySelector(`[data-turn-index="${turnIndex}"]`);
  while (!target) {
    const history = state.playerState?.history || [];
    const pagination = state.playerState?.history_pagination || {};
    const oldestTurn = Number(history[0]?.turn_index);
    if (
      !pagination.has_more
      || !Number.isFinite(oldestTurn)
      || oldestTurn <= turnIndex
    ) {
      break;
    }
    const limit = Math.min(
      HISTORY_NAVIGATION_LOAD_SIZE,
      Math.max(1, oldestTurn - turnIndex),
    );
    const loaded = await loadEarlierHistory({ force: true, limit });
    if (jumpSequence !== navigationJumpSequence) return;
    if (!loaded) break;
    target = els.history.querySelector(`[data-turn-index="${turnIndex}"]`);
  }
  if (jumpSequence !== navigationJumpSequence) return;
  if (!target) {
    showToast(`第 ${turnIndex} 回合暂时无法载入`);
    return;
  }
  scrollToNavigationTarget(target, `turn:${turnIndex}`);
}

function jumpToCurrentTurn(key) {
  ++navigationJumpSequence;
  const target = key === "streaming" ? els.streamingCard : els.pendingCard;
  if (!target.hidden) scrollToNavigationTarget(target, key);
}

function jumpToLatestTurn() {
  ++navigationJumpSequence;
  const pending = activePending();
  if (state.mode === "generating" && !els.streamingCard.hidden) {
    jumpToCurrentTurn("streaming");
    return;
  }
  if (pending && !els.pendingCard.hidden) {
    jumpToCurrentTurn("pending");
    return;
  }
  const turns = els.history.querySelectorAll(".turn[data-turn-index]");
  const latest = turns[turns.length - 1];
  if (latest) scrollToNavigationTarget(latest, `turn:${latest.dataset.turnIndex}`);
}

function showActivePlayerAction(text) {
  els.activePlayerAction.textContent = text || "";
  els.activePlayerAction.hidden = !text;
}

async function loadEarlierHistory({
  force = false,
  limit = HISTORY_PAGE_SIZE,
} = {}) {
  if (historyLoadPromise && historyLoadSaveName === state.activeSave) {
    return historyLoadPromise;
  }
  const pagination = state.playerState?.history_pagination || {};
  const history = state.playerState?.history || [];
  if (
    (!force && !state.historyPagingEnabled)
    || !pagination.has_more
    || !history.length
  ) {
    return 0;
  }

  const loadPromise = (async () => {
    state.historyLoading = true;
    const saveName = state.activeSave;
    const previousHeight = document.documentElement.scrollHeight;
    const previousY = window.scrollY;
    try {
      const page = await request(
        `/api/player/history?before_turn=${encodeURIComponent(history[0].turn_index)}&limit=${encodeURIComponent(limit)}`,
      );
      if (saveName !== state.activeSave) return 0;
      const knownHighlights = state.playerState.highlights || [];
      const seenHighlights = new Set(knownHighlights.map((item) => item.surface.toLocaleLowerCase()));
      for (const item of page.highlights || []) {
        const key = item.surface.toLocaleLowerCase();
        if (!seenHighlights.has(key)) {
          knownHighlights.push(item);
          seenHighlights.add(key);
        }
      }
      state.playerState.highlights = knownHighlights;
      const earlier = page.history || [];
      let insertedCount = 0;
      if (earlier.length) {
        const knownTurnIndexes = new Set(
          state.playerState.history.map((turn) => Number(turn.turn_index)),
        );
        const uniqueEarlier = earlier.filter(
          (turn) => !knownTurnIndexes.has(Number(turn.turn_index)),
        );
        insertedCount = uniqueEarlier.length;
        state.playerState.history = [...uniqueEarlier, ...state.playerState.history];
        const fragment = document.createDocumentFragment();
        uniqueEarlier.forEach((turn) => fragment.appendChild(renderTurn(turn)));
        els.history.prepend(fragment);
        const addedHeight = document.documentElement.scrollHeight - previousHeight;
        window.scrollTo({ top: previousY + addedHeight, behavior: "auto" });
      }
      state.playerState.history_pagination = page.pagination || { has_more: false };
      refreshNavigationObserver({ force: true });
      return insertedCount;
    } catch (error) {
      showToast(error.message || "更早的故事暂时无法载入");
      return 0;
    }
  })();
  historyLoadPromise = loadPromise;
  historyLoadSaveName = state.activeSave;
  try {
    return await loadPromise;
  } finally {
    if (historyLoadPromise === loadPromise) {
      historyLoadPromise = null;
      historyLoadSaveName = null;
      state.historyLoading = false;
    }
  }
}

function renderPending() {
  const pending = activePending();
  if (!pending) {
    if (state.mode !== "generating") showActivePlayerAction("");
    els.pendingCard.hidden = true;
    els.pendingCard.replaceChildren();
    return;
  }
  if (state.selectedPendingIndex >= pending.variants.length) {
    state.selectedPendingIndex = pending.variants.length - 1;
  }
  const variant = selectedVariant();
  els.pendingCard.replaceChildren();
  els.pendingCard.hidden = false;

  const defaultPendingText = pending.kind === "opening" ? "故事从这里开始" : "";
  showActivePlayerAction(pending.user_input || defaultPendingText);
  const toolbar = node("div", "pending-toolbar");
  const kicker = node("div", "narrative-kicker");
  kicker.append(node("span", "quill", "✦"), node("span", "", pending.kind === "opening" ? "开场 · 待确认" : "新场景 · 待确认"));
  const versions = node("div", "version-control");
  const previous = node("button", "", "‹");
  const position = node("span", "", `${state.selectedPendingIndex + 1} / ${pending.variants.length}`);
  const next = node("button", "", "›");
  previous.disabled = state.selectedPendingIndex === 0;
  next.disabled = state.selectedPendingIndex === pending.variants.length - 1;
  previous.onclick = () => selectVariant(state.selectedPendingIndex - 1);
  next.onclick = () => selectVariant(state.selectedPendingIndex + 1);
  versions.append(previous, position, next);
  toolbar.append(kicker, versions);

  const display = appendHighlightedText(node("div", "scene-text"), readDraft(variant));
  const closing = variant.closing_prompt ? node("div", "closing-prompt", variant.closing_prompt) : null;
  const actions = node("div", "pending-actions");
  const confirming = state.mode === "confirming";
  const confirm = node("button", "primary-action confirm-button", "确认采用");
  const edit = node("button", "soft-button", "编辑正文");
  const restoreOriginal = node("button", "soft-button", "恢复原文");
  const rephrase = node("button", "soft-button dropdown-item", "重新措辞");
  const feedbackRewrite = node("button", "soft-button dropdown-item", "根据意见修改");
  const reroll = node("button", "soft-button dropdown-item", "重新判定");
  const discard = node("button", "soft-button danger-button compact-discard", "删除");

  confirm.disabled = confirming;
  confirm.textContent = confirming ? "正在确认……" : "确认采用";
  confirm.onclick = confirmTurn;

  edit.onclick = () => beginEdit(display, closing, actions, variant);
  edit.disabled = confirming;

  restoreOriginal.onclick = () => restoreOriginalScene(variant);
  restoreOriginal.disabled = confirming;

  rephrase.disabled = pending.kind === "opening" || ["generating", "confirming"].includes(state.mode);
  rephrase.title = "保留本次判定与导演输入，只重新生成正文";
  rephrase.onclick = () => retryTurn("rephrase");

  feedbackRewrite.disabled = rephrase.disabled;
  feedbackRewrite.title = "根据具体意见决定局部修订、重新构思或不重写";
  feedbackRewrite.onclick = openFeedbackRewrite;

  reroll.disabled = rephrase.disabled;
  reroll.title = "重新运行完整生成管线并重新投骰";
  reroll.onclick = () => retryTurn("reroll");

  discard.disabled = confirming;
  discard.onclick = discardPending;

  actions.appendChild(confirm);
  actions.appendChild(edit);
  if (hasDraft(variant)) actions.appendChild(restoreOriginal);

  if (pending.kind !== "opening") {
    const dropdown = node("div", "rewrite-dropdown");
    const dropdownTrigger = node("button", "soft-button rewrite-dropdown-trigger", "重写选项 ▾");
    dropdownTrigger.disabled = rephrase.disabled;
    const dropdownMenu = node("div", "rewrite-dropdown-menu");
    dropdownMenu.append(rephrase, reroll);
    dropdown.append(dropdownTrigger, dropdownMenu);
    actions.appendChild(dropdown);
    actions.appendChild(discard);
  }
  els.pendingCard.append(toolbar, display);
  if (closing) els.pendingCard.appendChild(closing);
  const resolution = renderPlayerResolution(variant.player_resolution);
  if (resolution) els.pendingCard.appendChild(resolution);
  if (confirming) {
    const progress = node("div", "confirmation-status");
    progress.id = "confirmationProgress";
    const kicker = node("div", "narrative-kicker confirmation-status-kicker");
    kicker.append(node("span", "quill", "✦"), node("span", "confirmation-progress-label", "正在读取最终正文"));
    progress.appendChild(kicker);
    els.pendingCard.appendChild(progress);
  }
  els.pendingCard.appendChild(actions);
}

function beginEdit(display, closing, actions, variant) {
  const editor = node("textarea", "scene-editor");
  editor.value = readDraft(variant);
  const editActions = node("div", "pending-actions");
  const cancel = node("button", "soft-button", "取消");
  const save = node("button", "primary-action", "保存修改");
  display.hidden = true;
  if (closing) closing.hidden = true;
  actions.hidden = true;
  actions.style.display = "none";

  display.after(editor);
  editor.after(editActions);

  cancel.onclick = () => renderPending();
  save.onclick = async () => {
    save.disabled = true;
    cancel.disabled = true;
    try {
      const result = await request("/api/player/pending/edit", {
        method: "POST",
        body: JSON.stringify({
          turn_id: variant.turn_id,
          scene_text: editor.value,
        }),
      });
      applySavedDraft(variant, result);
      renderPending();
      showToast("正文修改已保存");
    } catch (error) {
      save.disabled = false;
      cancel.disabled = false;
      showError(error);
    }
  };
  editActions.append(cancel, save);
  editor.focus();
  window.setTimeout(() => {
    const rect = editor.getBoundingClientRect();
    const targetY = window.scrollY + rect.top - 100;
    window.scrollTo({ top: Math.max(0, targetY), behavior: "smooth" });
  }, 30);
}

function showConfirmModal({
  title = "请确认操作",
  message = "是否确定继续执行此操作？",
  eyebrow = "CONFIRMATION",
  confirmText = "确定",
  cancelText = "取消",
  isDanger = false,
} = {}) {
  return new Promise((resolve) => {
    if (!els.codexConfirmDialog) {
      resolve(window.confirm(message));
      return;
    }
    if (els.codexConfirmEyebrow) els.codexConfirmEyebrow.textContent = eyebrow;
    if (els.codexConfirmTitle) els.codexConfirmTitle.textContent = title;
    if (els.codexConfirmMessage) els.codexConfirmMessage.textContent = message;
    if (els.codexConfirmCancelBtn) els.codexConfirmCancelBtn.textContent = cancelText;

    const okBtn = els.codexConfirmOkBtn;
    if (okBtn) {
      okBtn.textContent = confirmText;
      okBtn.classList.toggle("danger-confirm", isDanger);
      okBtn.classList.toggle("primary-action", !isDanger);
    }

    function cleanup() {
      els.codexConfirmDialog.close();
      els.codexConfirmCancelBtn.removeEventListener("click", onCancel);
      els.codexConfirmOkBtn.removeEventListener("click", onConfirm);
      els.codexConfirmDialog.removeEventListener("cancel", onCancel);
      els.codexConfirmDialog.removeEventListener("click", onBackdrop);
    }

    function onConfirm(e) {
      if (e) e.preventDefault();
      cleanup();
      resolve(true);
    }

    function onCancel(e) {
      if (e) e.preventDefault();
      cleanup();
      resolve(false);
    }

    function onBackdrop(e) {
      if (e.target === els.codexConfirmDialog) {
        onCancel(e);
      }
    }

    els.codexConfirmCancelBtn.addEventListener("click", onCancel);
    els.codexConfirmOkBtn.addEventListener("click", onConfirm);
    els.codexConfirmDialog.addEventListener("cancel", onCancel);
    els.codexConfirmDialog.addEventListener("click", onBackdrop);

    els.codexConfirmDialog.showModal();
  });
}

async function restoreOriginalScene(variant) {
  if (!variant || !hasDraft(variant)) return;
  const confirmed = await showConfirmModal({
    title: "恢复版本确认",
    message: "恢复后，当前版本已保存的正文修改将被删除。是否继续？",
    confirmText: "恢复原始正文",
    isDanger: true,
  });
  if (!confirmed) return;
  try {
    const result = await request("/api/player/pending/edit", {
      method: "POST",
      body: JSON.stringify({
        turn_id: variant.turn_id,
        scene_text: null,
      }),
    });
    applySavedDraft(variant, result);
    renderPending();
    showToast("已恢复该版本的原始正文");
  } catch (error) {
    showError(error);
  }
}

function selectVariant(index) {
  const pending = activePending();
  if (!pending || index < 0 || index >= pending.variants.length) return;
  state.selectedPendingIndex = index;
  renderPending();
}

let activeCodexProfile = null;
let activeCodexTrigger = null;

function closeCodexProfile() {
  if (els.codexDialog && els.codexDialog.open) {
    els.codexDialog.close();
    els.codexDialog.classList.remove("is-floating");
    els.codexDialog.style.top = "";
    els.codexDialog.style.left = "";
    activeCodexProfile = null;
    activeCodexTrigger = null;
    document.removeEventListener("pointerdown", handleCodexOutsideClick);
    document.removeEventListener("keydown", handleCodexKeydown);
    if (document.activeElement && typeof document.activeElement.blur === "function") {
      document.activeElement.blur();
    }
    window.getSelection()?.removeAllRanges();
  }
}

function handleCodexOutsideClick(event) {
  if (els.codexDialog && els.codexDialog.open) {
    if (event.target === els.codexDialog) {
      const rect = els.codexDialog.getBoundingClientRect();
      const isInContent = (
        rect.top <= event.clientY && event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX && event.clientX <= rect.left + rect.width
      );
      if (!isInContent) {
        closeCodexProfile();
        return;
      }
    }
    if (!els.codexDialog.contains(event.target) && !event.target.closest(".story-highlight")) {
      closeCodexProfile();
    }
  }
}

function handleCodexKeydown(event) {
  if (event.key === "Escape") {
    closeCodexProfile();
  }
}

function positionCodexPopover(triggerElement) {
  if (!els.codexDialog || !els.codexDialog.open) return;

  if (window.innerWidth < 640) {
    if (els.codexDialog.classList.contains("is-floating")) {
      els.codexDialog.classList.remove("is-floating");
      els.codexDialog.style.position = "";
      els.codexDialog.style.top = "";
      els.codexDialog.style.left = "";
      els.codexDialog.style.margin = "";
      els.codexDialog.style.width = "";
      els.codexDialog.close();
      els.codexDialog.showModal();
    }
    return;
  }

  if (!els.codexDialog.classList.contains("is-floating")) {
    els.codexDialog.close();
    els.codexDialog.classList.add("is-floating");
    els.codexDialog.show();
  }

  const dialogWidth = Math.min(500, window.innerWidth - 24);
  let left = 16;
  if (triggerElement) {
    const rect = triggerElement.getBoundingClientRect();
    left = rect.left + rect.width / 2 - dialogWidth / 2;
  }
  left = Math.max(12, Math.min(left, window.innerWidth - dialogWidth - 12));

  const popoverHeight = els.codexDialog.offsetHeight || 220;
  let top = 80;
  if (triggerElement) {
    const rect = triggerElement.getBoundingClientRect();
    top = rect.bottom + 10;
    if (top + popoverHeight > window.innerHeight - 16) {
      top = Math.max(16, rect.top - popoverHeight - 10);
      if (top < 16) {
        top = Math.max(16, window.innerHeight - popoverHeight - 16);
      }
    }
  } else {
    top = Math.max(16, window.innerHeight / 2 - popoverHeight / 2);
  }

  els.codexDialog.style.position = "fixed";
  els.codexDialog.style.top = `${top}px`;
  els.codexDialog.style.left = `${left}px`;
  els.codexDialog.style.margin = "0";
  els.codexDialog.style.width = `${dialogWidth}px`;
}

window.addEventListener("resize", () => {
  if (els.codexDialog && els.codexDialog.open && activeCodexTrigger) {
    positionCodexPopover(activeCodexTrigger);
  }
});

function openCodexProfile(profile, triggerElement) {
  if (!profile) return;

  if (els.codexDialog && els.codexDialog.open && (activeCodexProfile === profile || (triggerElement && activeCodexTrigger === triggerElement))) {
    closeCodexProfile();
    return;
  }

  activeCodexProfile = profile;
  activeCodexTrigger = triggerElement || null;

  els.codexDialog.classList.remove("is-expanded");
  els.codexTitle.textContent = profile.name || "典籍条目";
  els.codexContent.replaceChildren();

  const isNPC = profile.kind === "npc" || profile.entity_type === "npc" || profile.type === "npc";
  const isItem = profile.kind === "item" || profile.entity_type === "item" || profile.type === "item";

  const typeLabels = {
    npc: "人物档案",
    item: "物品图鉴",
    location: "地理风物",
    organization: "势力名录",
  };
  const entityType = isNPC ? "npc" : (isItem ? "item" : (profile.entity_type || profile.kind || "location"));
  const typeBadge = node("span", "codex-kind-tag", typeLabels[entityType] || "典籍");

  let subText = "";
  if (isNPC) {
    const roleText = profile.role || "未知身份";
    const factionText = profile.faction || "无归属";
    subText = `${roleText} ｜ ${factionText}`;
  } else if (isItem) {
    const typeText = profile.item_type || "无";
    const sourceText = profile.item_source || "无";
    subText = `${typeText} ｜ ${sourceText}`;
  } else {
    const typeText = profile.location_type || "无";
    const parentText = profile.parent_location ? `上级地点：${profile.parent_location}` : "上级地点：无";
    subText = `${typeText} ｜ ${parentText}`;
  }

  if (subText.length > 28) subText = subText.slice(0, 28) + "…";

  const subHeader = node("div", "codex-sub-header");
  if (subText) subHeader.appendChild(node("span", "codex-sub-text", subText));
  subHeader.appendChild(typeBadge);
  els.codexContent.appendChild(subHeader);

  // 2. 顶部引文区：展示外貌描写 (appearance) 或 summary
  const quoteText = isNPC
    ? (profile.appearance || profile.public_summary || "")
    : (profile.summary || profile.public_summary || profile.description || "");

  if (quoteText) {
    const quoteBox = node("div", "codex-quote-box", `“${quoteText}”`);
    els.codexContent.appendChild(quoteBox);
  }

  // 3. 底部微型勋章区
  const pillsContainer = node("div", "codex-pills");
  if (profile.personality_traits?.length) {
    profile.personality_traits.forEach((t) => pillsContainer.appendChild(node("span", "codex-pill pill-trait", t)));
  }
  if (profile.known_abilities?.length) {
    profile.known_abilities.forEach((a) => pillsContainer.appendChild(node("span", "codex-pill pill-ability", a)));
  }
  if (profile.features?.length) {
    profile.features.forEach((f) => pillsContainer.appendChild(node("span", "codex-pill pill-feature", f)));
  }
  if (profile.aliases?.length) {
    profile.aliases.forEach((al) => pillsContainer.appendChild(node("span", "codex-pill pill-alias", al)));
  }
  if (pillsContainer.childNodes.length) {
    els.codexContent.appendChild(pillsContainer);
  }

  // 4. 渐进式折叠详查区
  const detailsPanel = node("div", "codex-details-panel");
  detailsPanel.hidden = true;

  const appendDetailText = (label, value) => {
    if (!value || value === quoteText) return;
    const row = node("section", "codex-field");
    row.append(node("span", "resource-label", label), node("p", "", value));
    detailsPanel.appendChild(row);
  };

  if (isNPC) {
    if (profile.public_summary && profile.public_summary !== quoteText) {
      appendDetailText("详细信息", profile.public_summary);
    }
    if (profile.current_location) appendDetailText("最后活动地点", profile.current_location);
  } else if (isItem) {
    if (profile.description && profile.description !== quoteText) {
      appendDetailText("详细信息", profile.description);
    }
    if (profile.item_source && profile.item_source !== "无") {
      appendDetailText("物品来源", profile.item_source);
    }
    if (profile.parent_location) {
      appendDetailText("所属地点", profile.parent_location);
    }
    if (profile.public_summary && profile.public_summary !== quoteText && profile.public_summary !== profile.description) {
      appendDetailText("详细信息", profile.public_summary);
    }
  } else {
    if (profile.description && profile.description !== quoteText) {
      appendDetailText("详细信息", profile.description);
    }
    if (profile.public_summary && profile.public_summary !== quoteText && profile.public_summary !== profile.description) {
      appendDetailText("详细信息", profile.public_summary);
    }
  }

  if (detailsPanel.childNodes.length > 0) {
    els.codexContent.appendChild(detailsPanel);
    const toggleBtn = node("button", "codex-toggle-more", "▸ 展开完整记载");
    toggleBtn.onclick = () => {
      const isExpanding = detailsPanel.hidden;
      detailsPanel.hidden = !isExpanding;
      toggleBtn.textContent = isExpanding ? "▾ 收起记载" : "▸ 展开完整记载";
      els.codexDialog.classList.toggle("is-expanded", isExpanding);
      window.requestAnimationFrame(() => positionCodexPopover(triggerElement));
    };
    const footerAction = node("div", "codex-footer-action");
    footerAction.appendChild(toggleBtn);
    els.codexContent.appendChild(footerAction);
  }

  if (triggerElement && window.innerWidth >= 640) {
    els.codexDialog.classList.add("is-floating");
    if (!els.codexDialog.open) els.codexDialog.show();
    positionCodexPopover(triggerElement);
  } else {
    els.codexDialog.classList.remove("is-floating");
    els.codexDialog.style.position = "";
    els.codexDialog.style.top = "";
    els.codexDialog.style.left = "";
    els.codexDialog.style.margin = "";
    els.codexDialog.style.width = "";
    if (!els.codexDialog.open) els.codexDialog.showModal();
  }

  window.setTimeout(() => {
    document.addEventListener("pointerdown", handleCodexOutsideClick);
    document.addEventListener("keydown", handleCodexKeydown);
  }, 0);
}

function renderDrawer() {
  const campaign = state.playerState?.campaign || {};
  const player = campaign.player || {};
  els.playerSummary.replaceChildren();
  els.playerSummary.appendChild(node("strong", "", player.name || "尚未命名"));
  els.playerSummary.appendChild(node("div", "", player.visible_status || "暂无特殊外观"));

  const participants = campaign.participants || [];
  els.sceneSummary.replaceChildren();
  const appendSceneGroup = (label, items, emptyText) => {
    const group = node("section", "scene-summary-group");
    group.appendChild(node("span", "resource-label", label));
    const list = node("div", "scene-summary-items");
    if (items.length) {
      items.forEach((item) => list.appendChild(node("span", "tag", item)));
    } else {
      list.appendChild(node("span", "resource-empty", emptyText));
    }
    group.appendChild(list);
    els.sceneSummary.appendChild(group);
  };
  appendSceneGroup("在场角色", participants, "暂无");

  const resources = campaign.resources || {};
  const money = resources.money == null ? "无" : `${resources.money}${resources.money_unit ? ` ${resources.money_unit}` : ""}`;
  const moneyCard = node("div", "resource-money");
  moneyCard.append(node("span", "resource-label", "可用金钱"), node("strong", "", money));

  const resourceGroup = (label, items, kind) => {
    const group = node("section", `resource-group resource-group-${kind}`);
    const header = node("div", "resource-group-header");
    header.append(
      node("span", "resource-label", label),
      node("span", "resource-count", String(items.length)),
    );
    const list = node("div", "resource-items");
    if (!items.length) {
      list.appendChild(node("div", "resource-empty", "暂无"));
    } else if (kind === "inventory") {
      items.forEach((item) => {
        const row = node("div", "inventory-item");
        const name = node("span", "inventory-name", item.name);
        const quantity = node("span", "inventory-quantity", `×${item.quantity ?? 1}`);
        row.append(name, quantity);
        list.appendChild(row);
      });
    } else {
      items.forEach((item) => list.appendChild(node("div", "asset-item", item)));
    }
    group.append(header, list);
    return group;
  };

  els.resourceSummary.replaceChildren(
    moneyCard,
    resourceGroup("物品", resources.inventory || [], "inventory"),
    resourceGroup("固定资产", resources.fixed_assets || [], "fixed"),
    resourceGroup("临时资产", resources.temporary_assets || [], "temporary"),
  );
  readerPreferences.apply(state.readerPreferences);
  els.difficultySelect.value = state.playerState?.difficulty || "困难";
  els.diceDisplaySelect.value = state.playerState?.dice_display || "outcome";
  enhanceCustomSelect(els.difficultySelect);
  enhanceCustomSelect(els.diceDisplaySelect);
  enhanceCustomSelect(els.fontSizeSelect);
  enhanceCustomSelect(els.fontFamilySelect);
  enhanceCustomSelect(els.themeSelect);
}

function renderSaves() {
  els.saveSelect.replaceChildren();
  state.saves.forEach((save) => {
    const option = node("option", "", `${save.name}${save.initialized ? "" : " · 未初始化"}`);
    option.value = save.save_id;
    option.selected = save.save_id === state.activeSave;
    els.saveSelect.appendChild(option);
  });
  if (!state.saves.length) {
    const option = node("option", "", "暂无存档");
    option.disabled = true;
    option.selected = true;
    els.saveSelect.appendChild(option);
  }
  enhanceCustomSelect(els.saveSelect);
  if (els.saveManagerDialog.open) renderSaveManager();
}

function formatLastPlayed(value) {
  if (!value) return "尚未开始";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function saveManagerMessage(message = "") {
  els.saveManagerError.textContent = message;
  els.saveManagerError.hidden = !message;
}

function saveMeta(save) {
  const protagonist = save.protagonist_name || "主角未设定";
  const location = save.location || "位置未知";
  const turns = `${Number(save.turn_count) || 0} 个已确认回合`;
  return `${protagonist} · ${turns} · ${location}`;
}

function saveActionButton(label, className, action) {
  const button = node("button", className, label);
  button.type = "button";
  button.disabled = saveManagerBusy;
  button.onclick = action;
  return button;
}

async function runSaveManagerAction(action, successMessage) {
  if (saveManagerBusy) return;
  saveManagerBusy = true;
  saveManagerMessage();
  renderSaveManager();
  renderControls();
  try {
    await action();
    await loadAll({ resumeOperation: false });
    showToast(successMessage);
  } catch (error) {
    saveManagerMessage(error?.message || "存档操作没有完成");
  } finally {
    saveManagerBusy = false;
    renderSaveManager();
    renderControls();
  }
}

function showRenamePanel(card, save) {
  card.querySelector(".save-card-confirm")?.remove();
  const panel = node("form", "save-card-confirm");
  const label = node("label", "", "新的存档名称");
  const input = node("input");
  input.maxLength = 80;
  input.value = save.name;
  label.appendChild(input);
  const actions = node("div", "save-card-confirm-actions");
  const cancel = saveActionButton("取消", "soft-button", () => panel.remove());
  const confirm = saveActionButton("保存名称", "primary-action", () => {});
  confirm.type = "submit";
  confirm.onclick = null;
  actions.append(cancel, confirm);
  panel.append(label, actions);
  panel.onsubmit = (event) => {
    event.preventDefault();
    const name = input.value.trim();
    if (!name || name === save.name) return;
    runSaveManagerAction(
      () => request("/api/player/saves/rename", { method: "POST", body: JSON.stringify({ save_id: save.save_id, name }) }),
      "存档已重命名",
    );
  };
  card.appendChild(panel);
  input.focus();
}

function showDeletePanel(card, save) {
  card.querySelector(".save-card-confirm")?.remove();
  const panel = node("form", "save-card-confirm save-card-delete-confirm");
  const warning = node("p", "", `请输入“${save.name}”确认永久删除。`);
  const input = node("input");
  input.placeholder = save.name;
  input.autocomplete = "off";
  panel.append(warning, input);
  const replacements = state.saves.filter((item) => item.save_id !== save.save_id);
  let replacement = null;
  if (save.active && replacements.length) {
    const label = node("label", "", "删除后切换到");
    replacement = node("select");
    replacements.forEach((item) => {
      const option = node("option", "", item.name);
      option.value = item.save_id;
      replacement.appendChild(option);
    });
    label.appendChild(replacement);
    panel.appendChild(label);
  }
  const actions = node("div", "save-card-confirm-actions");
  const cancel = saveActionButton("取消", "soft-button", () => panel.remove());
  const confirm = saveActionButton("永久删除", "danger-action", () => {});
  confirm.type = "submit";
  confirm.onclick = null;
  confirm.disabled = true;
  input.oninput = () => { confirm.disabled = saveManagerBusy || input.value !== save.name; };
  actions.append(cancel, confirm);
  panel.appendChild(actions);
  panel.onsubmit = (event) => {
    event.preventDefault();
    if (input.value !== save.name) return;
    const body = { save_id: save.save_id, confirmation_name: input.value };
    if (replacement?.value) body.replacement_save_id = replacement.value;
    runSaveManagerAction(
      () => request("/api/player/saves/delete", { method: "POST", body: JSON.stringify(body) }),
      "存档已删除",
    );
  };
  card.appendChild(panel);
  input.focus();
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function renderSaveManager() {
  els.saveCardList.replaceChildren();
  els.importSaveBtn.disabled = saveManagerBusy;
  els.importSaveName.disabled = saveManagerBusy;
  for (const button of els.importSaveForm.querySelectorAll("button")) button.disabled = saveManagerBusy;
  state.saves.forEach((save) => {
    const card = node("article", `save-card${save.active ? " active" : ""}`);
    const heading = node("div", "save-card-heading");
    const title = node("h3", "", save.name);
    heading.appendChild(title);
    if (save.active) heading.appendChild(node("span", "save-active-badge", "当前"));
    const metadata = node("p", "save-card-meta", saveMeta(save));
    const played = node("p", "save-card-played", `最后游玩：${formatLastPlayed(save.last_played_at)}`);
    const actions = node("div", "save-card-actions");
    const deleteButton = saveActionButton("删除", "danger-button", () => showDeletePanel(card, save));
    if (state.saves.length <= 1) {
      deleteButton.disabled = true;
      deleteButton.title = "至少需要保留一个存档";
    }
    actions.append(
      saveActionButton("重命名", "soft-button", () => showRenamePanel(card, save)),
      saveActionButton("复制", "soft-button", () => runSaveManagerAction(
        () => request("/api/player/saves/copy", { method: "POST", body: JSON.stringify({ save_id: save.save_id }) }),
        "存档副本已创建",
      )),
      saveActionButton("导出 ZIP", "soft-button", () => runSaveManagerAction(async () => {
        const archive = await downloadSave(save.save_id);
        triggerDownload(archive.blob, archive.filename);
      }, "存档已导出")),
      deleteButton,
    );
    card.append(heading, metadata, played, actions);
    els.saveCardList.appendChild(card);
  });
}

function autoExpandTextarea(textarea) {
  if (!textarea) return;
  textarea.style.height = "auto";
  const scrollHeight = textarea.scrollHeight;
  const computed = window.getComputedStyle(textarea);
  const minHeight = parseFloat(computed.minHeight) || 0;
  const maxHeight = parseFloat(computed.maxHeight) || Infinity;
  const targetHeight = Math.min(Math.max(scrollHeight, minHeight), maxHeight);
  textarea.style.height = `${targetHeight}px`;
  textarea.style.overflowY = scrollHeight > maxHeight ? "auto" : "hidden";
}

function initAutoExpandingTextareas() {
  document.addEventListener("input", (event) => {
    if (event.target && event.target.tagName === "TEXTAREA") {
      autoExpandTextarea(event.target);
    }
  });

  window.addEventListener("resize", () => {
    document.querySelectorAll("textarea").forEach(autoExpandTextarea);
  }, { passive: true });

  document.querySelectorAll("textarea").forEach(autoExpandTextarea);
}

function setTextareaValueWithUndo(textarea, text) {
  textarea.focus();
  textarea.select();
  let inserted = false;
  try {
    inserted = document.execCommand("insertText", false, text);
  } catch (_e) {
    inserted = false;
  }
  if (!inserted) {
    textarea.value = text;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }
  autoExpandTextarea(textarea);
}

function renderSuggestions() {
  els.suggestionList.replaceChildren();
  els.suggestions.hidden = state.suggestions.length === 0 || Boolean(activePending());
  state.suggestions.forEach((preview) => {
    const button = node("button", "suggestion");
    const header = node("div", "suggestion-header");
    header.append(node("span", "suggestion-level", preview.level || "建议"));
    const results = node("div", "suggestion-results");
    if (preview.good_result) {
      results.appendChild(node("small", "suggestion-result suggestion-good", `✦ ${preview.good_result}`));
    }
    if (preview.risk) {
      results.appendChild(node("small", "suggestion-result suggestion-risk", `⚠ ${preview.risk}`));
    }
    button.append(
      header,
      node("strong", "", preview.label),
      results,
    );
    button.onclick = () => { setTextareaValueWithUndo(els.playerInput, preview.label); autoSizeInput(); renderControls(); };
    els.suggestionList.appendChild(button);
  });
}

function renderAll() {
  renderHeader(); renderHistory(); renderPending(); renderDrawer(); renderSaves(); renderSuggestions(); renderControls();
}

function cachePlayerShell() {
  if (!state.playerState) return;
  try {
    sessionStorage.setItem(PLAYER_SHELL_CACHE_KEY, JSON.stringify({
      version: 1,
      cachedAt: Date.now(),
      activeSave: state.activeSave,
      saves: state.saves,
      playerState: state.playerState,
    }));
  } catch (_error) {
    // The cache is only a refresh-time display fallback.
  }
}

function activeOperation(operation) {
  return operation && !["succeeded", "failed", "cancelled"].includes(operation.status);
}

function nextGeneratedTurnIndex() {
  const entries = state.playerState?.history_navigation || [];
  const maxTurn = entries.reduce(
    (current, entry) => Math.max(current, Number(entry.turn_index ?? -1)),
    -1,
  );
  return maxTurn >= 0 ? maxTurn + 1 : null;
}

function generationRunSnapshot(run = activeGenerationRun) {
  if (!run) return null;
  return {
    operationId: run.operationId || null,
    operationType: run.operationType || "generation",
    userInput: run.userInput || "",
    turnIndex: run.turnIndex ?? nextGeneratedTurnIndex(),
    cancellationRequested: Boolean(run.cancellationRequested),
  };
}

function persistGenerationRun() {
  const snapshot = generationRunSnapshot();
  if (!snapshot) return;
  try {
    sessionStorage.setItem(GENERATION_IN_FLIGHT_KEY, JSON.stringify(snapshot));
  } catch (_error) {
    // The marker is only a refresh-time display fallback.
  }
}

function clearGenerationRunMarker() {
  sessionStorage.removeItem(GENERATION_IN_FLIGHT_KEY);
}

function readGenerationRunMarker() {
  try {
    const marker = JSON.parse(sessionStorage.getItem(GENERATION_IN_FLIGHT_KEY) || "null");
    if (!marker || typeof marker !== "object") return null;
    return marker;
  } catch (_error) {
    clearGenerationRunMarker();
    return null;
  }
}

function generationRunFromOperation(operation) {
  if (!operation || operation.recovery_kind !== "generation") return null;
  return {
    operationId: operation.operation_id || null,
    operationType: operation.operation_type || "generation",
    userInput: operation.metadata?.user_input || "",
    turnIndex: nextGeneratedTurnIndex(),
    cancellationRequested: operation.current_step === "cancellation_requested",
  };
}

function restoreGenerationRunPlaceholder(run) {
  if (!run) return false;
  prepareGenerationRun(
    run.operationType || "generation",
    run.userInput || "",
    run.operationId || null,
    {
      turnIndex: run.turnIndex ?? nextGeneratedTurnIndex(),
      cancellationRequested: Boolean(run.cancellationRequested),
    },
  );
  setMode("generating");
  showActivePlayerAction(activeGenerationRun.userInput);
  startStreaming(
    activeGenerationRun.cancellationRequested
      ? "正在停止生成"
      : "正在恢复生成进度",
  );
  return true;
}

function restorePlayerShell() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(PLAYER_SHELL_CACHE_KEY) || "null");
    if (cached?.version !== 1 || !cached.playerState) return false;
    state.activeSave = cached.activeSave || null;
    state.saves = cached.saves || [];
    state.playerState = cached.playerState;
    state.suggestions = cached.playerState.action_preview?.previews || [];
    state.selectedPendingIndex = Math.max(0, (activePending()?.variants?.length || 1) - 1);
    const confirmationInFlight = sessionStorage.getItem(CONFIRMATION_IN_FLIGHT_KEY) === "1";
    const generationInFlight = readGenerationRunMarker();
    if (generationInFlight) {
      restoreGenerationRunPlaceholder(generationInFlight);
      renderAll();
    } else {
      setMode(
        confirmationInFlight
          ? "confirming"
          : cached.playerState.needs_init
            ? "needs_init"
            : activePending()
              ? "pending"
              : "idle",
      );
      renderAll();
    }
    scrollToBottom("instant");
    return true;
  } catch (_error) {
    sessionStorage.removeItem(PLAYER_SHELL_CACHE_KEY);
    return false;
  }
}

function showModelTestResult(status, message) {
  els.modelTestResult.dataset.status = status;
  els.modelTestResult.textContent = message;
  els.modelTestResult.hidden = false;
}

function invalidateModelConnectionTest() {
  modelConnectionVerified = false;
  els.modelTestResult.hidden = true;
}

function modelConfigPayload() {
  return {
    api_key: els.modelApiKey.value.trim(),
    base_url: els.modelBaseUrl.value.trim(),
    model: els.modelName.value.trim(),
    thinking: els.modelThinking.checked,
  };
}

function renderModelConfigForm(config, { required = false } = {}) {
  state.modelConfig = config;
  els.modelConfigDialog.dataset.required = required ? "true" : "false";
  els.closeModelConfigBtn.hidden = required;
  els.modelConfigIntro.textContent = required
    ? "开始故事前，请先配置并测试本机使用的模型服务。配置不会写入或导出到故事存档。"
    : "模型配置只保存在本机，不会写入或导出到任何故事存档。";
  els.modelApiKey.value = "";
  els.modelApiKey.placeholder = config.configured
    ? "••••••••••••（已配置）"
    : "填写 API Key";
  els.modelKeyHint.textContent = config.configured
    ? "已保存密钥。留空会继续使用现有密钥；密钥不会传回浏览器。"
    : "保存后不会再次显示或传回浏览器。";
  els.modelBaseUrl.value = config.base_url || "https://api.openai.com/v1";
  els.modelName.value = config.model || "gpt-4o";
  els.modelThinking.checked = Boolean(config.thinking);
  invalidateModelConnectionTest();
}

function openModelConfig(config = state.modelConfig, { required = false } = {}) {
  renderModelConfigForm(config || {
    configured: false,
    base_url: "https://api.openai.com/v1",
    model: "gpt-4o",
    thinking: false,
  }, { required });
  if (!els.modelConfigDialog.open) els.modelConfigDialog.showModal();
  window.setTimeout(() => (
    config?.configured ? els.modelBaseUrl : els.modelApiKey
  ).focus(), 0);
}

function enterModelSetup(config) {
  state.modelConfig = config;
  setMode("needs_model");
  if (els.initDialog.open) els.initDialog.close();
  renderControls();
  openModelConfig(config, { required: true });
}

function setModelConfigBusy(busy) {
  modelConfigBusy = busy;
  for (const input of els.modelConfigForm.querySelectorAll("input, button")) {
    input.disabled = busy;
  }
  if (els.modelConfigDialog.dataset.required === "true") {
    els.closeModelConfigBtn.hidden = true;
  }
  renderControls();
}

async function testModelConnection() {
  if (!els.modelConfigForm.reportValidity()) return;
  setModelConfigBusy(true);
  showModelTestResult("pending", "正在连接模型服务……");
  try {
    const result = await request("/api/player/model-config/test", {
      method: "POST",
      body: JSON.stringify(modelConfigPayload()),
    });
    modelConnectionVerified = Boolean(result.success);
    if (result.success) {
      showModelTestResult(
        "success",
        `连接成功 · ${result.model} · ${Number(result.latency_ms).toFixed(0)} ms`,
      );
    } else {
      showModelTestResult(
        "error",
        `${result.error?.message || "连接失败"}（${result.error?.code || "unknown"}，${Number(result.latency_ms).toFixed(0)} ms）`,
      );
    }
  } catch (error) {
    modelConnectionVerified = false;
    showModelTestResult("error", error.message || "连接检查没有完成");
  } finally {
    setModelConfigBusy(false);
  }
}

async function saveModelSettings() {
  if (!els.modelConfigForm.reportValidity()) return;
  if (!modelConnectionVerified) {
    showModelTestResult("error", "请先使用当前填写内容完成一次连接测试。修改任一字段后需要重新测试。");
    return;
  }
  setModelConfigBusy(true);
  try {
    const config = await request("/api/player/model-config", {
      method: "POST",
      body: JSON.stringify(modelConfigPayload()),
    });
    state.modelConfig = config;
    els.modelApiKey.value = "";
    els.modelConfigDialog.close();
    showToast("模型配置已保存并立即启用");
    await loadAll({ scroll: "instant", knownModelConfig: config });
  } catch (error) {
    showModelTestResult("error", error.message || "模型配置未能保存");
  } finally {
    setModelConfigBusy(false);
  }
}

async function loadAll({ scroll = false, resumeOperation = true, knownModelConfig = null } = {}) {
  state.historyPagingEnabled = false;
  const modelConfig = knownModelConfig || await request("/api/player/model-config");
  state.modelConfig = modelConfig;
  if (!modelConfig.configured) {
    enterModelSetup(modelConfig);
    return;
  }
  const saves = await request("/api/player/saves");
  state.saves = saves.saves || [];
  state.activeSave = saves.active;
  ++navigationJumpSequence;
  cancelNavigationSettlement();
  pinnedNavigationKey = null;
  navigationRenderSignature = "";
  if (
    resumeOperation
    && !activeGenerationRun
    && !["booting", "generating", "confirming", "switching"].includes(state.mode)
  ) {
    setMode("booting");
  }
  renderSaves();
  renderControls();

  const recoverableOperations = resumeOperation
    ? (await request("/api/player/operations/recoverable")).operations || []
    : [];
  const firstOperation = recoverableOperations[0] || null;
  const activeGenerationOperation = recoverableOperations.find(
    (operation) => operation.recovery_kind === "generation" && activeOperation(operation),
  );
  const preserveCachedState = recoverableOperations.some(
    (operation) => ["confirmation", "initialization"].includes(
      operation.recovery_kind,
    ),
  ) || Boolean(activeGenerationOperation && state.playerState);
  if (activeGenerationOperation) {
    restoreGenerationRunPlaceholder(generationRunFromOperation(activeGenerationOperation));
  } else {
    if (activeGenerationRun) {
      finishGenerationRun();
      stopStreaming();
    }
    clearGenerationRunMarker();
  }
  const playerState = preserveCachedState && state.playerState
    ? state.playerState
    : await request("/api/player/state");
  if (firstOperation?.recovery_kind !== "confirmation") {
    sessionStorage.removeItem(CONFIRMATION_IN_FLIGHT_KEY);
  }
  state.playerState = playerState;
  state.suggestions = playerState.action_preview?.previews || [];
  state.selectedPendingIndex = Math.max(0, (activePending()?.variants?.length || 1) - 1);
  if (activeGenerationRun && activeGenerationRun.turnIndex === null) {
    activeGenerationRun.turnIndex = nextGeneratedTurnIndex();
    persistGenerationRun();
  }
  setMode(
    activeGenerationOperation
      ? "generating"
      : firstOperation?.recovery_kind === "confirmation"
        ? "confirming"
        : firstOperation?.recovery_kind === "initialization"
          ? "generating"
      : playerState.needs_init
        ? "needs_init"
        : activePending()
          ? "pending"
          : "idle",
  );
  renderAll();
  cachePlayerShell();
  if (recoverableOperations.length) {
    let reloadAfterRecovery = false;
    for (const operation of recoverableOperations) {
      if (operation.recovery_kind === "initialization") {
        reloadAfterRecovery = (
          await resumeInitializationOperation(operation)
        ) || reloadAfterRecovery;
      } else if (operation.recovery_kind === "confirmation") {
        reloadAfterRecovery = (
          await resumeConfirmationOperation(operation)
        ) || reloadAfterRecovery;
      } else if (operation.recovery_kind === "generation") {
        reloadAfterRecovery = (
          await resumeGenerationOperation(operation)
        ) || reloadAfterRecovery;
      }
    }
    if (reloadAfterRecovery) {
      await loadAll({ scroll: scroll || "smooth", resumeOperation: false });
    }
    return;
  }
  if (playerState.needs_init && !els.initDialog.open) els.initDialog.showModal();
  if (scroll) scrollToBottom(scroll, () => { state.historyPagingEnabled = true; });
  else window.requestAnimationFrame(() => { state.historyPagingEnabled = true; });
}

async function bootstrap() {
  initAutoExpandingTextareas();
  await readerPreferences.load();
  const modelConfig = await request("/api/player/model-config");
  state.modelConfig = modelConfig;
  if (!modelConfig.configured) {
    enterModelSetup(modelConfig);
    return;
  }
  restorePlayerShell();
  await loadAll({ scroll: "instant", knownModelConfig: modelConfig });
}

function handleInitializationEvent(event) {
  setProgress(event.step);
  if (event.step === "story_direction_done") {
    showStoryDirectionStage(event.data || {});
  }
}

async function resumeInitializationOperation(operation) {
  const storyDirection = operation.metadata?.stage === "story_direction";
  setMode("generating");
  renderControls();
  if (!els.initDialog.open) els.initDialog.showModal();
  const button = storyDirection ? els.storyDirectionBtn : els.initSubmitBtn;
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = storyDirection ? "正在生成……" : "正在生成开场……";
  try {
    await subscribe(
      `/api/player/operations/${encodeURIComponent(operation.operation_id)}/events?after=0`,
      handleInitializationEvent,
    );
    await acknowledgeHandledOperation(operation.operation_id);
    if (storyDirection) {
      setMode("needs_init");
      renderControls();
      return false;
    } else {
      els.initDialog.close();
      return true;
    }
  } catch (error) {
    setMode("needs_init");
    els.initError.textContent = error.message;
    els.initError.hidden = false;
    renderControls();
    await acknowledgeFailedOperation(error, operation.operation_id);
    return false;
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

function setProgress(step) {
  const label = progressLabels[step];
  if (label) els.streamingLabel.textContent = label;
  const confirmationLabel = document.querySelector("#confirmationProgress .confirmation-progress-label");
  if (label && confirmationLabel) confirmationLabel.textContent = label;
}

function prepareGenerationRun(operationType, userInput, operationId = null, options = {}) {
  activeGenerationRun = {
    operationId,
    operationType,
    userInput: userInput || "",
    turnIndex: options.turnIndex ?? nextGeneratedTurnIndex(),
    cancellationRequested: Boolean(options.cancellationRequested),
    cancelled: false,
  };
  persistGenerationRun();
}

function finishGenerationRun() {
  activeGenerationRun = null;
  clearGenerationRunMarker();
  els.stopGenerationBtn.hidden = true;
  els.stopGenerationBtn.disabled = true;
  els.stopGenerationBtn.textContent = "停止生成";
}

function updateGenerationRunFromEvent(event) {
  if (!activeGenerationRun) return;
  if (event.step === "operation_started") {
    activeGenerationRun.operationId = event.data?.operation_id || activeGenerationRun.operationId;
    activeGenerationRun.operationType = event.data?.operation_type || activeGenerationRun.operationType;
    els.stopGenerationBtn.disabled = !activeGenerationRun.operationId;
    persistGenerationRun();
  } else if (event.step === "cancellation_requested") {
    activeGenerationRun.cancellationRequested = true;
    els.streamingLabel.textContent = "正在停止生成";
    persistGenerationRun();
  } else if (event.step === "operation_cancelled") {
    activeGenerationRun.cancelled = true;
    persistGenerationRun();
  }
}

async function stopActiveGeneration() {
  const run = activeGenerationRun;
  if (!run?.operationId || run.cancellationRequested) return;
  run.cancellationRequested = true;
  els.stopGenerationBtn.disabled = true;
  els.stopGenerationBtn.textContent = "正在停止……";
  els.streamingLabel.textContent = "正在停止生成";
  els.streamingText.replaceChildren();
  try {
    const result = await cancelOperation(run.operationId);
    if (result.status === "already_completed") {
      run.cancellationRequested = false;
      els.stopGenerationBtn.textContent = "停止生成";
      els.streamingLabel.textContent = "生成已经完成，正在载入";
    }
  } catch (error) {
    run.cancellationRequested = false;
    els.stopGenerationBtn.disabled = false;
    els.stopGenerationBtn.textContent = "停止生成";
    showError(error);
  }
}

function settleCancelledGeneration({ retryMode = null, userInput = "" } = {}) {
  stopStreaming();
  if (retryMode) {
    setMode("pending");
    renderAll();
  } else {
    showActivePlayerAction("");
    els.playerInput.value = userInput;
    autoSizeInput();
    setMode("idle");
    renderControls();
    els.playerInput.focus();
  }
  showToast("已停止生成");
}

function startStreaming(label = "世界正在回应") {
  streamBuffer = "";
  followStream = true;
  if (streamFrame) window.cancelAnimationFrame(streamFrame);
  streamFrame = 0;
  els.streamingText.replaceChildren();
  els.streamingLabel.textContent = label;
  els.streamingCard.hidden = false;
  els.stopGenerationBtn.hidden = false;
  els.stopGenerationBtn.disabled = !activeGenerationRun?.operationId
    || activeGenerationRun.cancellationRequested;
  els.stopGenerationBtn.textContent = activeGenerationRun?.cancellationRequested
    ? "正在停止……"
    : "停止生成";
  els.pendingCard.hidden = true;
  renderTurnNavigator();
  scrollToBottom("smooth");
}

function appendStreamingText(delta) {
  if (!delta) return;
  streamBuffer += delta;
  followStream = followStream || isNearBottom();
  if (streamFrame) return;
  streamFrame = window.requestAnimationFrame(() => {
    if (streamBuffer) els.streamingText.appendChild(document.createTextNode(streamBuffer));
    streamBuffer = "";
    streamFrame = 0;
    if (followStream) scrollToBottom("instant");
    followStream = false;
  });
}

function flushStreamingText() {
  if (streamFrame) window.cancelAnimationFrame(streamFrame);
  streamFrame = 0;
  if (streamBuffer) els.streamingText.appendChild(document.createTextNode(streamBuffer));
  streamBuffer = "";
}

function stopStreaming() {
  flushStreamingText();
  els.streamingCard.hidden = true;
  renderTurnNavigator();
}

function newRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `request-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function acknowledgeHandledOperation(operationId) {
  if (!operationId) return;
  try {
    await acknowledgeOperation(operationId);
  } catch (_ackError) {
    // Keep the terminal Operation recoverable for the next refresh.
  }
}

async function acknowledgeFailedOperation(error, operationId = null) {
  await acknowledgeHandledOperation(operationId || error?.operationId);
}

function handleGenerationEvent(event, { retryMode = null } = {}) {
  updateGenerationRunFromEvent(event);
  if (event.step === "operation_events_truncated") {
    els.streamingLabel.textContent = "正在恢复最新生成进度";
  }
  setProgress(event.step);
  if (event.step === "feedback_rewrite_strategy_selected") {
    const strategy = event.data?.strategy;
    if (strategy === "revise_from_source") els.streamingLabel.textContent = "正在基于原文修订";
    if (strategy === "recompose") els.streamingLabel.textContent = "正在重新构思正文";
  }
  if (event.step === "scene_stream_started") startStreaming(
    event.data?.rewrite_mode === "recompose"
      ? "正在重新构思正文"
      : retryMode === "feedback_rewrite"
        ? "正在基于原文修订"
      : event.data?.revision
      ? "正在根据问题断言重新编写"
      : retryMode === "rephrase"
        ? "正在重新措辞"
        : retryMode === "reroll"
          ? "正在重新判定"
        : "世界正在回应",
  );
  if (event.step === "scene_text_delta") {
    appendStreamingText(event.data?.delta || "");
  }
}

async function resumeGenerationOperation(operation) {
  const retryMode = ["rephrase", "reroll", "feedback_rewrite"].includes(operation.operation_type)
    ? operation.operation_type
    : null;
  const userInput = operation.metadata?.user_input || "";
  prepareGenerationRun(operation.operation_type, userInput, operation.operation_id);
  setMode("generating");
  renderControls();
  showActivePlayerAction(userInput);
  startStreaming(
    retryMode === "rephrase"
      ? "正在恢复重新措辞"
      : retryMode === "reroll"
        ? "正在恢复重新判定"
        : retryMode === "feedback_rewrite"
          ? "正在恢复意见重写"
        : "正在恢复生成进度",
  );
  let feedbackDecision = null;
  try {
    await subscribe(
      `/api/player/operations/${encodeURIComponent(operation.operation_id)}/events?after=0`,
      (event) => {
        if (event.step === "feedback_rewrite_strategy_selected") feedbackDecision = event.data;
        handleGenerationEvent(event, { retryMode });
      },
    );
    await acknowledgeHandledOperation(operation.operation_id);
    if (activeGenerationRun?.cancelled) {
      settleCancelledGeneration({ retryMode, userInput });
      return false;
    }
    stopStreaming();
    if (retryMode === "feedback_rewrite" && feedbackDecision?.strategy === "no_rewrite") {
      showToast(feedbackNoRewriteMessage(feedbackDecision.reason_code));
    } else if (retryMode) {
      showToast("新版本已生成，旧版本仍然保留");
    }
    return true;
  } catch (error) {
    stopStreaming();
    if (retryMode) {
      setMode("pending");
      renderAll();
    } else {
      showActivePlayerAction("");
      if (userInput) {
        els.playerInput.value = userInput;
        autoSizeInput();
      }
    }
    showError(error);
    await acknowledgeFailedOperation(error, operation.operation_id);
    return false;
  } finally {
    finishGenerationRun();
  }
}

function handleConfirmationEvent(event) {
  setProgress(event.step);
  if (event.step === "action_preview_done" || event.step === "finish") {
    const previews = event.data?.action_preview?.previews;
    if (previews) state.suggestions = previews;
  }
}

async function resumeConfirmationOperation(operation) {
  const pending = activePending();
  sessionStorage.setItem(CONFIRMATION_IN_FLIGHT_KEY, "1");
  setMode("confirming");
  renderPending();
  renderControls();
  try {
    await subscribe(
      `/api/player/operations/${encodeURIComponent(operation.operation_id)}/events?after=0`,
      handleConfirmationEvent,
    );
    await acknowledgeHandledOperation(operation.operation_id);
    sessionStorage.removeItem(CONFIRMATION_IN_FLIGHT_KEY);
    showToast("这一回合已经写入旅程");
    return true;
  } catch (error) {
    sessionStorage.removeItem(CONFIRMATION_IN_FLIGHT_KEY);
    setMode("pending");
    renderAll();
    showError(error);
    await acknowledgeFailedOperation(error, operation.operation_id);
    return false;
  }
}

async function rewriteFromFeedbackText(userFeedback) {
  const pending = activePending(); const variant = selectedVariant();
  if (!pending || !variant || pending.kind === "opening" || !userFeedback) return;
  if (els.feedbackRewriteDialog?.open) els.feedbackRewriteDialog.close();
  prepareGenerationRun("feedback_rewrite", pending.user_input || "");
  setMode("generating"); renderControls();
  startStreaming("正在理解修改意见");
  let decision = null;
  try {
    const operationId = await stream("/api/player/feedback-rewrite", {
      turn_id: variant.turn_id,
      source_scene_text: readDraft(variant),
      user_feedback: userFeedback,
      request_id: newRequestId(),
    }, (event) => {
      if (event.step === "feedback_rewrite_strategy_selected") decision = event.data;
      handleGenerationEvent(event, { retryMode: "feedback_rewrite" });
    });
    await acknowledgeHandledOperation(operationId);
    if (activeGenerationRun?.cancelled) {
      settleCancelledGeneration({ retryMode: "feedback_rewrite" });
      return;
    }
    stopStreaming();
    await loadAll({ scroll: decision?.strategy === "no_rewrite" ? "instant" : "smooth" });
    els.feedbackRewriteInput.value = "";
    if (decision?.strategy === "no_rewrite") {
      showToast(feedbackNoRewriteMessage(decision.reason_code));
    } else {
      showToast("新版本已生成，旧版本仍然保留");
    }
  } catch (error) {
    stopStreaming(); setMode("pending"); renderAll(); showError(error);
    await acknowledgeFailedOperation(error);
  } finally {
    finishGenerationRun();
  }
}

async function rewriteFromFeedback() {
  const userFeedback = els.feedbackRewriteInput.value.trim();
  await rewriteFromFeedbackText(userFeedback);
}

async function generateTurn() {
  const userInput = els.playerInput.value.trim();
  const pending = activePending();
  if (pending && userInput) {
    els.playerInput.value = "";
    autoSizeInput();
    await rewriteFromFeedbackText(userInput);
    return;
  }
  if (!userInput || state.mode !== "idle") return;
  prepareGenerationRun("generation", userInput);
  clearError(); setMode("generating"); state.suggestions = []; renderSuggestions(); renderControls();
  showActivePlayerAction(userInput);
  els.playerInput.value = ""; autoSizeInput(); renderControls();
  startStreaming("正在理解你的行动");
  try {
    const operationId = await stream("/api/player/generate", {
      user_input: userInput,
      difficulty: els.difficultySelect.value,
      request_id: newRequestId(),
    }, (event) => handleGenerationEvent(event));
    await acknowledgeHandledOperation(operationId);
    if (activeGenerationRun?.cancelled) {
      settleCancelledGeneration({ userInput });
      return;
    }
    stopStreaming();
    await loadAll({ scroll: "smooth" });
  } catch (error) {
    stopStreaming(); showActivePlayerAction("");
    els.playerInput.value = userInput; autoSizeInput();
    showError(error);
    await acknowledgeFailedOperation(error);
  } finally {
    finishGenerationRun();
  }
}

async function retryTurn(retryMode) {
  const pending = activePending(); const variant = selectedVariant();
  if (!pending || !variant || pending.kind === "opening") return;
  const rephrase = retryMode === "rephrase";
  const endpoint = rephrase ? "rephrase" : "reroll";
  prepareGenerationRun(retryMode, pending.user_input || "");
  setMode("generating"); renderControls();
  startStreaming(rephrase ? "正在重新措辞" : "正在重新判定");
  try {
    const operationId = await stream(`/api/player/${endpoint}`, {
      turn_id: variant.turn_id,
      request_id: newRequestId(),
    }, (event) => handleGenerationEvent(event, { retryMode }));
    await acknowledgeHandledOperation(operationId);
    if (activeGenerationRun?.cancelled) {
      settleCancelledGeneration({ retryMode });
      return;
    }
    stopStreaming(); await loadAll({ scroll: "smooth" }); showToast("新版本已生成，旧版本仍然保留");
  } catch (error) {
    stopStreaming(); setMode("pending"); renderAll(); showError(error);
    await acknowledgeFailedOperation(error);
  } finally {
    finishGenerationRun();
  }
}

function openFeedbackRewrite() {
  const pending = activePending(); const variant = selectedVariant();
  if (!pending || !variant || pending.kind === "opening") return;
  els.feedbackRewriteDialog.showModal();
  window.setTimeout(() => els.feedbackRewriteInput.focus(), 0);
}

function feedbackNoRewriteMessage(reasonCode) {
  if (["changes_player_action", "changes_resolved_outcome"].includes(reasonCode)) {
    return "这项意见会改变玩家行动或判定结果，请使用重新判定";
  }
  if (reasonCode === "conflicts_with_authority") {
    return "这项意见与当前已经确定的事实冲突，未执行重写";
  }
  if (reasonCode === "no_actionable_request") {
    return "没有识别到明确可执行的修改要求";
  }
  return "这项意见不适用于正文重写，原版本保持不变";
}

async function confirmTurn() {
  const pending = activePending(); const variant = selectedVariant();
  if (!pending || !variant) return;
  sessionStorage.setItem(CONFIRMATION_IN_FLIGHT_KEY, "1");
  setMode("confirming"); renderPending(); renderControls();
  try {
    const operationId = await stream("/api/player/confirm", {
      turn_id: variant.turn_id,
      scene_text: readDraft(variant),
      request_id: newRequestId(),
    }, handleConfirmationEvent);
    await acknowledgeHandledOperation(operationId);
    await loadAll({ scroll: "smooth" }); showToast("这一回合已经写入旅程");
  } catch (error) {
    setMode("pending"); renderAll(); showError(error);
    await acknowledgeFailedOperation(error);
  }
  finally {
    sessionStorage.removeItem(CONFIRMATION_IN_FLIGHT_KEY);
  }
}

async function discardPending() {
  const pending = activePending(); const variant = selectedVariant();
  if (!pending || !variant) return;
  const confirmed = await showConfirmModal({
    title: "删除待确认段落",
    message: "删除全部未确认版本，并回到输入行动之前？",
    confirmText: "确定删除",
    isDanger: true,
  });
  if (!confirmed) return;
  setMode("switching"); renderControls();
  try {
    const result = await request("/api/player/pending/discard", { method: "POST", body: JSON.stringify({ turn_id: variant.turn_id }) });
    await loadAll(); els.playerInput.value = result.user_input || ""; autoSizeInput(); renderControls(); els.playerInput.focus(); showToast("未确认内容已删除");
  } catch (error) { showError(error); }
}

async function changeHistory(action) {
  const pendingIds = (activePending()?.variants || []).map((variant) => variant.turn_id);
  const redoPendingIds = state.playerState?.history_controls?.redo_pending_turn_ids || [];
  if (activePending() && !(action === "redo" && pendingIds.some((id) => redoPendingIds.includes(id)))) return;
  setMode("switching"); renderControls();
  try { await request(`/api/player/history/${action}`, { method: "POST", body: "{}" }); await loadAll({ scroll: "smooth" }); showToast(action === "undo" ? "已撤销上一回合" : "已重做回合"); }
  catch (error) { showError(error); }
}

async function confirmDiscardUnsavedPendingEdit() {
  if (!els.pendingCard.querySelector(".scene-editor")) return true;
  return showConfirmModal({
    title: "放弃当前编辑",
    message: "当前正文仍在编辑，切换存档会丢失尚未保存的修改。是否继续？",
    confirmText: "放弃修改并切换",
    isDanger: true,
  });
}

async function switchSave(saveId) {
  if (!saveId || saveId === state.activeSave) return;
  const canContinue = await confirmDiscardUnsavedPendingEdit();
  if (!canContinue) {
    els.saveSelect.value = state.activeSave;
    syncCustomSelect(els.saveSelect);
    return;
  }
  setMode("switching"); renderControls();
  try {
    await request("/api/player/saves/switch", {
      method: "POST",
      body: JSON.stringify({ save_id: saveId }),
    });
    state.suggestions = []; await loadAll({ scroll: "instant" }); closeDrawer();
  } catch (error) { els.saveSelect.value = state.activeSave; syncCustomSelect(els.saveSelect); showError(error); }
}

async function createSave(name) {
  const canContinue = await confirmDiscardUnsavedPendingEdit();
  if (!canContinue) return;
  setMode("switching"); renderControls();
  await request("/api/player/saves/create", {
    method: "POST",
    body: JSON.stringify({ name, activate: true }),
  });
  state.suggestions = []; await loadAll({ scroll: "instant" });
}

function formValue(form, name) {
  return String(new FormData(form).get(name) || "").trim();
}

function readInitConfig(form) {
  const fanfic = els.initFanfic.checked;
  const abilityNames = [...form.querySelectorAll(".ability-name")];
  const abilityDescriptions = [...form.querySelectorAll(".ability-description")];
  const protagonistAbilities = abilityNames.map((input, index) => {
    const name = input.value.trim();
    const description = abilityDescriptions[index].value.trim();
    if (Boolean(name) !== Boolean(description)) {
      throw new Error(`能力 ${index + 1} 的名称和解释需要同时填写`);
    }
    return name ? { name, description } : null;
  }).filter(Boolean);
  const extraCoreMemoryItems = [...els.extraCoreMemoryList.querySelectorAll(".extra-memory-row")].map((row, index) => {
    const id = row.querySelector(".extra-memory-id").value.trim();
    const category = row.querySelector(".extra-memory-category").value.trim();
    const content = row.querySelector(".extra-memory-content").value.trim();
    const priorityRaw = row.querySelector(".extra-memory-priority").value.trim();
    if (!id || !category || !content) throw new Error(`额外核心设定 ${index + 1} 尚未填写完整`);
    return { id, category, content, priority: priorityRaw ? Number(priorityRaw) : 100 };
  });
  const age = formValue(form, "protagonist_age");
  const abilitySystem = fanfic ? null : formValue(form, "ability_system") || null;
  return {
    version: 1,
    fanfic,
    work_name: fanfic ? formValue(form, "work_name") || null : null,
    birth_location: fanfic ? formValue(form, "birth_location") || null : null,
    story_era: fanfic ? formValue(form, "story_era") || null : null,
    world_name: fanfic ? null : formValue(form, "world_name") || null,
    world_style: fanfic ? null : formValue(form, "world_style") || null,
    ability_system: abilitySystem,
    custom_ability_system: abilitySystem === "自定义" ? formValue(form, "custom_ability_system") || null : null,
    protagonist_gender: formValue(form, "protagonist_gender") || "男",
    protagonist_name: formValue(form, "protagonist_name"),
    protagonist_age: age ? Number(age) : null,
    protagonist_background: formValue(form, "protagonist_background") || null,
    protagonist_abilities: protagonistAbilities,
    narrative_tone: formValue(form, "narrative_tone") || null,
    story_expectation: formValue(form, "story_expectation") || null,
    extra_core_memory_items: extraCoreMemoryItems,
  };
}

function addExtraCoreMemoryRow(values = {}) {
  const row = node("div", "extra-memory-row");
  const id = node("input", "extra-memory-id");
  id.placeholder = "唯一 ID"; id.value = values.id || "";
  const category = node("input", "extra-memory-category");
  category.placeholder = "分类"; category.value = values.category || "";
  const priority = node("input", "extra-memory-priority");
  priority.type = "number"; priority.min = "0"; priority.placeholder = "优先级 100"; priority.value = values.priority ?? "";
  const content = node("textarea", "extra-memory-content");
  content.placeholder = "额外长期设定内容"; content.value = values.content || "";
  const remove = node("button", "text-button danger-button", "删除");
  remove.type = "button"; remove.onclick = () => row.remove();
  row.append(id, category, priority, content, remove);
  els.extraCoreMemoryList.appendChild(row);
}

function toggleInitWorldFields() {
  const fanfic = els.initFanfic.checked;
  els.initOriginalFields.hidden = fanfic;
  els.initFanficFields.hidden = !fanfic;
  for (const control of els.initOriginalFields.querySelectorAll("input, textarea, select")) control.disabled = fanfic;
  for (const control of els.initFanficFields.querySelectorAll("input, textarea, select")) control.disabled = !fanfic;
  for (const name of ["world_name", "world_style"]) els.initForm.elements[name].required = !fanfic;
  for (const name of ["work_name", "birth_location", "story_era"]) els.initForm.elements[name].required = fanfic;
  toggleCustomAbilityField();
}

function toggleCustomAbilityField() {
  const visible = !els.initFanfic.checked && els.initAbilitySystem.value === "自定义";
  els.initCustomAbilityWrap.hidden = !visible;
  els.initForm.elements.custom_ability_system.disabled = !visible;
  els.initForm.elements.custom_ability_system.required = visible;
}

function showStoryDirectionStage(result) {
  initConfigSnapshot = result.config;
  els.storyDirectionContent.value = result.story_direction?.content || "";
  els.storyDirectionCurrency.value = result.story_direction?.currency_unit || "";
  els.initConfigFields.disabled = true;
  els.initConfigActions.hidden = true;
  els.storyDirectionStage.hidden = false;
  els.storyDirectionActions.hidden = false;
  els.initStageHint.textContent = "检查并修改故事方向；确认后才会生成开场并写入存档。";
  els.storyDirectionContent.focus();
}

function editInitConfig() {
  initConfigSnapshot = null;
  els.initConfigFields.disabled = false;
  els.initConfigActions.hidden = false;
  els.storyDirectionStage.hidden = true;
  els.storyDirectionActions.hidden = true;
  els.initStageHint.textContent = "填写世界与主角设定，然后先生成一份可修改的长期故事方向。";
  els.initError.hidden = true;
}

async function requestStoryDirection({ regenerate = false } = {}) {
  els.initError.hidden = true;
  let config;
  try {
    if (regenerate && initConfigSnapshot) config = initConfigSnapshot;
    else {
      if (!els.initForm.reportValidity()) return;
      config = readInitConfig(els.initForm);
    }
  } catch (error) {
    els.initError.textContent = error.message;
    els.initError.hidden = false;
    return;
  }
  const button = regenerate ? els.regenerateStoryDirectionBtn : els.storyDirectionBtn;
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = "正在生成……";
  try {
    const operationId = await stream("/api/player/init/story-direction", {
      ...config,
      request_id: newRequestId(),
    }, handleInitializationEvent);
    await acknowledgeHandledOperation(operationId);
  } catch (error) {
    els.initError.textContent = error.message;
    els.initError.hidden = false;
    await acknowledgeFailedOperation(error);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function initialize() {
  const content = els.storyDirectionContent.value.trim();
  if (!initConfigSnapshot || !content) {
    els.initError.textContent = "请先生成并确认故事方向";
    els.initError.hidden = false;
    return;
  }
  const payload = {
    config: initConfigSnapshot,
    story_direction: {
      content,
      currency_unit: els.storyDirectionCurrency.value.trim() || null,
    },
  };
  setMode("generating"); els.initSubmitBtn.disabled = true; els.initSubmitBtn.textContent = "正在生成开场……"; els.initError.hidden = true;
  try {
    const operationId = await stream("/api/player/init", {
      ...payload,
      request_id: newRequestId(),
    }, handleInitializationEvent);
    await acknowledgeHandledOperation(operationId);
    els.initDialog.close();
    await loadAll({ scroll: "instant" });
  }
  catch (error) {
    setMode("needs_init"); els.initError.textContent = error.message; els.initError.hidden = false;
    await acknowledgeFailedOperation(error);
  }
  finally { els.initSubmitBtn.disabled = false; els.initSubmitBtn.textContent = "生成开场"; renderControls(); }
}

function openLeftDrawer() {
  if (els.leftDrawer) {
    els.leftDrawer.classList.add("open");
    els.leftDrawer.setAttribute("aria-hidden", "false");
    els.drawerBackdrop.hidden = false;
  }
}
function closeLeftDrawer() {
  if (els.leftDrawer) {
    els.leftDrawer.classList.remove("open");
    els.leftDrawer.setAttribute("aria-hidden", "true");
    if (!els.drawer?.classList.contains("open")) {
      els.drawerBackdrop.hidden = true;
    }
  }
}
function openDrawer() { els.drawer.classList.add("open"); els.drawer.setAttribute("aria-hidden", "false"); els.drawerBackdrop.hidden = false; }
function closeDrawer() {
  els.drawer.classList.remove("open");
  els.drawer.setAttribute("aria-hidden", "true");
  if (!els.leftDrawer?.classList.contains("open")) {
    els.drawerBackdrop.hidden = true;
  }
}
function isNearBottom() {
  return document.documentElement.scrollHeight - window.innerHeight - window.scrollY < 180;
}

function scrollToBottom(mode = "smooth", onSettled = null) {
  const behavior = mode === "smooth" ? "smooth" : "auto";
  const move = () => window.scrollTo({
    top: document.documentElement.scrollHeight,
    behavior,
  });
  window.requestAnimationFrame(() => {
    move();
    if (mode === "instant") {
      window.requestAnimationFrame(() => { move(); onSettled?.(); });
    } else {
      window.setTimeout(() => onSettled?.(), 450);
    }
  });
}
function autoSizeInput() { els.playerInput.style.height = "auto"; els.playerInput.style.height = `${Math.min(210, els.playerInput.scrollHeight)}px`; }

els.playerInput.addEventListener("input", () => {
  autoSizeInput();
  renderControls({ updateNavigation: false });
});
els.story.addEventListener("mousedown", handleHighlightPointerDown);
els.story.addEventListener("mousemove", handleHighlightPointerMove);
els.story.addEventListener("click", handleHighlightClick);
els.story.addEventListener("keydown", handleHighlightKeydown);
els.playerInput.addEventListener("focus", autoSizeInput);
els.playerInput.addEventListener("blur", () => { els.playerInput.style.height = ""; });
els.playerInput.addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); generateTurn(); } });
els.generateBtn.onclick = generateTurn;
els.stopGenerationBtn.onclick = stopActiveGeneration;
els.dismissErrorBtn.onclick = clearError;
els.openDrawerBtn.onclick = openDrawer; els.closeDrawerBtn.onclick = closeDrawer;
if (els.openLeftDrawerBtn) els.openLeftDrawerBtn.onclick = openLeftDrawer;
if (els.closeLeftDrawerBtn) els.closeLeftDrawerBtn.onclick = closeLeftDrawer;
els.drawerBackdrop.onclick = () => { closeDrawer(); closeLeftDrawer(); };
els.saveSelect.onchange = () => switchSave(els.saveSelect.value);
els.newSaveBtn.onclick = () => { els.saveNameInput.value = ""; els.saveDialog.showModal(); window.setTimeout(() => els.saveNameInput.focus(), 0); };
els.saveCancelBtn.onclick = () => els.saveDialog.close();
els.saveForm.addEventListener("submit", async (event) => { event.preventDefault(); const name = els.saveNameInput.value.trim(); if (!name) return; els.saveDialog.close(); try { await createSave(name); } catch (error) { showError(error); } });
els.manageSavesBtn.onclick = () => {
  saveManagerMessage();
  renderSaveManager();
  els.saveManagerDialog.showModal();
};
els.modelSettingsBtn.onclick = () => {
  closeDrawer();
  openModelConfig(state.modelConfig, { required: false });
};
els.closeModelConfigBtn.onclick = () => els.modelConfigDialog.close();
els.testModelBtn.onclick = testModelConnection;
els.modelConfigForm.addEventListener("submit", (event) => {
  event.preventDefault();
  saveModelSettings();
});
for (const input of els.modelConfigForm.querySelectorAll("input")) {
  input.addEventListener("input", invalidateModelConnectionTest);
  input.addEventListener("change", invalidateModelConnectionTest);
}
els.modelConfigDialog.addEventListener("cancel", (event) => {
  if (els.modelConfigDialog.dataset.required === "true") event.preventDefault();
});
els.feedbackRewriteCancelBtn.onclick = () => els.feedbackRewriteDialog.close();
els.feedbackRewriteForm.addEventListener("submit", (event) => {
  event.preventDefault();
  rewriteFromFeedback();
});
els.closeCodexBtn.onclick = closeCodexProfile;
els.closeSaveManagerBtn.onclick = () => els.saveManagerDialog.close();
els.importSaveBtn.onclick = () => els.importSaveInput.click();
els.importSaveInput.onchange = () => {
  const file = els.importSaveInput.files?.[0];
  els.importSaveInput.value = "";
  if (!file) return;
  selectedImportFile = file;
  const suggestedName = file.name.replace(/\.zip$/i, "").trim() || "导入的旅程";
  els.importSaveFileName.textContent = `已选择：${file.name}`;
  els.importSaveName.value = suggestedName;
  els.importSaveForm.hidden = false;
  els.importSaveName.focus();
};
els.cancelImportSaveBtn.onclick = () => {
  selectedImportFile = null;
  els.importSaveForm.hidden = true;
};
els.importSaveForm.onsubmit = (event) => {
  event.preventDefault();
  const file = selectedImportFile;
  const name = els.importSaveName.value.trim();
  if (!file || !name) return;
  runSaveManagerAction(async () => {
    await importSaveArchive(file, name);
    selectedImportFile = null;
    els.importSaveForm.hidden = true;
  }, "存档已导入");
};
els.difficultySelect.onchange = async () => { try { await request("/api/player/difficulty", { method: "POST", body: JSON.stringify({ difficulty: els.difficultySelect.value }) }); state.playerState.difficulty = els.difficultySelect.value; showToast("难度已更新"); } catch (error) { showError(error); } };
els.diceDisplaySelect.onchange = async () => {
  const previous = state.playerState.dice_display || "outcome";
  try {
    const result = await request("/api/player/dice-display", {
      method: "POST",
      body: JSON.stringify({ dice_display: els.diceDisplaySelect.value }),
    });
    state.playerState.dice_display = result.dice_display;
    renderHistory();
    renderPending();
    showToast("判定显示方式已更新");
  } catch (error) {
    els.diceDisplaySelect.value = previous;
    showError(error);
  }
};
els.fontSizeSelect.onchange = readerPreferences.save;
els.fontFamilySelect.onchange = readerPreferences.save;
els.themeSelect.onchange = readerPreferences.save;
els.undoBtn.onclick = () => changeHistory("undo"); els.redoBtn.onclick = () => changeHistory("redo");
els.turnNavigatorLatest.onclick = jumpToLatestTurn;
els.initFanfic.onchange = toggleInitWorldFields;
els.initAbilitySystem.onchange = toggleCustomAbilityField;
els.addCoreMemoryBtn.onclick = () => addExtraCoreMemoryRow();
els.storyDirectionBtn.onclick = () => requestStoryDirection();
els.regenerateStoryDirectionBtn.onclick = () => requestStoryDirection({ regenerate: true });
els.editInitConfigBtn.onclick = editInitConfig;
els.initForm.addEventListener("submit", (event) => { event.preventDefault(); initialize(); });
els.initDialog.addEventListener("cancel", (event) => event.preventDefault());
window.addEventListener("scroll", () => {
  if (window.scrollY <= 220) loadEarlierHistory();
}, { passive: true });
window.addEventListener("resize", () => {
  refreshNavigationObserver({ force: true });
}, { passive: true });

toggleInitWorldFields();
bootstrap().catch(showError);
