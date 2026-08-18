import { acknowledgeOperation, cancelOperation, downloadSave, importSaveArchive, loadCoreMemory, loadEvents, loadRecords, request, saveCoreMemory, saveExperienceMode, stream, subscribe, updateRecord } from "./api.js";
import { playerErrorFeedback } from "./error_feedback.js";
import {
  enhanceCustomSelect,
  syncCustomSelect,
  syncCustomSelects,
} from "./custom_select.js";
import { createReaderPreferencesController } from "./reader_preferences.js";
import { bindPopoverFallbackDismiss } from "./popover_fallback.js";
import { bindSettingInfoButtons, bindSettingInfoDismiss } from "./setting_info.js";
import {
  installClientDiagnostics,
  reportClientError,
} from "./client_diagnostics.js";
import { createTokenUsageController, pendingFooterMode, renderCardTokenFooter } from "./token_usage.js";
import {
  activePending,
  applySavedDraft,
  hasDraft,
  modeLabels,
  readDraft,
  selectedVariant,
  setMode,
  shouldAutoConfirmTurn,
  state,
} from "./state.js";
import { migrateStorageKey } from "./storage_migration.js";

const $ = (id) => document.getElementById(id);
const els = {
  worldTime: $("worldTime"), worldLocation: $("worldLocation"), statusPill: $("statusPill"),
  story: $("story"), welcome: $("welcome"), continueInitBtn: $("continueInitBtn"), history: $("history"),
  activePlayerAction: $("activePlayerAction"),
  streamingCard: $("streamingCard"), streamingText: $("streamingText"), streamingLabel: $("streamingLabel"),
  stopGenerationBtn: $("stopGenerationBtn"),
  pendingCard: $("pendingCard"), suggestions: $("suggestions"), suggestionList: $("suggestionList"),
  turnNavigator: $("turnNavigator"), turnNavigatorList: $("turnNavigatorList"),
  turnNavigatorLatest: $("turnNavigatorLatest"),
  errorCard: $("errorCard"), errorTitle: $("errorTitle"), errorMessage: $("errorMessage"), dismissErrorBtn: $("dismissErrorBtn"),
  playerInput: $("playerInput"), generateBtn: $("generateBtn"), inputHint: $("inputHint"),
  composerModeBadge: $("composerModeBadge"),
  drawer: $("drawer"), leftDrawer: $("leftDrawer"), drawerBackdrop: $("drawerBackdrop"), openDrawerBtn: $("openDrawerBtn"), closeDrawerBtn: $("closeDrawerBtn"),
  closeLeftDrawerBtn: $("closeLeftDrawerBtn"), codexMetaBar: $("codexMetaBar"),
  recordsDrawer: $("recordsDrawer"),
  recordsRefreshState: $("recordsRefreshState"),
  openLeftDrawerRailBtn: $("openLeftDrawerRailBtn"),
  experienceModePlayerBtn: $("experienceModePlayerBtn"), experienceModeCreatorBtn: $("experienceModeCreatorBtn"),
  recordsContinuityList: $("recordsContinuityList"), recordsNpcList: $("recordsNpcList"), recordsSettingList: $("recordsSettingList"), recordsEventList: $("recordsEventList"),
  recordsRail: $("recordsRail"), recordsTabContinuity: $("recordsTabContinuity"), recordsTabNpc: $("recordsTabNpc"), recordsTabSetting: $("recordsTabSetting"), recordsTabEvent: $("recordsTabEvent"),
  openCoreMemoryBtn: $("openCoreMemoryBtn"), coreMemoryDialog: $("coreMemoryDialog"), closeCoreMemoryBtn: $("closeCoreMemoryBtn"),
  addCoreMemoryBtn: $("addCoreMemoryBtn"), saveCoreMemoryBtn: $("saveCoreMemoryBtn"), coreMemoryList: $("coreMemoryList"), coreMemoryError: $("coreMemoryError"),
  saveSelect: $("saveSelect"), newSaveBtn: $("newSaveBtn"), manageSavesBtn: $("manageSavesBtn"), playerSummary: $("playerSummary"), sceneSummary: $("sceneSummary"), resourceSummary: $("resourceSummary"),
  modelSettingsBtn: $("modelSettingsBtn"), modelConfigDialog: $("modelConfigDialog"), modelConfigForm: $("modelConfigForm"),
  closeModelConfigBtn: $("closeModelConfigBtn"), modelConfigIntro: $("modelConfigIntro"), modelApiKey: $("modelApiKey"),
  modelKeyHint: $("modelKeyHint"), modelBaseUrl: $("modelBaseUrl"), modelName: $("modelName"),
  modelThinking: $("modelThinking"), modelTestResult: $("modelTestResult"), testModelBtn: $("testModelBtn"), saveModelBtn: $("saveModelBtn"),
  feedbackRewriteDialog: $("feedbackRewriteDialog"), feedbackRewriteForm: $("feedbackRewriteForm"),
  feedbackRewriteInput: $("feedbackRewriteInput"), feedbackRewriteCancelBtn: $("feedbackRewriteCancelBtn"),
  difficultySelect: $("difficultySelect"), diceDisplaySelect: $("diceDisplaySelect"), canonValidationBtn: $("canonValidationBtn"), canonInfoBtn: $("canonInfoBtn"), tokenUsageInfoBtn: $("tokenUsageInfoBtn"), oneClickInfoBtn: $("oneClickInfoBtn"), settingInfoDialog: $("settingInfoDialog"), settingInfoTitle: $("settingInfoTitle"), settingInfoContent: $("settingInfoContent"), fontSizeSelect: $("fontSizeSelect"), fontFamilySelect: $("fontFamilySelect"), themeSelect: $("themeSelect"), tokenUsageSelect: $("tokenUsageSelect"), oneClickConfirm: $("oneClickConfirm"), tokenUsageDialog: $("tokenUsageDialog"), tokenUsageDialogContent: $("tokenUsageDialogContent"), undoBtn: $("undoBtn"), redoBtn: $("redoBtn"),
  codexDialog: $("codexDialog"), codexTitle: $("codexTitle"), codexContent: $("codexContent"), codexFooter: $("codexFooter"), closeCodexBtn: $("closeCodexBtn"),
  codexConfirmDialog: $("codexConfirmDialog"), codexConfirmEyebrow: $("codexConfirmEyebrow"), codexConfirmTitle: $("codexConfirmTitle"), codexConfirmMessage: $("codexConfirmMessage"), codexConfirmCancelBtn: $("codexConfirmCancelBtn"), codexConfirmOkBtn: $("codexConfirmOkBtn"),
  saveDialog: $("saveDialog"), saveForm: $("saveForm"), saveNameInput: $("saveNameInput"), saveCancelBtn: $("saveCancelBtn"),
  saveManagerDialog: $("saveManagerDialog"), closeSaveManagerBtn: $("closeSaveManagerBtn"), saveCardList: $("saveCardList"),
  importSaveBtn: $("importSaveBtn"), importSaveInput: $("importSaveInput"), importSaveForm: $("importSaveForm"),
  importSaveName: $("importSaveName"), importSaveFileName: $("importSaveFileName"), cancelImportSaveBtn: $("cancelImportSaveBtn"),
  saveManagerError: $("saveManagerError"),
  initDialog: $("initDialog"), initForm: $("initForm"), closeInitDialogBtn: $("closeInitDialogBtn"), initSubmitBtn: $("initSubmitBtn"), initError: $("initError"),
  initConfigFields: $("initConfigFields"), initOriginalFields: $("initOriginalFields"),
  initAbilitySystem: $("initAbilitySystem"), initCustomAbilityWrap: $("initCustomAbilityWrap"),
  initWorldStyle: $("initWorldStyle"), initCustomWorldStyleWrap: $("initCustomWorldStyleWrap"),
  initConfigActions: $("initConfigActions"), campaignSetupActions: $("campaignSetupActions"), campaignSetupStage: $("campaignSetupStage"),
  campaignSetupBtn: $("campaignSetupBtn"), regenerateCampaignSetupBtn: $("regenerateCampaignSetupBtn"), editInitConfigBtn: $("editInitConfigBtn"),
  setupCurrencyUnit: $("setupCurrencyUnit"), setupPowerSource: $("setupPowerSource"),
  setupPowerPrevalence: $("setupPowerPrevalence"), setupPowerLearningDifficulty: $("setupPowerLearningDifficulty"),
  setupPowerLimitation: $("setupPowerLimitation"),
  setupPeoplesAndBeings: $("setupPeoplesAndBeings"), setupMajorWorldConflicts: $("setupMajorWorldConflicts"),
  setupDominantPowers: $("setupDominantPowers"), setupCivilizationBaseline: $("setupCivilizationBaseline"),
  abilityRows: $("abilityRows"), addAbilityBtn: $("addAbilityBtn"),
  storyInspirationTags: $("storyInspirationTags"), initStageHint: $("initStageHint"),
  toast: $("toast"),
};

const progressLabels = {
  state_loaded: "正在读取世界状态",
  npc_profiles_loaded: "正在载入人物档案",
  action_parsed: "正在解析行动意图",
  context_facts_done: "前情已检索 · 正在推演因果",
  rolls_done: "检定已结算 · 正在推演",
  turn_resolution_done: "机制判定达成 · 正在构思正文",
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
  scene_facts_done: "正在提取场景事实",
  state_update_done: "正在更新世界状态",
  continuity_projected: "正在整理未决事项",
  npc_profile_updated: "正在更新人物信息",
  action_preview_done: "下一步方向已经生成",
  npc_profiles_updated: "正在写入旅程",
  campaign_setup_started: "正在生成公开世界惯例",
  initialization_started: "正在准备开场",
  opening_foundation_ready: "私有开局基础已经准备完成",
  opening_settings_started: "正在建立世界设定",
  opening_settings_done: "世界设定已经建立",
  opening_scene_done: "开场正文已经生成",
  initialization_pending: "正在保存待确认开场",
};
let streamBuffer = "";
let streamFrame = 0;
let initConfigSnapshot = null;
let campaignSetupCommitted = false;
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

const tokenUsage = createTokenUsageController({
  node,
});

const readerPreferences = createReaderPreferencesController({
  elements: {
    fontSize: els.fontSizeSelect,
    fontFamily: els.fontFamilySelect,
    theme: els.themeSelect,
    tokenUsage: els.tokenUsageSelect,
    oneClickConfirm: els.oneClickConfirm,
  },
  request,
  state,
  showToast,
  showError,
  onTokenUsageVisibilityChange: (visible) => {
    if (tokenUsage.isVisible() === visible) return;
    tokenUsage.setVisible(visible);

    const pending = activePending();
    if (!pending || pending.variants.length <= 1) return;

    const oldFooter = els.pendingCard.querySelector(".turn-token-footer");
    if (oldFooter) oldFooter.remove();

    const variant = selectedVariant();
    const footer = _buildPendingFooter(pending, variant);
    if (footer) {
      const actions = els.pendingCard.querySelector(".pending-actions");
      els.pendingCard.insertBefore(footer, actions);
    }
  },
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

function showError(
  error,
  fallback = "之前的故事没有受到影响，你可以重新尝试。",
  options = {},
) {
  reportClientError(error, "displayed_error");
  state.lastError = error;
  const feedback = playerErrorFeedback(error, fallback, options);
  setMode("error");
  els.errorTitle.textContent = feedback.title;
  els.errorMessage.textContent = feedback.message;
  els.errorCard.hidden = false;
  renderControls();
  scrollToBottom();
}

function clearError() {
  state.lastError = null;
  els.errorCard.hidden = true;
  els.errorCard.querySelector(".error-retry-btn")?.remove();
  const pending = activePending();
  setMode(!state.modelConfig?.configured ? "needs_model" : state.playerState?.needs_init ? "needs_init" : pending ? "pending" : "idle");
  renderControls();
}

function showComposerEmptyMessage() {
  els.errorCard.querySelector(".error-retry-btn")?.remove();
  const pending = activePending(); const variant = selectedVariant();
  setMode(pending ? "pending" : "idle");
  els.errorTitle.textContent = "正文生成失败";
  if (pending && variant) {
    els.errorMessage.textContent = "之前的故事没有受到影响，你可以重新措辞来重试。";
    const retryBtn = document.createElement("button");
    retryBtn.className = "error-retry-btn";
    retryBtn.type = "button";
    retryBtn.textContent = "重新措辞";
    retryBtn.onclick = () => { clearError(); retryTurn("rephrase"); };
    els.errorCard.appendChild(retryBtn);
  } else {
    els.errorMessage.textContent = "之前的故事没有受到影响，你可以重试生成。";
    const savedInput = state.composerEmptyUserInput || "";
    if (savedInput) {
      els.playerInput.value = savedInput;
      autoSizeInput();
    }
    const retryBtn = document.createElement("button");
    retryBtn.className = "error-retry-btn";
    retryBtn.type = "button";
    retryBtn.textContent = "重试生成";
    retryBtn.onclick = () => { clearError(); generateTurn(); };
    els.errorCard.appendChild(retryBtn);
  }
  els.errorCard.hidden = false;
  renderControls();
  scrollToBottom();
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

  if (pending?.kind === "opening" && !busy) {
    if (els.composerModeBadge) els.composerModeBadge.hidden = true;
    els.playerInput.disabled = true;
    els.playerInput.placeholder = "请先确认或重新生成开场";
    setInputHintText("等待确认开场");
    els.generateBtn.disabled = true;
    const btnSpan = els.generateBtn.querySelector("span");
    if (btnSpan) btnSpan.textContent = "执行行动";
  } else if (pending && !busy) {
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
  els.canonValidationBtn.disabled = busy;
  els.continueInitBtn.disabled = state.mode === "switching";
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
  const time = campaign.world_time || "";
  const location = campaign.location || "";
  els.worldTime.textContent = time;
  els.worldLocation.textContent = location;
  const parent = els.worldTime?.parentElement;
  if (parent) {
    if (time || location) {
      parent.classList.add("is-ready");
    } else {
      parent.classList.remove("is-ready");
    }
  }
}

function renderCanonValidation() {
  const enabled = Boolean(state.playerState?.canon_validation_enabled);
  els.canonValidationBtn.checked = enabled;
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
    const mark = node("mark", `story-highlight story-highlight-${highlight?.kind || "npc"}`, value);
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
  const usageData = turn.token_usage || tokenUsage.turnUsageStore.get(String(turn.turn_index)) || tokenUsage.turnUsageStore.get(String(turn.turn_id));
  const tokenFooter = renderCardTokenFooter(usageData, { visible: tokenUsage.isVisible(), detailButtons: tokenUsage.isVisible() });
  if (tokenFooter) card.appendChild(tokenFooter);
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
  const needsInit = Boolean(state.playerState?.needs_init);
  els.continueInitBtn.hidden = !needsInit;
  const welcomeTitle = els.welcome.querySelector("h1");
  const welcomeText = els.welcome.querySelector("h1 + p");
  if (welcomeTitle) welcomeTitle.textContent = needsInit ? "故事尚未启程" : "故事等待你的选择";
  if (welcomeText) {
    welcomeText.textContent = needsInit
      ? "初始化草稿会按当前存档保留，你可以随时返回继续。"
      : "这里没有预设答案。描述你想做的事，世界会记住已经发生的一切。";
  }
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
  const previousActiveKey = navigationActiveKey;
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
  const targetActiveKey = (previousActiveKey && navigationButtons.has(previousActiveKey))
    ? previousActiveKey
    : (entries.length > 0 ? entries[entries.length - 1].key : null);
  if (targetActiveKey) {
    setNavigationActive(targetActiveKey);
  }
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

function _buildVersionControl(pending) {
  const versions = node("div", "version-control");
  const previous = node("button", "", "‹");
  const position = node("span", "", `${state.selectedPendingIndex + 1} / ${pending.variants.length}`);
  const next = node("button", "", "›");
  previous.disabled = state.selectedPendingIndex === 0;
  next.disabled = state.selectedPendingIndex === pending.variants.length - 1;
  previous.onclick = () => selectVariant(state.selectedPendingIndex - 1);
  next.onclick = () => selectVariant(state.selectedPendingIndex + 1);
  versions.append(previous, position, next);
  return versions;
}

function _buildPendingFooter(pending, variant) {
  const usageData = variant.token_usage || tokenUsage.getVariantUsage(variant.turn_id) || tokenUsage.getCurrentPendingUsage();
  const showVersions = pending.variants.length > 1;
  const mode = pendingFooterMode({
    showToken: tokenUsage.isVisible(),
    showVersions,
    usageData,
  });
  if (mode === "none") return null;
  if (mode === "token") {
    return renderCardTokenFooter(usageData, {
      visible: true,
      detailButtons: true,
      appendToRow: showVersions ? _buildVersionControl(pending) : null,
    });
  }
  const footer = node("div", "turn-token-footer turn-version-footer");
  const row = node("div", "turn-token-row");
  row.appendChild(_buildVersionControl(pending));
  footer.appendChild(row);
  return footer;
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
  toolbar.append(kicker);

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
  const rewriteOpening = node("button", "soft-button", "重新生成开场");
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

  rewriteOpening.disabled = ["generating", "confirming"].includes(state.mode);
  rewriteOpening.title = "使用相同初始化设定生成一个新版本，原版本会保留";
  rewriteOpening.onclick = retryOpening;

  discard.disabled = confirming;
  discard.onclick = discardPending;

  actions.appendChild(confirm);
  actions.appendChild(edit);
  if (hasDraft(variant)) actions.appendChild(restoreOriginal);

  if (pending.kind === "opening") {
    actions.appendChild(rewriteOpening);
  } else {
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
  const pendingFooter = _buildPendingFooter(pending, variant);
  if (pendingFooter) els.pendingCard.appendChild(pendingFooter);

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
      showError(error, undefined, { pendingPreserved: true });
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
    showError(error, undefined, { pendingPreserved: true });
  }
}

function selectVariant(index) {
  const pending = activePending();
  if (!pending || index < 0 || index >= pending.variants.length) return;
  state.selectedPendingIndex = index;
  renderPending();
  scrollToBottom("instant");
}

let activeCodexProfile = null;
let activeCodexTrigger = null;

function closeCodexProfile() {
  if (els.codexDialog && els.codexDialog.open) {
    els.codexDialog.close();
    els.codexDialog.classList.remove("is-floating");
    els.codexDialog.style.top = "";
    els.codexDialog.style.left = "";
    if (els.codexFooter) {
      els.codexFooter.hidden = true;
      els.codexFooter.replaceChildren();
    }
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
  if (isDraggingCodex) return;
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
    if (!els.codexDialog.contains(event.target) && !event.target.closest(".story-highlight") && !event.target.closest(".codex-participant-chip")) {
      closeCodexProfile();
    }
  }
}

function handleCodexKeydown(event) {
  if (event.key === "Escape") {
    closeCodexProfile();
  }
}

function positionCodexPopover(triggerElement, keepCurrentPosition = false) {
  if (!els.codexDialog || !els.codexDialog.open) return;

  if (window.innerWidth < 640) {
    els.codexDialog.style.position = "";
    els.codexDialog.style.top = "";
    els.codexDialog.style.left = "";
    els.codexDialog.style.margin = "";
    els.codexDialog.style.width = "";
    return;
  }

  if (!els.codexDialog.classList.contains("is-floating")) {
    els.codexDialog.close();
    els.codexDialog.classList.add("is-floating");
    els.codexDialog.show();
  }

  const dialogWidth = Math.min(500, window.innerWidth - 24);
  const popoverHeight = els.codexDialog.offsetHeight || 220;

  const triggerRect = triggerElement ? triggerElement.getBoundingClientRect() : null;
  const isTriggerVisible = triggerRect && triggerRect.bottom > 0 && triggerRect.top < window.innerHeight;

  let left = 16;
  let top = 80;

  if (keepCurrentPosition || !isTriggerVisible) {
    const curTop = parseFloat(els.codexDialog.style.top) || (window.innerHeight / 2 - popoverHeight / 2);
    const curLeft = parseFloat(els.codexDialog.style.left) || 16;

    top = Math.max(16, Math.min(curTop, window.innerHeight - popoverHeight - 16));
    left = Math.max(12, Math.min(curLeft, window.innerWidth - dialogWidth - 12));
  } else {
    left = triggerRect.left + triggerRect.width / 2 - dialogWidth / 2;
    left = Math.max(12, Math.min(left, window.innerWidth - dialogWidth - 12));

    top = triggerRect.bottom + 10;
    if (top + popoverHeight > window.innerHeight - 16) {
      top = Math.max(16, triggerRect.top - popoverHeight - 10);
      if (top < 16 || top + popoverHeight > window.innerHeight - 16) {
        top = Math.max(16, window.innerHeight - popoverHeight - 16);
      }
    }
    top = Math.max(16, Math.min(top, window.innerHeight - popoverHeight - 16));
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

function presentCodexDialog(triggerElement) {
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

const SETTING_KIND_LABELS = {
  faction: "势力",
  location: "地点",
  item: "物品",
  lore: "知识",
};

function openSettingCodex(profile, triggerElement) {
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

  // 1. 顶部结构：统一双列属性网格
  const kindLabel = SETTING_KIND_LABELS[profile.setting_kind] || profile.setting_kind || "世界设定";
  const metaGrid = node("div", "codex-meta-grid");
  const appendMetaRow = (key, val) => {
    if (!val) return;
    const row = node("div", "codex-meta-row");
    row.append(
      node("span", "meta-key", key),
      node("span", "meta-val", val),
    );
    metaGrid.appendChild(row);
  };
  appendMetaRow("类别", kindLabel);
  els.codexContent.appendChild(metaGrid);

  // 2. 核心引语区：设定概述 (summary)
  if (profile.summary) {
    els.codexContent.appendChild(node("div", "codex-quote-box", `“${profile.summary}”`));
  }

  // 3. 渐进式折叠详查区：公开记载
  if (profile.public_facts?.length) {
    const factsPanel = node("div", "codex-details-panel");
    factsPanel.hidden = true;
    const facts = node("section", "codex-field");
    facts.append(node("span", "resource-label", "公开记载"));
    const factList = node("ul", "codex-fact-list");
    profile.public_facts.forEach((fact) => factList.appendChild(node("li", "", fact)));
    facts.appendChild(factList);
    factsPanel.appendChild(facts);

    if (factsPanel.childNodes.length > 0) {
      els.codexContent.appendChild(factsPanel);
      const toggleBtn = node("button", "codex-toggle-more", "▸ 展开完整记载");
      toggleBtn.onclick = () => {
        const isExpanding = factsPanel.hidden;
        factsPanel.hidden = !isExpanding;
        toggleBtn.textContent = isExpanding ? "▾ 收起记载" : "▸ 展开完整记载";
        els.codexDialog.classList.toggle("is-expanded", isExpanding);
        window.requestAnimationFrame(() => positionCodexPopover(triggerElement, true));
      };
      if (els.codexFooter) {
        els.codexFooter.hidden = false;
        els.codexFooter.replaceChildren(toggleBtn);
      }
    } else if (els.codexFooter) {
      els.codexFooter.hidden = true;
      els.codexFooter.replaceChildren();
    }
  } else if (els.codexFooter) {
    els.codexFooter.hidden = true;
    els.codexFooter.replaceChildren();
  }

  presentCodexDialog(triggerElement);
}

function openCodexProfile(profile, triggerElement) {
  if (!profile) return;

  if (profile.kind === "setting") {
    openSettingCodex(profile, triggerElement);
    return;
  }

  if (els.codexDialog && els.codexDialog.open && (activeCodexProfile === profile || (triggerElement && activeCodexTrigger === triggerElement))) {
    closeCodexProfile();
    return;
  }

  activeCodexProfile = profile;
  activeCodexTrigger = triggerElement || null;

  els.codexDialog.classList.remove("is-expanded");
  els.codexTitle.textContent = profile.name || "典籍条目";
  els.codexContent.replaceChildren();

  // 1. 核心引语区：优先展示最近出场记录与出场回合（支持点击跳转），若无则展示外貌描述
  const quoteText = profile.last_scene_summary || profile.appearance || "";
  if (quoteText) {
    const quoteBox = node("div", "codex-quote-box");
    const subLabelEl = node("div", "codex-quote-sublabel");
    if (profile.last_scene_summary) {
      subLabelEl.appendChild(document.createTextNode("最近出场"));
      if (profile.last_scene_turn != null) {
        subLabelEl.appendChild(document.createTextNode(" · "));
        const turnJumpBtn = node("button", "codex-turn-jump-btn", `第 ${profile.last_scene_turn} 回合`);
        turnJumpBtn.type = "button";
        turnJumpBtn.title = `点击跳转至第 ${profile.last_scene_turn} 回合`;
        turnJumpBtn.onclick = () => {
          closeCodexProfile();
          jumpToHistoryTurn(profile.last_scene_turn);
        };
        subLabelEl.appendChild(turnJumpBtn);
      }
    } else {
      subLabelEl.appendChild(document.createTextNode("外貌描述"));
    }
    quoteBox.appendChild(subLabelEl);
    quoteBox.appendChild(node("div", "codex-quote-text", `“${quoteText}”`));
    els.codexContent.appendChild(quoteBox);
  }

  // 2. 属性网格：阵营 -> 身份 -> 特质 -> 地点 -> 态度
  const metaGrid = node("div", "codex-meta-grid");

  const appendMetaRow = (key, val) => {
    if (!val) return;
    const row = node("div", "codex-meta-row");
    row.append(
      node("span", "meta-key", key),
      node("span", "meta-val", val),
    );
    metaGrid.appendChild(row);
  };

  if (profile.faction) appendMetaRow("阵营", profile.faction);
  if (profile.role) appendMetaRow("身份", profile.role);
  if (profile.personality_trait) appendMetaRow("特质", profile.personality_trait);
  if (profile.last_known_location) appendMetaRow("地点", profile.last_known_location);
  if (profile.attitude_to_player) appendMetaRow("态度", profile.attitude_to_player);

  if (metaGrid.childNodes.length > 0) {
    els.codexContent.appendChild(metaGrid);
  }

  // 3. 标签胶囊区：展示别名（已知能力在下方详述中展示，避免重复）
  const pillsContainer = node("div", "codex-pills");
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
    if (!value) return;
    const row = node("section", "codex-field");
    row.append(node("span", "resource-label", label), node("p", "", value));
    detailsPanel.appendChild(row);
  };

  // 已知能力：移至外貌特征上方
  if (profile.known_abilities?.length) {
    const abilitiesSection = node("section", "codex-field codex-abilities-field");
    abilitiesSection.appendChild(node("span", "resource-label", "已知能力"));
    const abilityList = node("div", "codex-ability-list");
    profile.known_abilities.forEach((a) => {
      const item = node("div", "codex-ability-item");
      const pill = node("span", "codex-pill pill-ability", a.name);
      const desc = node("span", "codex-ability-desc", a.description || "暂无详细描述");
      item.append(pill, desc);
      abilityList.appendChild(item);
    });
    abilitiesSection.appendChild(abilityList);
    detailsPanel.appendChild(abilitiesSection);
  }

  // 外貌特征（在已知能力下方）
  if (profile.appearance) {
    appendDetailText("外貌特征", profile.appearance);
  }

  // 最后出场地点
  if (profile.last_known_location) {
    appendDetailText("最后出场地点", profile.last_known_location);
  }

  if (detailsPanel.childNodes.length > 0) {
    els.codexContent.appendChild(detailsPanel);
    const toggleBtn = node("button", "codex-toggle-more", "▸ 展开完整记载");
    toggleBtn.onclick = () => {
      const isExpanding = detailsPanel.hidden;
      detailsPanel.hidden = !isExpanding;
      toggleBtn.textContent = isExpanding ? "▾ 收起记载" : "▸ 展开完整记载";
      els.codexDialog.classList.toggle("is-expanded", isExpanding);
      window.requestAnimationFrame(() => positionCodexPopover(triggerElement, true));
    };
    if (els.codexFooter) {
      els.codexFooter.hidden = false;
      els.codexFooter.replaceChildren(toggleBtn);
    }
  } else if (els.codexFooter) {
    els.codexFooter.hidden = true;
    els.codexFooter.replaceChildren();
  }

  presentCodexDialog(triggerElement);
}

function renderDrawer() {
  const campaign = state.playerState?.campaign || {};
  const player = campaign.player || {};

  // 1. 时空概览药丸条 (真实字段：turn_index, location, world_time)
  if (els.codexMetaBar) {
    els.codexMetaBar.replaceChildren();
    const metaRows = [];
    if (campaign.turn_index != null) {
      metaRows.push([`第 ${campaign.turn_index} 回合`, "turn"]);
    }
    if (campaign.location) {
      metaRows.push([campaign.location, "loc"]);
    }
    if (campaign.world_time) {
      metaRows.push([campaign.world_time, "time"]);
    }
    if (metaRows.length > 0) {
      els.codexMetaBar.hidden = false;
      const bar = node("div", "codex-meta-bar-inner");
      for (const [text, kind] of metaRows) {
        const pill = node("span", `codex-meta-pill codex-meta-pill-${kind}`, text);
        bar.appendChild(pill);
      }
      els.codexMetaBar.appendChild(bar);
    } else {
      els.codexMetaBar.hidden = true;
    }
  }

  // 2. 主角档案
  els.playerSummary.replaceChildren();
  const playerCard = node("div", "codex-player-card-inner");
  const playerHead = node("div", "codex-player-head");
  const nameEl = node("span", "codex-player-name", player.name || "尚未命名");
  const badge = node("span", "codex-player-badge", "主角");
  playerHead.append(nameEl, badge);
  playerCard.appendChild(playerHead);

  if (player.visible_status) {
    const statusRow = node("div", "codex-player-status-row");
    statusRow.append(
      node("span", "codex-player-status-label", "外观状态："),
      node("span", "codex-player-status-text", player.visible_status),
    );
    playerCard.appendChild(statusRow);
  }
  els.playerSummary.appendChild(playerCard);

  // 3. 在场角色 (点击联动 NPC 档案)
  const participants = campaign.participants || [];
  els.sceneSummary.replaceChildren();
  const list = node("div", "scene-summary-items");
  if (participants.length) {
    participants.forEach((name) => {
      const isPlayer = name === player.name;
      const chip = node("button", `tag codex-participant-chip ${isPlayer ? "is-player" : ""}`, name);
      chip.type = "button";
      if (isPlayer) {
        chip.title = `${name} (主角)`;
      } else {
        const npcList = state.records?.npc_profiles || [];
        const nameTrimmed = String(name || "").trim().toLowerCase();
        const match = npcList.find((n) => {
          const d = n.public || n.fields?.public || n.fields || n || {};
          const nName = String(d.name || "").trim().toLowerCase();
          if (nName && (nName === nameTrimmed || nameTrimmed.includes(nName) || nName.includes(nameTrimmed))) {
            return true;
          }
          if (Array.isArray(d.aliases)) {
            return d.aliases.some((al) => {
              const alTrim = String(al || "").trim().toLowerCase();
              return alTrim && (alTrim === nameTrimmed || nameTrimmed.includes(alTrim) || alTrim.includes(nameTrimmed));
            });
          }
          return false;
        });
        if (match) {
          const profileData = {
            ...(match.public || match.fields?.public || match.fields || match),
            id: match.id,
            ...(state.experienceMode === "creator" ? match.director_only : {}),
          };
          chip.title = `点击查看 ${name} 的人物档案`;
          chip.onclick = (e) => {
            e.stopPropagation();
            openCodexProfile(profileData, chip);
          };
        }
      }
      list.appendChild(chip);
    });
  } else {
    list.appendChild(node("span", "resource-empty", "暂无角色在场"));
  }
  els.sceneSummary.appendChild(list);

  // 4. 资产与物品
  const resources = campaign.resources || {};
  const money = resources.money == null ? "无" : `${resources.money}${resources.money_unit ? ` ${resources.money_unit}` : ""}`;
  const moneyCard = node("div", "resource-money");
  moneyCard.append(node("span", "resource-label", "可用资金"), node("strong", "", money));

  const resourceGroup = (label, items, kind) => {
    const group = node("section", `resource-group resource-group-${kind}`);
    const header = node("div", "resource-group-header");
    header.append(
      node("span", "resource-label", label),
      node("span", "resource-count", String(items.length)),
    );
    const groupList = node("div", "resource-items");
    if (!items.length) {
      groupList.appendChild(node("div", "resource-empty", "暂无"));
    } else if (kind === "inventory") {
      items.forEach((item) => {
        const row = node("div", "inventory-item");
        const itemName = node("span", "inventory-name", item.name);
        const quantity = node("span", "inventory-quantity", `×${item.quantity ?? 1}`);
        row.append(itemName, quantity);
        groupList.appendChild(row);
      });
    } else {
      items.forEach((item) => groupList.appendChild(node("div", "asset-item", item)));
    }
    group.append(header, groupList);
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
  renderCanonValidation();
  enhanceCustomSelect(els.difficultySelect);
  enhanceCustomSelect(els.diceDisplaySelect);
  enhanceCustomSelect(els.fontSizeSelect);
  enhanceCustomSelect(els.fontFamilySelect);
  enhanceCustomSelect(els.themeSelect);
  enhanceCustomSelect(els.initWorldStyle);
  enhanceCustomSelect(els.initAbilitySystem);
  enhanceCustomSelect(els.initForm.elements.protagonist_gender);
  enhanceCustomSelect(els.initForm.elements.failure_severity);
  enhanceCustomSelect(els.setupPowerPrevalence);
  enhanceCustomSelect(els.setupPowerLearningDifficulty);
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
    const deleteButton = saveActionButton("删除", "danger-button", async () => {
      const confirmed = await showConfirmModal({
        title: "确认删除存档",
        message: `确定要永久删除“${save.name}”吗？此操作不可撤销。`,
        confirmText: "永久删除",
        isDanger: true,
      });
      if (!confirmed) return;
      const replacements = state.saves.filter((item) => item.save_id !== save.save_id);
      const replacementId = replacements[0]?.save_id || null;
      const body = { save_id: save.save_id, confirmation_name: save.name };
      if (replacementId) body.replacement_save_id = replacementId;
      runSaveManagerAction(
        () => request("/api/player/saves/delete", { method: "POST", body: JSON.stringify(body) }),
        "存档已删除",
      );
    });
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
  textarea.style.height = "0px";
  const scrollHeight = textarea.scrollHeight;
  const computed = window.getComputedStyle(textarea);
  const minHeight = parseFloat(computed.minHeight) || 0;
  const maxHeight = parseFloat(computed.maxHeight) || Infinity;
  const lineHeight = parseFloat(computed.lineHeight) || 20;
  const verticalPadding = (parseFloat(computed.paddingTop) || 0) + (parseFloat(computed.paddingBottom) || 0);
  const floor = Math.max(minHeight, lineHeight + verticalPadding);
  const targetHeight = Math.min(Math.max(scrollHeight, floor), maxHeight);
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
  applyUiMode();
  renderHeader(); renderHistory(); renderPending(); renderDrawer(); renderSaves(); renderSuggestions(); renderControls();
  cachePlayerShell();
}

function cachePlayerShell() {
  if (!state.playerState) return;
  try {
    localStorage.removeItem(PLAYER_SHELL_CACHE_KEY);
    const serialized = JSON.stringify({
      version: 1,
      cachedAt: Date.now(),
      activeSave: state.activeSave,
      saves: state.saves,
      playerState: state.playerState,
    });
    sessionStorage.setItem(PLAYER_SHELL_CACHE_KEY, serialized);
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
    localStorage.removeItem(PLAYER_SHELL_CACHE_KEY);
    const raw = sessionStorage.getItem(PLAYER_SHELL_CACHE_KEY);
    const cached = JSON.parse(raw || "null");
    if (cached?.version !== 1 || !cached.playerState) return false;
    state.activeSave = cached.activeSave || null;
    state.saves = cached.saves || [];
    state.playerState = cached.playerState;
    state.experienceMode = cached.playerState.experience_mode || "player";
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
    renderRecordsRail();
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
  if (
    !state.playerState
    && resumeOperation
    && !activeGenerationRun
    && !["booting", "generating", "confirming", "switching"].includes(state.mode)
  ) {
    setMode("booting");
    renderControls();
  }
  renderSaves();

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
  state.experienceMode = playerState.experience_mode || "player";
  if (playerState.needs_init) await loadInitializationDraft();
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
  if (state.activeSave) refreshRecords({ force: true });
  if (recoverableOperations.length) {
    let reloadAfterRecovery = false;
    let autoConfirmAfterRecovery = false;
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
        const resumed = await resumeGenerationOperation(operation);
        reloadAfterRecovery = resumed || reloadAfterRecovery;
        if (resumed && operation.operation_type === "generation") {
          autoConfirmAfterRecovery = true;
        }
        if (!reloadAfterRecovery) {
          const pendingAfterResume = activePending();
          if (pendingAfterResume) {
            const latest = pendingAfterResume.variants[pendingAfterResume.variants.length - 1];
            if (latest) tokenUsage.saveVariantGenerationUsage(latest.turn_id);
          }
        }
      }
    }
    if (reloadAfterRecovery) {
      await loadAll({ scroll: scroll || "smooth", resumeOperation: false });
    }
    if (autoConfirmAfterRecovery) {
      await maybeAutoConfirm();
    }
    return;
  }
  if (playerState.needs_init && !els.initDialog.open) els.initDialog.showModal();
  if (scroll) scrollToBottom(scroll, () => { state.historyPagingEnabled = true; });
  else window.requestAnimationFrame(() => { state.historyPagingEnabled = true; });
}

async function bootstrap() {
  initAutoExpandingTextareas();
  applyUiMode();
  restoreRecordsTabPreference();
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

async function handleInitializationEvent(event) {
  tokenUsage.handleEvent(event);
  setProgress(event.step);
  if (event.step === "campaign_setup_done") {
    showCampaignSetupStage(event.data || {});
    try {
      await persistInitializationDraft();
    } catch (error) {
      reportClientError(error, "initialization_draft_save_failed");
      showToast("公开世界惯例已生成，但初始化草稿暂未保存");
    }
  }
}

async function resumeInitializationOperation(operation) {
  const campaignSetup = operation.metadata?.stage === "campaign_setup";
  setMode("generating");
  renderControls();
  if (!els.initDialog.open) els.initDialog.showModal();
  const button = campaignSetup ? els.campaignSetupBtn : els.initSubmitBtn;
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = campaignSetup ? "正在生成……" : "正在生成开场……";
  try {
    await subscribe(
      `/api/player/operations/${encodeURIComponent(operation.operation_id)}/events?after=0`,
      handleInitializationEvent,
    );
    await acknowledgeHandledOperation(operation.operation_id);
    if (campaignSetup) {
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

function startStreaming(label = "正在书写剧情正文") {
  streamBuffer = "";
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
}

function appendStreamingText(delta) {
  if (!delta) return;
  streamBuffer += delta;
  if (streamFrame) return;
  streamFrame = window.requestAnimationFrame(() => {
    if (streamBuffer) els.streamingText.appendChild(document.createTextNode(streamBuffer));
    streamBuffer = "";
    streamFrame = 0;
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
  tokenUsage.handleEvent(event);
  updateGenerationRunFromEvent(event);
  if (event.step === "operation_events_truncated") {
    els.streamingLabel.textContent = "正在恢复最新生成进度";
  }
  setProgress(event.step);
  if (event.step === "action_parsed") {
    const parsed = event.data?.action_parse_result;
    const firstAction = parsed?.atomic_actions?.[0];
    if (firstAction) {
      const desc = [firstAction.verb, firstAction.target].filter(Boolean).join(" ");
      if (desc) {
        els.streamingLabel.textContent = `正在解析：尝试「${desc}」`;
      }
    }
  }
  if (event.step === "context_facts_done") {
    els.streamingLabel.textContent = "前情已检索 · 正在推演因果";
  }
  if (event.step === "rolls_done") {
    const rolls = event.data?.roll_results;
    if (Array.isArray(rolls) && rolls.length > 0) {
      const firstRoll = rolls[0];
      const dice = firstRoll.dice || "检定";
      const val = firstRoll.value ?? firstRoll.raw_value;
      const sides = firstRoll.dice_sides ? `/${firstRoll.dice_sides}` : "";
      const valStr = val != null ? ` ➜ ${val}${sides}` : "";
      els.streamingLabel.textContent = `检定已掷出：${dice}${valStr} · 正在结算`;
    }
  }
  if (event.step === "turn_resolution_done") {
    const tr = event.data?.turn_resolution;
    const firstResolution = tr?.action_resolutions?.[0];
    const band = firstResolution?.outcome_band;
    const bandMap = {
      critical_success: "大成功",
      success: "成功",
      partial_success: "部分成功",
      failure: "失败",
      critical_failure: "大失败",
    };
    const bandText = bandMap[band] || band;
    if (bandText) {
      els.streamingLabel.textContent = `机制判定达成：${bandText} · 正在构思正文`;
    } else {
      els.streamingLabel.textContent = "机制判定达成 · 正在构思正文";
    }
  }
  if (event.step === "feedback_rewrite_strategy_selected") {
    const strategy = event.data?.strategy;
    if (strategy === "revise_from_source") els.streamingLabel.textContent = "正在基于原文修订正文";
    if (strategy === "recompose") els.streamingLabel.textContent = "正在重新构思剧情正文";
  }
  if (event.step === "scene_stream_started") startStreaming(
    event.data?.rewrite_mode === "recompose"
      ? "正在重新构思剧情正文"
      : retryMode === "feedback_rewrite"
        ? "正在基于原文修订正文"
      : event.data?.revision
      ? "发现逻辑偏差，正在自动修订正文"
      : retryMode === "rephrase"
        ? "正在重新措辞正文"
        : retryMode === "reroll"
          ? "正在重新判定并重写正文"
        : "正在书写剧情正文",
  );
  if (event.step === "scene_text_delta") {
    appendStreamingText(event.data?.delta || "");
  }
  if (event.step === "composer_empty") {
    state.composerEmpty = true;
  }
}

async function resumeGenerationOperation(operation) {
  const retryMode = ["rephrase", "reroll", "feedback_rewrite", "opening_rewrite"].includes(operation.operation_type)
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
        : retryMode === "opening_rewrite"
          ? "正在恢复开场重写"
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
    if (state.composerEmpty) {
      state.composerEmpty = false;
      state.composerEmptyUserInput = userInput;
      await loadAll({ scroll: "smooth" });
      showComposerEmptyMessage();
      return true;
    }
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
    showError(error, undefined, { pendingPreserved: Boolean(retryMode) });
    await acknowledgeFailedOperation(error, operation.operation_id);
    return false;
  } finally {
    finishGenerationRun();
  }
}

function handleConfirmationEvent(event) {
  tokenUsage.handleEvent(event);
  setProgress(event.step);
  if (event.step === "action_preview_done" || event.step === "finish") {
    const previews = event.data?.action_preview?.previews;
    if (previews) state.suggestions = previews;
  }
  if (event.step === "finish") {
    const warnings = event.data?.state_update_warnings || [];
    const fallback = warnings.find(
      (warning) => warning && warning.code === "state_update_fallback",
    );
    if (fallback) {
      showToast("本轮世界状态更新未能生效，其余内容照常写入。");
    }
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
    refreshRecordsAfterTurn();
    return true;
  } catch (error) {
    sessionStorage.removeItem(CONFIRMATION_IN_FLIGHT_KEY);
    setMode("pending");
    renderAll();
    showError(error, undefined, { pendingPreserved: true });
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
  startStreaming("正在分析修改意见");
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
    if (decision?.strategy !== "no_rewrite") {
      const pendingAfterRewrite = activePending();
      if (pendingAfterRewrite) {
        const latest = pendingAfterRewrite.variants[pendingAfterRewrite.variants.length - 1];
        if (latest) tokenUsage.saveVariantGenerationUsage(latest.turn_id);
      }
      showToast("新版本已生成，旧版本仍然保留");
    } else {
      showToast(feedbackNoRewriteMessage(decision.reason_code));
    }
  } catch (error) {
    stopStreaming(); setMode("pending"); renderAll(); showError(error, undefined, { pendingPreserved: true });
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
  state.composerEmptyUserInput = userInput;
  showActivePlayerAction(userInput);
  els.playerInput.value = ""; autoSizeInput(); renderControls();
  startStreaming("正在解析行动意图");
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
    if (state.composerEmpty) {
      state.composerEmpty = false;
      await loadAll({ scroll: "smooth" });
      showComposerEmptyMessage();
      return;
    }
    state.composerEmptyUserInput = null;
    await loadAll({ scroll: "smooth" });
    const pendingAfterGen = activePending();
    if (pendingAfterGen) {
      const latest = pendingAfterGen.variants[pendingAfterGen.variants.length - 1];
      if (latest) tokenUsage.saveVariantGenerationUsage(latest.turn_id);
    }
    await maybeAutoConfirm();
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
  startStreaming(rephrase ? "正在重新措辞正文" : "正在重新判定并重写正文");
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
    stopStreaming();
    if (state.composerEmpty) {
      state.composerEmpty = false;
      await loadAll({ scroll: "smooth" });
      showComposerEmptyMessage();
      return;
    }
    await loadAll({ scroll: "smooth" });
    const pendingAfterRetry = activePending();
    if (pendingAfterRetry) {
      const latest = pendingAfterRetry.variants[pendingAfterRetry.variants.length - 1];
      if (latest) tokenUsage.saveVariantGenerationUsage(latest.turn_id);
    }
    showToast("新版本已生成，旧版本仍然保留");
  } catch (error) {
    stopStreaming(); setMode("pending"); renderAll(); showError(error, undefined, { pendingPreserved: true });
    await acknowledgeFailedOperation(error);
  } finally {
    finishGenerationRun();
  }
}

async function retryOpening() {
  const pending = activePending(); const variant = selectedVariant();
  if (!pending || !variant || pending.kind !== "opening") return;
  if (
    pending.variants.length >= 5
    && !await showConfirmModal({
      title: "继续生成开场版本？",
      message: "当前已经保留 5 个或更多开场版本。继续会产生一次新的模型调用。",
      confirmText: "继续生成",
    })
  ) return;
  prepareGenerationRun("opening_rewrite", "");
  setMode("generating"); renderControls();
  startStreaming("正在重新生成开场");
  try {
    const operationId = await stream("/api/player/opening/rewrite", {
      turn_id: variant.turn_id,
      request_id: newRequestId(),
    }, (event) => handleGenerationEvent(event, { retryMode: "opening_rewrite" }));
    await acknowledgeHandledOperation(operationId);
    if (activeGenerationRun?.cancelled) {
      settleCancelledGeneration({ retryMode: "opening_rewrite" });
      return;
    }
    stopStreaming();
    await loadAll({ scroll: "smooth" });
    const pendingAfterRetry = activePending();
    if (pendingAfterRetry) {
      const latest = pendingAfterRetry.variants[pendingAfterRetry.variants.length - 1];
      if (latest) tokenUsage.saveVariantGenerationUsage(latest.turn_id);
    }
    showToast("新开场已生成，原版本仍然保留");
  } catch (error) {
    stopStreaming(); setMode("pending"); renderAll(); showError(error, undefined, { pendingPreserved: true });
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
  if (reasonCode === "strategy_unavailable") {
    return "暂时无法判断修改方式，原文已保留，可重试";
  }
  return "这项意见不适用于正文重写，原版本保持不变";
}

async function maybeAutoConfirm() {
  const preferences = state.readerPreferences || {};
  if (!shouldAutoConfirmTurn({
    enabled: preferences.one_click_confirm,
    pendingKind: activePending()?.kind,
  })) return;
  if (sessionStorage.getItem(CONFIRMATION_IN_FLIGHT_KEY) === "1") return;
  const variant = selectedVariant();
  if (variant && !readDraft(variant).trim()) return;
  await confirmTurn();
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
    const storedUsage = tokenUsage.getVariantUsage(variant.turn_id);
    const genUsage = storedUsage?.generation || tokenUsage.getCurrentPendingUsage().generation;
    const confUsage = tokenUsage.getCurrentPendingUsage().confirmation;
    tokenUsage.saveFullVariantUsage(variant.turn_id, {
      generation: genUsage,
      confirmation: confUsage,
    });
    tokenUsage.cleanUpUnconfirmedVariants(
      variant.turn_id,
      pending.variants.map((v) => v.turn_id),
    );
    await loadAll({ scroll: "smooth" });
    tokenUsage.clearPendingUsage();
    showToast("这一回合已经写入旅程");
    refreshRecordsAfterTurn();
  } catch (error) {
    setMode("pending"); renderAll(); showError(error, undefined, { pendingPreserved: true });
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
  } catch (error) { showError(error, undefined, { pendingPreserved: true }); }
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
  const rows = [...els.abilityRows.querySelectorAll(".ability-row")];
  const protagonistAbilities = rows.map((row, index) => {
    const name = row.querySelector(".ability-name").value.trim();
    const description = row.querySelector(".ability-description").value.trim();
    if (Boolean(name) !== Boolean(description)) {
      throw new Error(`能力 ${index + 1} 的名称和解释需要同时填写`);
    }
    return name ? { name, description } : null;
  }).filter(Boolean);
  const age = formValue(form, "protagonist_age");
  const abilitySystem = formValue(form, "ability_system") || null;
  const worldStyleValue = formValue(form, "world_style");
  const customWorldStyle = formValue(form, "custom_world_style");
  if (worldStyleValue === "自定义" && !customWorldStyle) {
    throw new Error("自定义世界风格不能为空");
  }
  const world_style = worldStyleValue === "自定义"
    ? customWorldStyle
    : worldStyleValue;
  return {
    version: 4,
    world_name: formValue(form, "world_name") || null,
    world_style,
    ability_system: abilitySystem,
    custom_ability_system: abilitySystem === "自定义" ? formValue(form, "custom_ability_system") || null : null,
    protagonist_gender: formValue(form, "protagonist_gender") || "男",
    protagonist_name: formValue(form, "protagonist_name"),
    protagonist_age: age ? Number(age) : null,
    protagonist_introduction: formValue(form, "protagonist_introduction") || null,
    protagonist_abilities: protagonistAbilities,
    story_expectation: formValue(form, "story_expectation"),
    failure_severity: formValue(form, "failure_severity") || "适中",
  };
}

function captureInitializationForm() {
  const fields = {};
  for (const control of els.initForm.querySelectorAll("[name]")) {
    fields[control.name] = control.value;
  }
  const rows = [...els.abilityRows.querySelectorAll(".ability-row")];
  return {
    fields,
    protagonist_abilities: rows.map((row) => ({
      name: row.querySelector(".ability-name").value,
      description: row.querySelector(".ability-description").value,
    })).filter((a) => a.name.trim()),
  };
}

function restoreInitializationForm(form = {}) {
  els.initForm.reset();
  for (const [name, value] of Object.entries(form.fields || {})) {
    const control = els.initForm.elements[name];
    if (control) control.value = value ?? "";
  }
  const savedWorldStyle = (form.fields && form.fields.world_style) || "";
  const worldStyleOptions = [...els.initWorldStyle.options].map((o) => o.value);
  if (savedWorldStyle && !worldStyleOptions.includes(savedWorldStyle)) {
    els.initWorldStyle.value = "自定义";
    const customTextarea = els.initForm.elements.custom_world_style;
    if (customTextarea) customTextarea.value = savedWorldStyle;
  }
  const abilities = (form.protagonist_abilities || []).filter(
    (a) => a && a.name && a.name.trim(),
  );
  els.abilityRows.replaceChildren();
  for (const ability of abilities) {
    addAbilityRow(ability.name, ability.description);
  }
  toggleCustomAbilityField();
  toggleCustomWorldStyleField();
}

function readCampaignSetup() {
  const currencyUnit = els.setupCurrencyUnit.value.trim();
  const powerSource = els.setupPowerSource.value.trim();
  const limitation = els.setupPowerLimitation.value.trim();
  const peoplesAndBeings = readCampaignSetupLines(
    els.setupPeoplesAndBeings,
    "主要存在主体",
    { minItems: 1, maxItems: 6, maxChars: 10 },
  );
  const majorWorldConflicts = readCampaignSetupLines(
    els.setupMajorWorldConflicts,
    "世界主要冲突",
    { minItems: 1, maxItems: 2, maxChars: 50 },
  );
  const dominantPowers = readCampaignSetupLines(
    els.setupDominantPowers,
    "主导势力",
    { minItems: 1, maxItems: 3, maxChars: 10 },
  );
  const civilizationBaseline = els.setupCivilizationBaseline.value.trim();
  if (!currencyUnit) throw new Error("默认货币单位不能为空");
  if (!powerSource) throw new Error("力量来源不能为空");
  if (!limitation) throw new Error("力量限制不能为空");
  if (!civilizationBaseline) throw new Error("文明与技术基准不能为空");
  return {
    version: 2,
    currency_unit: currencyUnit,
    public_power_baseline: {
      source: powerSource,
      prevalence: els.setupPowerPrevalence.value,
      learning_difficulty: els.setupPowerLearningDifficulty.value,
      limitation,
    },
    peoples_and_beings: peoplesAndBeings,
    major_world_conflicts: majorWorldConflicts,
    dominant_powers: dominantPowers,
    civilization_baseline: civilizationBaseline,
  };
}

function readCampaignSetupLines(control, label, {
  minItems,
  maxItems,
  maxChars,
}) {
  const items = control.value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
  if (items.length < minItems || items.length > maxItems) {
    throw new Error(`${label}需要填写 ${minItems} 至 ${maxItems} 项`);
  }
  const tooLong = items.find((item) => [...item].length > maxChars);
  if (tooLong) throw new Error(`${label}每项不能超过 ${maxChars} 个字`);
  return items;
}

function initializationDraftPayload() {
  return {
    version: 4,
    stage: initConfigSnapshot ? "campaign_setup" : "config",
    form: captureInitializationForm(),
    config: initConfigSnapshot,
    campaign_setup: initConfigSnapshot ? readCampaignSetup() : null,
  };
}

async function persistInitializationDraft() {
  if (!state.playerState?.needs_init) return;
  if (state.playerState?.initialization?.phase !== "config") return;
  await request("/api/player/init/draft", {
    method: "POST",
    body: JSON.stringify(initializationDraftPayload()),
  });
}

async function loadInitializationDraft() {
  const committed = state.playerState?.initialization;
  if (
    committed?.phase
    && committed.phase !== "config"
    && committed.config
    && committed.campaign_setup
  ) {
    restoreInitializationForm();
    showCampaignSetupStage({
      config: committed.config,
      campaign_setup: committed.campaign_setup,
    }, { committed: true });
    return;
  }
  const result = await request("/api/player/init/draft");
  const draft = result.draft;
  if (!draft) {
    restoreInitializationForm();
    editInitConfig();
    return;
  }
  restoreInitializationForm(draft.form);
  if (
    draft.stage === "campaign_setup"
    && draft.config
    && draft.campaign_setup?.version === 2
    && draft.campaign_setup?.currency_unit
    && draft.campaign_setup?.public_power_baseline
    && draft.campaign_setup?.peoples_and_beings?.length
    && draft.campaign_setup?.major_world_conflicts?.length
    && draft.campaign_setup?.dominant_powers?.length
    && draft.campaign_setup?.civilization_baseline
  ) {
    showCampaignSetupStage({
      config: draft.config,
      campaign_setup: draft.campaign_setup,
    });
  } else {
    editInitConfig();
  }
}

async function closeInitializationDialog() {
  try {
    await persistInitializationDraft();
  } catch (error) {
    reportClientError(error, "initialization_draft_save_failed");
    showToast("初始化草稿暂未保存");
  } finally {
    els.initDialog.close();
  }
}

function toggleCustomAbilityField() {
  const visible = els.initAbilitySystem.value === "自定义";
  els.initCustomAbilityWrap.hidden = !visible;
  els.initForm.elements.custom_ability_system.disabled = !visible;
  els.initForm.elements.custom_ability_system.required = visible;
}

function toggleCustomWorldStyleField() {
  const visible = els.initWorldStyle.value === "自定义";
  els.initCustomWorldStyleWrap.hidden = !visible;
  els.initForm.elements.custom_world_style.disabled = !visible;
  els.initForm.elements.custom_world_style.required = visible;
}

function addAbilityRow(name = "", description = "") {
  const count = els.abilityRows.children.length;
  if (count >= 3) return;
  const index = count + 1;
  const row = node("div", "ability-row");
  const nameInput = node("input", "ability-name");
  nameInput.placeholder = `能力 ${index} 名称`;
  nameInput.value = name;
  const descInput = node("input", "ability-description");
  descInput.placeholder = `能力 ${index} 的效果、边界或代价`;
  descInput.value = description;
  const removeBtn = node("button", "ability-remove-btn", "×");
  removeBtn.type = "button";
  removeBtn.onclick = () => { row.remove(); updateAddAbilityButton(); };
  row.append(nameInput, descInput, removeBtn);
  els.abilityRows.appendChild(row);
  updateAddAbilityButton();
}

function updateAddAbilityButton() {
  els.addAbilityBtn.hidden = els.abilityRows.children.length >= 3;
}

function setCampaignSetupControlsDisabled(disabled) {
  for (const control of els.campaignSetupStage.querySelectorAll(
    "input, textarea, select",
  )) {
    control.disabled = disabled;
  }
  syncCustomSelects([
    els.setupPowerPrevalence,
    els.setupPowerLearningDifficulty,
  ]);
}

function showCampaignSetupStage(result, { committed = false } = {}) {
  initConfigSnapshot = result.config;
  campaignSetupCommitted = committed;
  const proposal = result.campaign_setup || {};
  const baseline = proposal.public_power_baseline || {};
  els.setupCurrencyUnit.value = proposal.currency_unit || "";
  els.setupPowerSource.value = baseline.source || "";
  els.setupPowerPrevalence.value = baseline.prevalence || "少数人掌握";
  els.setupPowerLearningDifficulty.value = baseline.learning_difficulty || "适中";
  els.setupPowerLimitation.value = baseline.limitation || "";
  els.setupPeoplesAndBeings.value = (proposal.peoples_and_beings || []).join("\n");
  els.setupMajorWorldConflicts.value = (proposal.major_world_conflicts || []).join("\n");
  els.setupDominantPowers.value = (proposal.dominant_powers || []).join("\n");
  els.setupCivilizationBaseline.value = proposal.civilization_baseline || "";
  els.initConfigFields.disabled = true;
  els.initConfigActions.hidden = true;
  els.campaignSetupStage.hidden = false;
  els.campaignSetupActions.hidden = false;
  setCampaignSetupControlsDisabled(committed);
  els.editInitConfigBtn.hidden = committed;
  els.regenerateCampaignSetupBtn.hidden = committed;
  els.initSubmitBtn.textContent = committed
    ? "继续生成开场"
    : "确认惯例并生成开场";
  els.initStageHint.textContent = committed
    ? "公开世界惯例和 CoreMemory 已确认。继续生成私有开局基础与开场正文。"
    : "检查并修改公开世界惯例；提交后将正式建立 CoreMemory，不能返回修改。";
  els.setupCurrencyUnit.focus();
}

function editInitConfig() {
  initConfigSnapshot = null;
  campaignSetupCommitted = false;
  els.initConfigFields.disabled = false;
  els.initConfigActions.hidden = false;
  els.campaignSetupStage.hidden = true;
  els.campaignSetupActions.hidden = true;
  setCampaignSetupControlsDisabled(true);
  els.editInitConfigBtn.hidden = false;
  els.regenerateCampaignSetupBtn.hidden = false;
  els.initSubmitBtn.textContent = "确认惯例并生成开场";
  els.initStageHint.textContent = "填写世界、主角与长期体验要求，然后生成可确认的公开世界惯例。";
  els.initError.hidden = true;
}

async function requestCampaignSetup({ regenerate = false } = {}) {
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
  const button = regenerate ? els.regenerateCampaignSetupBtn : els.campaignSetupBtn;
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = "正在生成……";
  els.initConfigFields.disabled = true;
  try {
    if (!regenerate) await persistInitializationDraft();
    const operationId = await stream("/api/player/init/campaign-setup", {
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
    if (!initConfigSnapshot) els.initConfigFields.disabled = false;
  }
}

async function initialize() {
  if (!initConfigSnapshot) {
    els.initError.textContent = "请先生成并确认公开世界惯例";
    els.initError.hidden = false;
    return;
  }
  let campaignSetup;
  try {
    campaignSetup = campaignSetupCommitted
      ? state.playerState.initialization.campaign_setup
      : readCampaignSetup();
  } catch (error) {
    els.initError.textContent = error.message;
    els.initError.hidden = false;
    return;
  }
  const payload = { config: initConfigSnapshot, campaign_setup: campaignSetup };
  setMode("generating"); els.initSubmitBtn.disabled = true; els.initSubmitBtn.textContent = "正在生成开场……"; els.initError.hidden = true;
  try {
    if (!campaignSetupCommitted) await persistInitializationDraft();
    const operationId = await stream("/api/player/init", {
      ...payload,
      request_id: newRequestId(),
    }, handleInitializationEvent);
    await acknowledgeHandledOperation(operationId);
    els.initDialog.close();
    await loadAll({ scroll: "instant" });
  }
  catch (error) {
    await acknowledgeFailedOperation(error);
    try {
      await loadAll({ scroll: null, resumeOperation: false });
    } catch (reloadError) {
      reportClientError(reloadError, "initialization_reload_failed");
    }
    setMode("needs_init"); els.initError.textContent = error.message; els.initError.hidden = false;
  }
  finally {
    els.initSubmitBtn.disabled = false;
    if (!campaignSetupCommitted) {
      els.initSubmitBtn.textContent = "确认惯例并生成开场";
    }
    renderControls();
  }
}

async function openLeftDrawer() {
  if (els.leftDrawer) {
    const closed = await closeRecordsDrawer();
    if (!closed) return;
    renderDrawer();
    els.leftDrawer.classList.add("open");
    els.leftDrawer.setAttribute("aria-hidden", "false");
  }
}
function closeLeftDrawer() {
  if (els.leftDrawer) {
    els.leftDrawer.classList.remove("open");
    els.leftDrawer.setAttribute("aria-hidden", "true");
  }
}

/* ===== World Archive (records) drawer ===== */

const RECORDS_POLL_INTERVAL = 2000;
let recordsPollTimer = null;

const CONTINUITY_EDITABLE_FIELDS = [
  ["subject", "主题", false],
  ["entities", "关联实体", true],
  ["location", "地点", false],
];
const CONTINUITY_VIEW_FIELDS = [
  ["premise", "原始事项", false],
  ["current_state_text", "当前状态", false],
  ...CONTINUITY_EDITABLE_FIELDS,
];

const NPC_PUBLIC_FIELDS = [
  ["name", "姓名", false],
  ["aliases", "别名", true],
  ["role", "身份", false],
  ["appearance", "外貌", false],
  ["personality_trait", "性格倾向", false],
  ["known_abilities", "已知能力", true],
  ["faction", "阵营", false],
  ["last_known_location", "最后出场地点", false],
  ["attitude_to_player", "对玩家态度", false],
];

const NPC_ADVANCED_DIRECTOR_FIELDS = [
  ["core_motivation", "核心动机", false],
  ["hidden_secret", "隐藏秘密", false],
  ["conflict_style", "冲突风格", false],
  ["social_style", "社交风格", false],
  ["director_notes", "导演备注", false],
];

// 显示/编辑字段显式拆分：NPC_VIEW_FIELDS 是程序驱动的只读展示字段
// （last-scene pair 永不可编辑），NPC_EDITABLE_FIELDS 保持现有可编辑字段。
// 显示格式化器单独放 NPC_VIEW_FORMATTERS——不复用字段元组第 4 位
// （该位在 Setting 元组中是 "textarea" 字符串约定）。
const NPC_VIEW_FIELDS = [
  ["last_scene_summary", "最近出场", false],
  ["last_scene_turn", "出场回合", false],
];
const NPC_VIEW_FORMATTERS = {
  // 回合数用显式 null 判断：turn=0（开场回合）也必须显示「第 0 回合」。
  last_scene_turn: (raw) => (raw == null ? null : `第 ${raw} 回合`),
};
const NPC_EDITABLE_FIELDS = [
  ...NPC_PUBLIC_FIELDS,
  ...NPC_ADVANCED_DIRECTOR_FIELDS,
];

const SETTING_FIELDS = [
  ["name", "名称", false],
  ["summary", "摘要", false, "textarea"],
  ["public_facts", "公开事实", true, "textarea"],
  ["hidden_facts", "导演事实", true, "textarea"],
];

const RECORDS_TAB_STORAGE_KEY = "loreloop:records-tab";
let recordsActiveTab = "continuity";

function restoreRecordsTabPreference() {
  try {
    const stored = window.localStorage.getItem(RECORDS_TAB_STORAGE_KEY);
    if (["continuity", "npc", "setting", "event"].includes(stored)) recordsActiveTab = stored;
  } catch (_error) {
    // Fall back to the continuity tab.
  }
  renderRecordsRail();
}

function openRecordsTab(tab) {
  if (!els.recordsDrawer) return;
  if (els.recordsDrawer.classList.contains("open") && recordsActiveTab === tab) {
    closeRecordsDrawer();
    return;
  }
  if (recordsDirty || recomputeRecordsDirty()) {
    showConfirmModal({
      title: "切换档案分类",
      message: "当前有未保存的修改，切换分类会丢弃它们。确定继续吗？",
      eyebrow: "RECORDS TAB",
      confirmText: "继续切换",
      cancelText: "取消",
    }).then((proceed) => {
      if (!proceed) return;
      recordsDirty = false;
      switchRecordsTab(tab);
    });
    return;
  }
  switchRecordsTab(tab);
}

function switchRecordsTab(tab) {
  recordsActiveTab = tab;
  try {
    window.localStorage.setItem(RECORDS_TAB_STORAGE_KEY, tab);
  } catch (_error) {
    // Preference is per-session only when storage is unavailable.
  }
  renderRecordsRail();
  openRecordsDrawer();
}

function renderRecordsRail() {
  const panes = {
    continuity: els.recordsContinuityList,
    npc: els.recordsNpcList,
    setting: els.recordsSettingList,
    event: els.recordsEventList,
  };
  const counts = {
    continuity: (state.records?.continuity || []).length,
    npc: (state.records?.npc_profiles || []).length,
    setting: (state.records?.world_settings || []).length,
    event: (state.events || []).length,
  };
  const tabs = {
    continuity: els.recordsTabContinuity,
    npc: els.recordsTabNpc,
    setting: els.recordsTabSetting,
    event: els.recordsTabEvent,
  };
    const isOpen = els.recordsDrawer && els.recordsDrawer.classList.contains("open");
    for (const key of Object.keys(tabs)) {
      const btn = tabs[key];
      if (!btn) continue;
      btn.classList.toggle("is-active", isOpen && key === recordsActiveTab);
    const countEl = btn.querySelector(".rail-count");
    if (countEl) countEl.textContent = String(counts[key] ?? 0);
  }
  for (const key of Object.keys(panes)) {
    const pane = panes[key];
    if (pane) pane.classList.toggle("is-active", key === recordsActiveTab);
  }
}

function renderExperienceModeToggle() {
  const creator = state.experienceMode === "creator";
  if (els.experienceModePlayerBtn) els.experienceModePlayerBtn.classList.toggle("is-active", !creator);
  if (els.experienceModeCreatorBtn) els.experienceModeCreatorBtn.classList.toggle("is-active", creator);
}

function applyUiMode() {
  const creator = state.experienceMode === "creator";
  document.body.dataset.uiMode = creator ? "creator" : "player";
  if (els.recordsTabEvent) els.recordsTabEvent.hidden = !creator;
  if (!creator && recordsActiveTab === "event") {
    recordsActiveTab = "continuity";
    try { window.localStorage.setItem(RECORDS_TAB_STORAGE_KEY, "continuity"); } catch (_error) {}
  }
  renderExperienceModeToggle();
  renderRecordsRail();
}

async function switchExperienceMode(mode) {
  if (mode === state.experienceMode) return;
  if (!["player", "creator"].includes(mode)) return;
  if (recordsDirty || recomputeRecordsDirty()) {
    const proceed = await showConfirmModal({
      title: "切换体验模式",
      message: "当前档案有未保存的修改，切换模式会丢弃它们。确定继续吗？",
      eyebrow: "EXPERIENCE MODE",
      confirmText: "丢弃修改并切换",
      cancelText: "取消",
      isDanger: true,
    });
    if (!proceed) return;
  }
  try {
    const result = await saveExperienceMode(mode);
    state.experienceMode = result.experience_mode;
    state.events = null;
    state.records = null;
    renderRecords({ continuity: [], npc_profiles: [], world_settings: [] });
    recordsDirty = false;
    applyUiMode();
    showToast(result.experience_mode === "creator" ? "已切换到创作者模式" : "已切换到玩家模式");
    await refreshRecords({ force: true });
  } catch (error) {
    renderExperienceModeToggle();
    showError(error);
  }
}

function setRecordsRefreshState(text) {
  els.recordsRefreshState.textContent = text || "";
  els.recordsRefreshState.hidden = !text;
}

function openRecordsDrawer() {
  if (!els.recordsDrawer) return;
  const wasOpen = els.recordsDrawer.classList.contains("open");
  els.recordsDrawer.classList.add("open");
  els.recordsDrawer.setAttribute("aria-hidden", "false");
  closeLeftDrawer();
  if (!wasOpen) {
    recordsDirty = false;
    renderRecordsRail();
    refreshRecords({ force: true });
  }
}
async function closeRecordsDrawer() {
  if (!els.recordsDrawer || recordsCloseInFlight) return false;
  recordsCloseInFlight = true;
  try {
    if (els.recordsDrawer.classList.contains("open") && (recordsDirty || recomputeRecordsDirty())) {
      const proceed = await showConfirmModal({
        title: "丢弃未保存的修改？",
        message: "当前档案有未保存的修改，关闭后将丢弃它们。确定继续吗？",
        eyebrow: "RECORDS",
        confirmText: "丢弃修改",
        cancelText: "继续编辑",
        isDanger: true,
      });
      if (!proceed) return false;
      recordsDirty = false;
    }
    els.recordsDrawer.classList.remove("open");
    els.recordsDrawer.setAttribute("aria-hidden", "true");
    renderRecordsRail();
    return true;
  } finally {
    recordsCloseInFlight = false;
  }
}
function stopRecordsPoll() {
  window.clearInterval(recordsPollTimer);
  recordsPollTimer = null;
}
function startRecordsPoll() {
  stopRecordsPoll();
  recordsPollTimer = window.setInterval(() => { refreshRecords(); }, RECORDS_POLL_INTERVAL);
}

let recordsRefreshInFlight = false;
let recordsRefreshGeneration = 0;
let recordsRefreshPending = false;
let recordsRefreshPendingForce = false;
let recordsDirty = false;
let recordsCloseInFlight = false;
const recordsExpandedIds = new Set();
function collectFactValues(editor) {
  return [...editor.querySelectorAll(".record-fact-input")]
    .map((input) => input.value.trim())
    .filter(Boolean);
}
function recomputeRecordsDirty() {
  for (const list of [els.recordsContinuityList, els.recordsNpcList, els.recordsSettingList]) {
    if (!list) continue;
    for (const editor of list.querySelectorAll(".record-fact-editor")) {
      if (JSON.stringify(collectFactValues(editor)) !== String(editor.dataset.originalValue)) {
        return true;
      }
    }
    for (const editor of list.querySelectorAll(".record-ability-editor")) {
      if (snapshotAbilityValues(editor) !== String(editor.dataset.originalValue)) {
        return true;
      }
    }
    for (const control of list.querySelectorAll("input, textarea")) {
      if (control.disabled) continue;
      if (control.closest(".record-fact-editor")) continue;
      if (control.closest(".record-ability-editor")) continue;
      const original = control.dataset.originalValue ?? "";
      const current = control.value;
      if (control.dataset.isList === "1") {
        if (JSON.stringify(_splitListValue(current)) !== JSON.stringify(_splitListValue(original))) {
          return true;
        }
      } else if (current !== original) {
        return true;
      }
    }
  }
  return false;
}
async function refreshRecords({ force = false } = {}) {
  if (!els.recordsSettingList) return;
  const generation = ++recordsRefreshGeneration;
  if (recordsRefreshInFlight) {
    recordsRefreshPending = true;
    recordsRefreshPendingForce = recordsRefreshPendingForce || force;
    return;
  }
  recordsRefreshInFlight = true;
  try {
    const records = await loadRecords();
    if (generation !== recordsRefreshGeneration) {
      recordsRefreshPending = true;
      recordsRefreshPendingForce = recordsRefreshPendingForce || force;
      return;
    }
    state.records = records;
    if (force || !recordsDirty) {
      renderRecords(records);
      recordsDirty = false;
    }
    renderDrawer();
    if (state.experienceMode === "creator") {
      try {
        const events = await loadEvents();
        if (generation !== recordsRefreshGeneration) return;
        state.events = events.events || [];
        renderEventRecords(state.events);
        renderRecordsRail();
      } catch (error) {
        reportClientError(error, "events_load_failed");
      }
    }
    setRecordsRefreshState(records.maintenance_running ? "后台维护进行中 · 自动刷新" : "");
    if (records.maintenance_running) {
      startRecordsPoll();
    } else {
      stopRecordsPoll();
    }
  } catch (error) {
    if (generation !== recordsRefreshGeneration) {
      recordsRefreshPending = true;
      recordsRefreshPendingForce = recordsRefreshPendingForce || force;
      return;
    }
    setRecordsRefreshState("读取失败");
    reportClientError(error, "records_load_failed");
  } finally {
    recordsRefreshInFlight = false;
    if (recordsRefreshPending) {
      const pendingForce = recordsRefreshPendingForce;
      recordsRefreshPending = false;
      recordsRefreshPendingForce = false;
      refreshRecords({ force: pendingForce });
    }
  }
}

function refreshRecordsAfterTurn() {
  refreshRecords({ force: true });
}

function _abilityText(item) {
  if (!item || typeof item !== "object") return String(item);
  return item.description ? `${item.name}：${item.description}` : String(item.name);
}
function _formatEditListValue(value) {
  const items = Array.isArray(value) ? value : [];
  return items.join(", ");
}
function _splitListValue(value) {
  return String(value || "")
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}
function collectAbilityValues(editor) {
  return [...editor.querySelectorAll(".record-ability-row")]
    .map((rowEl) => ({
      name: (rowEl.querySelector(".record-ability-name")?.value ?? "").trim(),
      description: (rowEl.querySelector(".record-ability-desc")?.value ?? "").trim(),
    }))
    .filter((item) => item.name);
}
function snapshotAbilityValues(editor) {
  return JSON.stringify(collectAbilityValues(editor));
}

const recordsGroupExpanded = new Set();

function buildRecordsGroup({ id, title, count }) {
  const group = node("div", "records-archive-group");
  let expanded = recordsGroupExpanded.has(id);

  const header = node("div", "records-archive-header");
  header.tabIndex = 0;
  header.setAttribute("role", "button");
  header.setAttribute("aria-expanded", String(expanded));

  const headerLeft = node("div", "records-archive-header-left");
  const chevron = node("span", "records-archive-chevron", "▼");
  const titleEl = node("span", "records-archive-title", title);
  const countBadge = node("span", "records-archive-badge", String(count));
  headerLeft.append(chevron, titleEl, countBadge);
  header.appendChild(headerLeft);

  const body = node("div", "records-archive-body");
  body.hidden = !expanded;
  if (expanded) {
    group.classList.add("is-expanded");
  }

  const toggle = () => {
    expanded = !expanded;
    if (expanded) recordsGroupExpanded.add(id);
    else recordsGroupExpanded.delete(id);
    header.setAttribute("aria-expanded", String(expanded));
    group.classList.toggle("is-expanded", expanded);
    body.hidden = !expanded;
  };

  header.onclick = toggle;
  header.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };

  group.append(header, body);
  return { group, body };
}

function buildRecordCard({
  id = null,
  title,
  statusText = "",
  statusActive = false,
  metaRows = [],
  quote = null,
  pills = [],
  details = [],
  fields = [],
  editableFields = fields,
  viewFormatters = {},
  onSave,
}) {
  const card = node("div", "record-card");
  const head = node("div", "record-card-head");
  
  const titleWrap = node("div", "record-card-title-wrap");
  const chevron = node("span", "record-card-chevron", "▼");
  const titleEl = node("div", "record-card-title", title);
  titleWrap.append(chevron, titleEl);
  head.appendChild(titleWrap);

  const headActions = node("div", "record-head-actions");
  if (statusText) {
    headActions.appendChild(node("span", `codex-kind-tag${statusActive ? " is-active" : ""}`, statusText));
  }
  const editButton = node("button", "record-edit-button", "✎");
  editButton.type = "button";
  editButton.title = "编辑";
  editButton.setAttribute("aria-label", "编辑档案");
  headActions.appendChild(editButton);
  head.appendChild(headActions);
  card.appendChild(head);

  // 核心引语区（折叠与展开状态下均展示，保持内外一致）
  const quoteBox = node("div", "codex-quote-box");
  quoteBox.hidden = true;
  card.appendChild(quoteBox);

  // 可折叠的内容区（属性网格、胶囊、深度详查）
  const collapsibleBody = node("div", "record-collapsible-body");
  const editContainer = node("div", "record-edit");
  editContainer.hidden = true;
  card.appendChild(collapsibleBody);
  card.appendChild(editContainer);

  let collapsed = !(id != null && recordsExpandedIds.has(id));
  let editing = false;

  const controls = {};
  const valueSource = {};
  const factRenderers = {};

  const syncCollapse = () => {
    card.classList.toggle("is-collapsed", collapsed);
    if (editing) return;
    collapsibleBody.hidden = collapsed;
    editContainer.hidden = true;
  };

  head.onclick = (event) => {
    if (event.target.closest(".record-edit-button")) return;
    if (editing) return;
    collapsed = !collapsed;
    if (id != null) {
      if (collapsed) recordsExpandedIds.delete(id);
      else recordsExpandedIds.add(id);
    }
    syncCollapse();
  };

  const populate = (data) => {
    fields.forEach(([key]) => { valueSource[key] = data[key]; });
    editableFields.forEach(([key]) => { valueSource[key] = data[key]; });
  };

  const appendViewField = ([key, label, isList]) => {
    const row = node("div", "codex-field");
    row.appendChild(node("span", "resource-label", label));
    const raw = valueSource[key];
    if (isList) {
      const values = Array.isArray(raw) ? raw.filter((item) => String(item).trim()) : [];
      if (values.length) {
        if (RECORD_FACT_KEYS.has(key)) {
          const factList = node("ul", "codex-fact-list");
          values.forEach((item) => factList.appendChild(node("li", "", String(item))));
          row.appendChild(factList);
        } else if (key === "known_abilities") {
          const factList = node("ul", "codex-fact-list");
          values.forEach((item) => factList.appendChild(node("li", "", _abilityText(item))));
          row.appendChild(factList);
        } else {
          const pillsWrap = node("div", "codex-pills");
          values.forEach((item) => pillsWrap.appendChild(node("span", "codex-pill pill-alias", String(item))));
          row.appendChild(pillsWrap);
        }
      } else {
        row.appendChild(node("p", "", "—"));
      }
    } else {
      const format = viewFormatters[key];
      const text = raw == null || raw === ""
        ? "—"
        : (typeof format === "function" ? format(raw) : String(raw));
      row.appendChild(node("p", "", text));
    }
    collapsibleBody.appendChild(row);
  };

  const renderView = () => {
    collapsibleBody.replaceChildren();
    quoteBox.replaceChildren();
    quoteBox.hidden = true;

    // 1. 渲染 Quote Box（内外完全一致）
    if (quote) {
      const quoteText = typeof quote === "object" ? quote.text : String(quote);
      const quoteSub = typeof quote === "object" ? quote.subLabel : null;
      const turnIndex = typeof quote === "object" ? quote.turnIndex : null;

      if (quoteSub || turnIndex != null) {
        const subLabelEl = node("div", "codex-quote-sublabel");
        if (quoteSub) {
          subLabelEl.appendChild(document.createTextNode(quoteSub));
        }
        if (turnIndex != null) {
          if (quoteSub) {
            subLabelEl.appendChild(document.createTextNode(" · "));
          }
          const turnBtn = node("button", "codex-turn-jump-btn", `第 ${turnIndex} 回合`);
          turnBtn.type = "button";
          turnBtn.title = `点击跳转至第 ${turnIndex} 回合`;
          turnBtn.onclick = (e) => {
            e.stopPropagation();
            jumpToHistoryTurn(turnIndex);
          };
          subLabelEl.appendChild(turnBtn);
        }
        quoteBox.appendChild(subLabelEl);
      }

      if (quoteText) {
        quoteBox.appendChild(node("div", "codex-quote-text", quoteText));
        quoteBox.hidden = false;
      }
    }

    const hasStructuredLayout = (metaRows && metaRows.length > 0) || (pills && pills.length > 0) || (details && details.length > 0);

    if (hasStructuredLayout) {
      // 2. 属性网格
      if (metaRows && metaRows.length > 0) {
        const metaGrid = node("div", "codex-meta-grid");
        metaRows.forEach(([k, v]) => {
          if (v == null || v === "") return;
          const row = node("div", "codex-meta-row");
          row.append(
            node("span", "meta-key", k),
            node("span", "meta-val", String(v)),
          );
          metaGrid.appendChild(row);
        });
        if (metaGrid.childNodes.length > 0) {
          collapsibleBody.appendChild(metaGrid);
        }
      }

      // 3. 标签胶囊区
      if (pills && pills.length > 0) {
        const pillsWrap = node("div", "codex-pills");
        pills.forEach((p) => {
          const text = typeof p === "string" ? p : p?.text;
          const kind = typeof p === "object" ? (p?.kind || "alias") : "alias";
          if (text) {
            pillsWrap.appendChild(node("span", `codex-pill pill-${kind}`, text));
          }
        });
        if (pillsWrap.childNodes.length > 0) {
          collapsibleBody.appendChild(pillsWrap);
        }
      }

      // 4. 深度详查面板
      if (details && details.length > 0) {
        const detailsPanel = node("div", "codex-details-panel");
        details.forEach(([key, label, isList, rawVal]) => {
          if (rawVal == null || rawVal === "" || (Array.isArray(rawVal) && !rawVal.length)) return;
          const row = node("div", "codex-field");
          row.appendChild(node("span", "resource-label", label));

          if (isList === "hooks" && Array.isArray(rawVal)) {
            const list = node("div", "event-hooks-list");
            rawVal.forEach((hook) => {
              const kindLabel = EVENT_HOOK_KIND_LABELS[hook.kind] || hook.kind;
              const hookEl = node("div", `event-hook${hook.consumed ? " is-consumed" : ""}`);
              hookEl.textContent = `[${kindLabel}] ${hook.content}${hook.consumed ? "（已触发）" : ""}`;
              list.appendChild(hookEl);
            });
            row.appendChild(list);
          } else if (isList && Array.isArray(rawVal)) {
            const factList = node("ul", "codex-fact-list");
            rawVal.forEach((item) => factList.appendChild(node("li", "", String(item))));
            row.appendChild(factList);
          } else if (key === "known_abilities_detail" && Array.isArray(rawVal)) {
            const abilityList = node("div", "codex-ability-list");
            rawVal.forEach((a) => {
              const item = node("div", "codex-ability-item");
              const name = typeof a === "object" ? a.name : a;
              const desc = typeof a === "object" ? a.description : "";
              if (name) item.appendChild(node("span", "codex-pill pill-ability", name));
              if (desc) item.appendChild(node("span", "codex-ability-desc", desc));
              abilityList.appendChild(item);
            });
            row.appendChild(abilityList);
          } else {
            row.appendChild(node("p", "", String(rawVal)));
          }
          detailsPanel.appendChild(row);
        });
        if (detailsPanel.childNodes.length > 0) {
          collapsibleBody.appendChild(detailsPanel);
        }
      }
    } else {
      // 兼容旧版 fields
      for (const field of fields) {
        appendViewField(field);
      }
    }
  };

  const appendEditable = ([key, label, isList]) => {
    const row = node("div", "record-field");
    row.appendChild(node("span", "resource-label", label));
    if (isList && RECORD_FACT_KEYS.has(key)) {
      const editor = node("div", "record-fact-editor");
      editor.dataset.originalValue = JSON.stringify(valueSource[key] || []);
      const factRow = (fact) => {
        const rowEl = node("div", "record-fact-row");
        const input = document.createElement("textarea");
        input.className = "record-fact-input";
        input.rows = "1";
        input.value = fact || "";
        input.addEventListener("input", () => { recordsDirty = recomputeRecordsDirty(); });
        const removeBtn = node("button", "ability-remove-btn", "×");
        removeBtn.type = "button";
        removeBtn.title = "删除该条";
        removeBtn.setAttribute("aria-label", "删除该条事实");
        removeBtn.onclick = () => { rowEl.remove(); recordsDirty = recomputeRecordsDirty(); };
        rowEl.append(input, removeBtn);
        return rowEl;
      };
      const render = () => {
        editor.replaceChildren();
        (Array.isArray(valueSource[key]) ? valueSource[key] : [])
          .forEach((fact) => editor.appendChild(factRow(fact)));
        const addBtn = node("button", "text-button", "＋ 添加一条");
        addBtn.type = "button";
        addBtn.onclick = () => {
          const newRow = factRow("");
          editor.insertBefore(newRow, addBtn);
          const first = newRow.querySelector("textarea");
          if (first) first.focus();
          recordsDirty = recomputeRecordsDirty();
        };
        editor.appendChild(addBtn);
      };
      render();
      controls[key] = editor;
      factRenderers[key] = render;
      row.appendChild(editor);
      editContainer.appendChild(row);
      return;
    }
    if (key === "known_abilities") {
      const editor = node("div", "record-ability-editor");
      const abilityRow = (ability) => {
        const rowEl = node("div", "record-ability-row");
        const nameInput = document.createElement("input");
        nameInput.type = "text";
        nameInput.className = "record-ability-name";
        nameInput.placeholder = "名称";
        nameInput.maxLength = 20;
        nameInput.value = ability?.name || "";
        const descInput = document.createElement("textarea");
        descInput.className = "record-ability-desc";
        descInput.rows = "1";
        descInput.placeholder = "描述（可留空）";
        descInput.maxLength = 120;
        descInput.value = ability?.description || "";
        const markInput = () => { recordsDirty = recomputeRecordsDirty(); };
        nameInput.addEventListener("input", markInput);
        descInput.addEventListener("input", markInput);
        const removeBtn = node("button", "ability-remove-btn", "×");
        removeBtn.type = "button";
        removeBtn.title = "删除该能力";
        removeBtn.setAttribute("aria-label", "删除该能力");
        removeBtn.onclick = () => { rowEl.remove(); recordsDirty = recomputeRecordsDirty(); };
        rowEl.append(nameInput, descInput, removeBtn);
        return rowEl;
      };
      const render = () => {
        editor.replaceChildren();
        (Array.isArray(valueSource[key]) ? valueSource[key] : [])
          .forEach((ability) => editor.appendChild(abilityRow(ability)));
        const addBtn = node("button", "text-button", "＋ 添加能力");
        addBtn.type = "button";
        addBtn.onclick = () => {
          const newRow = abilityRow({ name: "", description: "" });
          editor.insertBefore(newRow, addBtn);
          newRow.querySelector(".record-ability-name")?.focus();
          recordsDirty = recomputeRecordsDirty();
        };
        editor.appendChild(addBtn);
      };
      render();
      controls[key] = editor;
      factRenderers[key] = render;
      editor.dataset.originalValue = snapshotAbilityValues(editor);
      row.appendChild(editor);
      editContainer.appendChild(row);
      return;
    }
    const control = document.createElement("textarea");
    control.rows = "1";
    control.value = isList ? _formatEditListValue(valueSource[key]) : (valueSource[key] ?? "");
    control.dataset.originalValue = control.value;
    if (isList) control.dataset.isList = "1";
    control.addEventListener("input", () => { recordsDirty = recomputeRecordsDirty(); });
    row.appendChild(control);
    editContainer.appendChild(row);
    controls[key] = control;
  };

  const applySourceToControls = () => {
    for (const [key, , isList] of editableFields) {
      if (factRenderers[key]) {
        factRenderers[key]();
        if (key === "known_abilities") {
          controls[key].dataset.originalValue = snapshotAbilityValues(controls[key]);
        } else {
          controls[key].dataset.originalValue = JSON.stringify(valueSource[key] || []);
        }
        continue;
      }
      const control = controls[key];
      control.value = isList ? _formatEditListValue(valueSource[key]) : (valueSource[key] ?? "");
      control.dataset.originalValue = control.value;
    }
  };

  const renderEdit = () => {
    editContainer.replaceChildren();
    editableFields.forEach((field) => appendEditable(field));
    if (!onSave) return;

    const actions = node("div", "record-edit-actions");
    const cancelBtn = node("button", "text-button", "取消");
    const saveBtn = node("button", "text-button is-primary", "保存");
    const message = node("span", "record-edit-message");
    cancelBtn.type = "button";
    saveBtn.type = "button";

    cancelBtn.onclick = () => {
      applySourceToControls();
      message.textContent = "";
      message.className = "record-edit-message";
      recordsDirty = recomputeRecordsDirty();
      switchToView();
    };

    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      message.textContent = "保存中…";
      message.className = "record-edit-message";
      try {
        const values = {};
        for (const [key, , isList] of editableFields) {
          const control = controls[key];
          const original = valueSource[key];
          if (factRenderers[key]) {
            const current = key === "known_abilities" ? collectAbilityValues(control) : collectFactValues(control);
            const originalList = Array.isArray(original) ? original : [];
            if (JSON.stringify(current) !== JSON.stringify(originalList)) {
              values[key] = current;
            }
          } else if (isList) {
            const current = _splitListValue(control.value);
            const originalList = Array.isArray(original) ? original : [];
            if (JSON.stringify(current) !== JSON.stringify(originalList)) {
              values[key] = current;
            }
          } else if (control.value !== (original ?? "")) {
            values[key] = control.value;
          }
        }
        if (!Object.keys(values).length) {
          message.textContent = "";
          message.className = "record-edit-message";
          saveBtn.disabled = false;
          recordsDirty = recomputeRecordsDirty();
          renderView();
          switchToView();
          showToast("没有需要保存的改动");
          return;
        }
        await onSave(values);
        Object.assign(valueSource, values);
        applySourceToControls();
        renderView();
        switchToView();
        recordsDirty = recomputeRecordsDirty();
        refreshRecords({ force: !recordsDirty });
      } catch (error) {
        message.textContent = error?.message || "保存失败";
        message.className = "record-edit-message is-error";
      } finally {
        saveBtn.disabled = false;
      }
    };

    actions.append(cancelBtn, saveBtn, message);
    editContainer.appendChild(actions);
  };

  const switchToEdit = () => {
    editing = true;
    collapsed = false;
    if (id != null) recordsExpandedIds.add(id);
    card.classList.remove("is-collapsed");
    quoteBox.hidden = true;
    collapsibleBody.hidden = true;
    editContainer.hidden = false;
    editButton.hidden = true;
    const firstControl = editContainer.querySelector("input, textarea");
    if (firstControl) firstControl.focus();
    window.requestAnimationFrame(() => {
      editContainer.querySelectorAll("textarea").forEach(autoExpandTextarea);
    });
  };

  const switchToView = () => {
    editing = false;
    editContainer.hidden = true;
    quoteBox.hidden = !quote;
    collapsibleBody.hidden = collapsed;
    editButton.hidden = false;
  };

  editButton.onclick = () => {
    renderEdit();
    switchToEdit();
  };

  const render = () => {
    renderView();
    if (onSave) {
      renderEdit();
    } else {
      editButton.hidden = true;
      editContainer.hidden = true;
    }
    syncCollapse();
  };

  return { card, populate, render };
}

function renderRecords(records) {
  renderContinuityRecords(records.continuity || []);
  renderNpcRecords(records.npc_profiles || []);
  renderSettingRecords(records.world_settings || []);
  renderRecordsRail();
}

function emptyState(message) {
  return node("div", "records-empty", message);
}

function renderContinuityRecords(items) {
  els.recordsContinuityList.replaceChildren();
  if (!items.length) {
    els.recordsContinuityList.appendChild(emptyState("暂无连续性记录。"));
    return;
  }

  const activeItems = items.filter((i) => i.status === "active");
  const inactiveItems = items.filter((i) => i.status !== "active");

  const renderItem = (item, container) => {
    const active = item.status === "active";
    const kindLabel = item.kind === "commitment" ? "承诺" : "悬念";
    const statusLabel = item.status === "active" ? "进行中" : item.status === "resolved" ? "已解决" : "已取消";

    // 1. 属性网格
    const metaRows = [
      ["类别", kindLabel],
      ["状态", statusLabel],
    ];
    if (item.location) metaRows.push(["地点", item.location]);
    if (item.deadline) metaRows.push(["期限", item.deadline]);
    if (item.created_turn != null) metaRows.push(["始于", `第 ${item.created_turn} 回合`]);

    // 2. 核心引语区 (优先展示当前进展，若无则回退至原始事项)
    let quote = null;
    if (item.current_state_text) {
      quote = { text: `“${item.current_state_text}”`, subLabel: "当前进展" };
    } else if (item.premise) {
      quote = { text: `“${item.premise}”`, subLabel: "原始事项" };
    }

    // 3. 标签胶囊区 (关联实体)
    const pills = [];
    if (item.entities?.length) {
      item.entities.forEach((entity) => {
        if (entity) pills.push({ text: entity, kind: "alias" });
      });
    }

    // 4. 深度详查区 (原始事项)
    const details = [];
    if (item.premise) {
      details.push(["premise", "原始事项", false, item.premise]);
    }

    const builder = buildRecordCard({
      id: item.id,
      title: item.subject || "（无主题）",
      statusText: `${kindLabel} · ${statusLabel}`,
      statusActive: active,
      metaRows,
      quote,
      pills,
      details,
      fields: CONTINUITY_VIEW_FIELDS,
      editableFields: CONTINUITY_EDITABLE_FIELDS,
      onSave: active ? async (values) => {
        await updateRecord("continuity", item.id, values);
        showToast("连续性已更新");
      } : null,
    });
    builder.populate(item);
    builder.render();
    container.appendChild(builder.card);
  };

  // 1. 渲染进行中的活跃项
  for (const item of activeItems) {
    renderItem(item, els.recordsContinuityList);
  }

  // 2. 渲染非活跃项的分组（已解决/已取消，默认折叠）
  if (inactiveItems.length > 0) {
    const { group, body } = buildRecordsGroup({
      id: "continuity_inactive",
      title: "已结案 / 已归档",
      count: inactiveItems.length,
    });
    for (const item of inactiveItems) {
      renderItem(item, body);
    }
    els.recordsContinuityList.appendChild(group);
  }
}

function _fieldMetaByKey(lists) {
  const meta = new Map();
  for (const fields of lists) {
    for (const entry of fields) meta.set(entry[0], entry);
  }
  return meta;
}

// 可编辑字段 meta 只覆盖 NPC_EDITABLE_FIELDS；last-scene pair 走
// NPC_VIEW_FIELDS 只读展示，绝不进入编辑表单。
const NPC_FIELD_META = _fieldMetaByKey([NPC_EDITABLE_FIELDS]);
const SETTING_FIELD_META = _fieldMetaByKey([SETTING_FIELDS]);

const NPC_HIDDEN_FIELD_KEYS = new Set(["aliases"]);
const RECORD_FACT_KEYS = new Set(["public_facts", "hidden_facts"]);

function _editableFieldsFor(data, meta) {
  return Object.keys(data)
    .map((key) => meta.get(key))
    .filter(Boolean);
}

function renderNpcRecords(profiles) {
  els.recordsNpcList.replaceChildren();
  if (!profiles.length) {
    els.recordsNpcList.appendChild(emptyState("暂无人物档案。"));
    return;
  }
  for (const profile of profiles) {
    const pub = profile.public || {};
    const dir = profile.director_only || {};
    const data = { ...pub, ...dir };

    // 1. 属性网格：阵营 -> 身份 -> 特质 -> 地点 -> 态度
    const metaRows = [];
    if (pub.faction) metaRows.push(["阵营", pub.faction]);
    if (pub.role) metaRows.push(["身份", pub.role]);
    if (pub.personality_trait) metaRows.push(["特质", pub.personality_trait]);
    if (pub.last_known_location) metaRows.push(["地点", pub.last_known_location]);
    if (pub.attitude_to_player) metaRows.push(["态度", pub.attitude_to_player]);

    // 2. 核心引语区 (内外完全一致：优先展示最近出场记录与出场回合，若无最近出场则回退至外貌描述)
    let quote = null;
    if (pub.last_scene_summary) {
      quote = {
        text: `“${pub.last_scene_summary}”`,
        subLabel: "最近出场",
        turnIndex: pub.last_scene_turn,
      };
    } else if (pub.appearance) {
      quote = {
        text: `“${pub.appearance}”`,
        subLabel: "外貌描述",
      };
    }

    // 3. 标签胶囊区：仅展示别名（已知能力在下方详述中展示，避免重复）
    const pills = [];
    if (pub.aliases?.length) {
      pub.aliases.forEach((alias) => {
        if (alias) pills.push({ text: alias, kind: "alias" });
      });
    }

    // 4. 深度详查区 (已知能力详述、外貌特征、最后出场地点与创作者私密字段)
    const details = [];
    if (pub.known_abilities?.length) {
      details.push(["known_abilities_detail", "已知能力", false, pub.known_abilities]);
    }
    if (pub.appearance) {
      details.push(["appearance", "外貌特征", false, pub.appearance]);
    }
    if (pub.last_known_location) {
      details.push(["last_known_location", "最后出场地点", false, pub.last_known_location]);
    }
    if (dir.core_motivation) details.push(["core_motivation", "核心动机", false, dir.core_motivation]);
    if (dir.hidden_secret) details.push(["hidden_secret", "隐藏秘密", false, dir.hidden_secret]);
    if (dir.conflict_style) details.push(["conflict_style", "冲突风格", false, dir.conflict_style]);
    if (dir.social_style) details.push(["social_style", "社交风格", false, dir.social_style]);
    if (dir.director_notes) details.push(["director_notes", "导演备注", false, dir.director_notes]);

    const editableFields = _editableFieldsFor(data, NPC_FIELD_META)
      .filter(([key]) => !NPC_HIDDEN_FIELD_KEYS.has(key));

    const builder = buildRecordCard({
      id: profile.id,
      title: pub.name || "（未命名）",
      statusText: "",
      metaRows,
      quote,
      pills,
      details,
      fields: [...editableFields, ...NPC_VIEW_FIELDS],
      editableFields,
      viewFormatters: NPC_VIEW_FORMATTERS,
      onSave: async (values) => {
        await updateRecord("npc", profile.id, values);
        showToast("人物档案已更新");
      },
    });
    builder.populate(data);
    builder.render();
    els.recordsNpcList.appendChild(builder.card);
  }
}

function renderSettingRecords(settings) {
  els.recordsSettingList.replaceChildren();
  if (!settings.length) {
    els.recordsSettingList.appendChild(emptyState("暂无世界设定。"));
    return;
  }
  for (const setting of settings) {
    const data = setting.fields || {};
    // 1. 属性网格
    const metaRows = [];
    if (setting.hit_count != null && setting.hit_count > 0) {
      metaRows.push(["登场", `${setting.hit_count} 次`]);
    }
    if (setting.created_turn != null) {
      metaRows.push(["创建", `第 ${setting.created_turn} 回合`]);
    }

    // 2. 核心引语区 (设定概述)
    const quote = data.summary ? { text: `“${data.summary}”`, subLabel: "设定概述" } : null;

    // 3. 标签胶囊区
    const pills = [];

    // 4. 深度详查区 (公开事实与导演事实)
    const details = [];
    if (data.public_facts?.length) {
      details.push(["public_facts", "公开事实", true, data.public_facts]);
    }
    if (data.hidden_facts?.length) {
      details.push(["hidden_facts", "导演事实", true, data.hidden_facts]);
    }

    const builder = buildRecordCard({
      id: setting.id,
      title: data.name || "（未命名）",
      statusText: "",
      metaRows,
      quote,
      pills,
      details,
      fields: _editableFieldsFor(data, SETTING_FIELD_META),
      editableFields: _editableFieldsFor(data, SETTING_FIELD_META),
      onSave: async (values) => {
        await updateRecord("setting", setting.id, values);
        showToast("世界设定已更新");
      },
    });
    builder.populate(data);
    builder.render();
    els.recordsSettingList.appendChild(builder.card);
  }
}

const EVENT_ROLE_LABELS = { arc: "主线", ambient: "环境" };
const EVENT_STAGE_LABELS = { hidden: "隐藏", engaged: "进行中", resolved: "已解决" };
const EVENT_HOOK_KIND_LABELS = {
  auditory: "听觉", visual: "视觉", rumor: "传闻", item: "物品", environment: "环境",
};

function _formatEventClock(clock) {
  if (!clock) return "";
  return `第${clock.day}日${clock.phase || ""}`;
}

function renderEventRecords(events) {
  if (!els.recordsEventList) return;
  els.recordsEventList.replaceChildren();
  if (!events.length) {
    els.recordsEventList.appendChild(emptyState("暂无导演事件。"));
    return;
  }

  const activeEvents = events.filter((e) => e.stage !== "resolved");
  const resolvedEvents = events.filter((e) => e.stage === "resolved");

  const renderEvent = (event, container) => {
    const roleLabel = EVENT_ROLE_LABELS[event.role] || event.role;
    const stageLabel = EVENT_STAGE_LABELS[event.stage] || event.stage;
    const statusActive = event.stage !== "resolved";

    // 1. 属性网格
    const metaRows = [
      ["定位", roleLabel],
      ["阶段", stageLabel],
    ];
    if (event.deadline_clock) metaRows.push(["期限", _formatEventClock(event.deadline_clock)]);
    if (event.created_turn != null) metaRows.push(["出现", `第 ${event.created_turn} 回合`]);

    // 2. 核心引语区
    const quoteText = event.macro_intent || event.intent || event.subject;
    const quote = quoteText ? { text: `“${quoteText}”`, subLabel: event.macro_intent ? "宏观意图" : "事件意图" } : null;

    // 3. 标签胶囊区 (参演角色)
    const pills = [];
    if (event.actors?.length) {
      event.actors.forEach((actor) => {
        const name = typeof actor === "object" ? actor.name : actor;
        if (name) pills.push({ text: `参演 · ${name}`, kind: "trait" });
      });
    }

    // 4. 深度详查区 (钩子池、世界影响、解决原因等)
    const details = [];
    if (event.subject && quoteText !== event.subject) details.push(["subject", "主题", false, event.subject]);
    if (event.intent && quoteText !== event.intent) details.push(["intent", "意图", false, event.intent]);
    if (event.method) details.push(["method", "手法", false, event.method]);
    if (event.hook_pool?.length) {
      details.push(["hook_pool", "伏笔钩子", "hooks", event.hook_pool]);
    }
    if (event.world_impact?.description) details.push(["world_impact", "世界影响", false, event.world_impact.description]);
    if (event.final_world_impact?.description) details.push(["final_world_impact", "最终影响", false, event.final_world_impact.description]);
    if (event.resolved_reason) details.push(["resolved_reason", "解决原因", false, event.resolved_reason]);

    const builder = buildRecordCard({
      id: event.id || event.event_id || event.title,
      title: event.title || event.subject || "（未命名事件）",
      statusText: `${roleLabel} · ${stageLabel}`,
      statusActive,
      metaRows,
      quote,
      pills,
      details,
      fields: [],
      editableFields: [],
      onSave: null,
    });
    builder.populate(event);
    builder.render();
    container.appendChild(builder.card);
  };

  // 1. 渲染未解决的活跃事件
  for (const event of activeEvents) {
    renderEvent(event, els.recordsEventList);
  }

  // 2. 渲染已结案事件分组（默认折叠）
  if (resolvedEvents.length > 0) {
    const { group, body } = buildRecordsGroup({
      id: "events_resolved",
      title: "已结案事件",
      count: resolvedEvents.length,
    });
    for (const event of resolvedEvents) {
      renderEvent(event, body);
    }
    els.recordsEventList.appendChild(group);
  }
}

const CORE_MEMORY_KIND_OPTIONS = [
  ["world_invariant", "世界恒常"],
  ["protagonist_invariant", "主角恒常"],
  ["narrative_preference", "叙事偏好"],
  ["system_convention", "系统惯例"],
  ["ability_rule", "能力规则"],
];
const CORE_MEMORY_STRENGTH_OPTIONS = [
  ["hard_constraint", "硬约束"],
  ["strong_preference", "强偏好"],
];
const CORE_MEMORY_SOURCE_LABELS = { player: "玩家", campaign_setup: "世界构建" };
let coreMemoryItems = [];

function _buildCoreMemorySelect(options, value) {
  const select = node("select");
  for (const [key, label] of options) {
    const option = node("option", "", label);
    option.value = key;
    if (key === value) option.selected = true;
    select.appendChild(option);
  }
  return select;
}

function renderCoreMemoryItems(items) {
  els.coreMemoryList.replaceChildren();
  if (!items.length) {
    els.coreMemoryList.appendChild(node("p", "core-memory-empty", "暂无条目。点击「添加条目」开始构建世界基础。"));
    return;
  }
  for (const item of items) {
    const row = node("div", "core-memory-item");
    const head = node("div", "core-memory-item-head");
    const kindSelect = _buildCoreMemorySelect(CORE_MEMORY_KIND_OPTIONS, item.kind);
    kindSelect.addEventListener("change", () => { item.kind = kindSelect.value; });
    const strengthSelect = _buildCoreMemorySelect(CORE_MEMORY_STRENGTH_OPTIONS, item.strength);
    strengthSelect.addEventListener("change", () => { item.strength = strengthSelect.value; });
    head.appendChild(kindSelect);
    head.appendChild(strengthSelect);
    head.appendChild(node("span", "core-memory-item-source", CORE_MEMORY_SOURCE_LABELS[item.source] || item.source || ""));
    const removeBtn = node("button", "remove-core-memory-btn", "删除");
    removeBtn.type = "button";
    removeBtn.onclick = () => {
      const index = items.indexOf(item);
      if (index >= 0) items.splice(index, 1);
      renderCoreMemoryItems(items);
    };
    head.appendChild(removeBtn);
    row.appendChild(head);
    const textarea = node("textarea");
    textarea.value = item.content || "";
    textarea.rows = 3;
    textarea.addEventListener("input", () => { item.content = textarea.value; });
    row.appendChild(textarea);
    els.coreMemoryList.appendChild(row);
  }
}

async function openCoreMemoryEditor() {
  els.coreMemoryError.hidden = true;
  try {
    const payload = await loadCoreMemory();
    if (!payload.initialized) {
      els.coreMemoryError.textContent = "核心记忆尚未初始化，请先完成世界初始化。";
      els.coreMemoryError.hidden = false;
    } else {
      coreMemoryItems = (payload.items || []).map((item) => ({ ...item }));
      renderCoreMemoryItems(coreMemoryItems);
    }
    els.coreMemoryDialog.showModal();
  } catch (error) {
    showError(error);
  }
}

function addCoreMemoryItem() {
  coreMemoryItems.push({ id: "", kind: "narrative_preference", strength: "strong_preference", source: "player", content: "" });
  renderCoreMemoryItems(coreMemoryItems);
}

async function saveCoreMemoryEditor() {
  els.saveCoreMemoryBtn.disabled = true;
  try {
    await saveCoreMemory(coreMemoryItems);
    showToast("核心记忆已更新");
    const payload = await loadCoreMemory();
    coreMemoryItems = (payload.items || []).map((item) => ({ ...item }));
    renderCoreMemoryItems(coreMemoryItems);
    els.coreMemoryError.hidden = true;
  } catch (error) {
    els.coreMemoryError.textContent = error?.message || "保存失败";
    els.coreMemoryError.hidden = false;
  } finally {
    els.saveCoreMemoryBtn.disabled = false;
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
if (els.closeLeftDrawerBtn) els.closeLeftDrawerBtn.onclick = closeLeftDrawer;
if (els.openLeftDrawerRailBtn) els.openLeftDrawerRailBtn.onclick = () => { if (els.leftDrawer.classList.contains("open")) closeLeftDrawer(); else openLeftDrawer(); };

if (els.experienceModePlayerBtn) els.experienceModePlayerBtn.onclick = () => switchExperienceMode("player");
if (els.experienceModeCreatorBtn) els.experienceModeCreatorBtn.onclick = () => switchExperienceMode("creator");
if (els.recordsTabContinuity) els.recordsTabContinuity.onclick = () => openRecordsTab("continuity");
if (els.recordsTabNpc) els.recordsTabNpc.onclick = () => openRecordsTab("npc");
if (els.recordsTabSetting) els.recordsTabSetting.onclick = () => openRecordsTab("setting");
if (els.recordsTabEvent) els.recordsTabEvent.onclick = () => openRecordsTab("event");
if (els.openCoreMemoryBtn) els.openCoreMemoryBtn.onclick = () => { closeDrawer(); openCoreMemoryEditor(); };
if (els.closeCoreMemoryBtn) els.closeCoreMemoryBtn.onclick = () => els.coreMemoryDialog.close();
if (els.addCoreMemoryBtn) els.addCoreMemoryBtn.onclick = addCoreMemoryItem;
if (els.saveCoreMemoryBtn) els.saveCoreMemoryBtn.onclick = saveCoreMemoryEditor;
els.drawerBackdrop.onclick = () => { closeDrawer(); closeLeftDrawer(); closeRecordsDrawer(); };
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
  openModelConfig();
};
els.closeModelConfigBtn.onclick = () => els.modelConfigDialog.close();

async function refreshGameView() {
  if (["generating", "confirming"].includes(state.mode) || activeGenerationRun) {
    showToast("正在生成中，请稍候");
    return;
  }
  try {
    await loadAll({ scroll: "instant" });
    showToast("已同步最新故事状态");
  } catch (error) {
    reportClientError(error, "refresh_game_view_failed");
  }
}

// Global hotkeys (Escape, F5, Ctrl+R)
document.addEventListener("keydown", (event) => {
  if (
    event.key === "F5"
    || ((event.key === "r" || event.key === "R") && (event.ctrlKey || event.metaKey))
  ) {
    if (!event.shiftKey) {
      event.preventDefault();
      refreshGameView();
      return;
    }
  }

  if (event.key === "Escape") {
    if (document.querySelector("dialog[open]")) return;
    let closed = false;
    if (els.leftDrawer?.classList.contains("open")) { closeLeftDrawer(); closed = true; }
    if (els.recordsDrawer?.classList.contains("open")) { closeRecordsDrawer(); closed = true; }
    if (closed && document.activeElement && document.activeElement.blur) {
      document.activeElement.blur();
    }
  }
});

document.addEventListener("click", (event) => {
  if (event.target.closest("dialog") || event.target.closest("[popover]:open")) return;
  if (event.target.closest("#recordsRail")) return;
  if (els.leftDrawer?.classList.contains("open") && event.target.closest("#leftDrawer")) return;
  if (els.recordsDrawer?.classList.contains("open") && event.target.closest("#recordsDrawer")) return;
  if (event.target.closest(".codex-button")) return; // Prevent interference with other popovers

  if (els.leftDrawer?.classList.contains("open")) closeLeftDrawer();
  if (els.recordsDrawer?.classList.contains("open")) closeRecordsDrawer();
});
els.tokenUsageDialog.addEventListener("click", (e) => {
  if (e.target === els.tokenUsageDialog) {
    if (els.tokenUsageDialog.hidePopover) els.tokenUsageDialog.hidePopover();
    else els.tokenUsageDialog.removeAttribute("open");
  }
});
bindPopoverFallbackDismiss(els.tokenUsageDialog, () => {
  if (els.tokenUsageDialog.hidePopover) els.tokenUsageDialog.hidePopover();
  else els.tokenUsageDialog.removeAttribute("open");
});
const settingInfo = bindSettingInfoButtons({
  dialog: els.settingInfoDialog,
  titleEl: els.settingInfoTitle,
  contentEl: els.settingInfoContent,
  buttons: {
    canonInfoBtn: els.canonInfoBtn,
    tokenUsageInfoBtn: els.tokenUsageInfoBtn,
    oneClickInfoBtn: els.oneClickInfoBtn,
  },
  specs: {
    canonInfoBtn: {
      title: "Canon 后校验",
      body: "校验正文与已确定事实的一致性。关闭后可能产生与已确定事实冲突的内容，但可大幅节省 Token 和等待时间。",
    },
    tokenUsageInfoBtn: {
      title: "Token 用量显示",
      body: "显示 Token 消耗。关闭后不影响生成质量或实际成本。",
    },
    oneClickInfoBtn: {
      title: "一键确认",
      body: "正文生成后自动确认。开启后若 LLM 随机性导致正文质量波动，可能会因此增加 Token 消耗。",
    },
  },
});
bindSettingInfoDismiss(els.settingInfoDialog, () => settingInfo.hide());
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
els.canonValidationBtn.onchange = async () => {
  const enabled = els.canonValidationBtn.checked;
  const previous = !enabled;
  els.canonValidationBtn.disabled = true;
  try {
    const result = await request("/api/player/canon-validation", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
    state.playerState.canon_validation_enabled = result.canon_validation_enabled;
    renderCanonValidation();
    showToast(`后校验已${result.canon_validation_enabled ? "开启" : "关闭"}`);
  } catch (error) {
    state.playerState.canon_validation_enabled = previous;
    renderCanonValidation();
    showError(error);
  } finally {
    renderControls();
  }
};
els.fontSizeSelect.onchange = readerPreferences.save;
els.fontFamilySelect.onchange = readerPreferences.save;
els.themeSelect.onchange = readerPreferences.save;
els.tokenUsageSelect.onchange = () => readerPreferences.save({ silent: true });
els.oneClickConfirm.onchange = () => readerPreferences.save({ silent: true });
els.undoBtn.onclick = () => changeHistory("undo"); els.redoBtn.onclick = () => changeHistory("redo");
els.turnNavigatorLatest.onclick = jumpToLatestTurn;
els.initAbilitySystem.onchange = toggleCustomAbilityField;
els.initWorldStyle.onchange = toggleCustomWorldStyleField;
els.addAbilityBtn.onclick = () => addAbilityRow();
els.campaignSetupBtn.onclick = () => requestCampaignSetup();
els.regenerateCampaignSetupBtn.onclick = () => requestCampaignSetup({ regenerate: true });
els.editInitConfigBtn.onclick = editInitConfig;
for (const button of els.storyInspirationTags.querySelectorAll("button[data-value]")) {
  button.onclick = () => {
    const textarea = els.initForm.elements.story_expectation;
    const value = button.dataset.value;
    const existing = textarea.value.trim();
    if (existing.includes(value)) return;
    textarea.value = existing ? `${existing}、${value}` : value;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  };
}
els.initForm.addEventListener("submit", (event) => { event.preventDefault(); initialize(); });
els.continueInitBtn.onclick = () => els.initDialog.showModal();
els.closeInitDialogBtn.onclick = closeInitializationDialog;
els.initDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeInitializationDialog();
});
window.addEventListener("scroll", () => {
  if (window.scrollY <= 220) loadEarlierHistory();
}, { passive: true });
window.addEventListener("resize", () => {
  refreshNavigationObserver({ force: true });
}, { passive: true });

// Prevent wheel scroll chaining to background window from modals and drawers
function _findScrollableContainer(target, boundaryElement) {
  let el = target;
  while (el && el !== boundaryElement && el !== document.body && el !== document.documentElement) {
    if (el.scrollHeight > el.clientHeight + 1) {
      const style = window.getComputedStyle(el);
      const overflowY = style.overflowY;
      if (overflowY === "auto" || overflowY === "scroll") {
        return el;
      }
    }
    el = el.parentElement;
  }
  if (boundaryElement && boundaryElement.scrollHeight > boundaryElement.clientHeight + 1) {
    const style = window.getComputedStyle(boundaryElement);
    const overflowY = style.overflowY;
    if (overflowY === "auto" || overflowY === "scroll") {
      return boundaryElement;
    }
  }
  return null;
}

document.addEventListener("wheel", (event) => {
  const scrollableParent = event.target.closest(".drawer, .modal, .codex-modal, .token-usage-popover");
  if (!scrollableParent) return;

  const scrollElem = _findScrollableContainer(event.target, scrollableParent);
  const delta = event.deltaY;

  if (!scrollElem) {
    event.preventDefault();
    return;
  }

  const isAtTop = scrollElem.scrollTop <= 0 && delta < 0;
  const isAtBottom = scrollElem.scrollTop + scrollElem.clientHeight >= scrollElem.scrollHeight - 1 && delta > 0;

  if (isAtTop || isAtBottom) {
    event.preventDefault();
  }
}, { passive: false });

// Draggable codex modal handle
let isDraggingCodex = false;
let codexDragStartX = 0;
let codexDragStartY = 0;
let codexDragInitialLeft = 0;
let codexDragInitialTop = 0;

function initCodexDrag() {
  const header = $("codexHeader") || els.codexDialog?.querySelector(".codex-modal-header");
  if (!header || !els.codexDialog) return;

  header.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button, a, input, select, textarea")) return;
    if (e.button !== 0) return;

    isDraggingCodex = true;
    codexDragStartX = e.clientX;
    codexDragStartY = e.clientY;

    const rect = els.codexDialog.getBoundingClientRect();
    codexDragInitialLeft = rect.left;
    codexDragInitialTop = rect.top;

    header.setPointerCapture(e.pointerId);
    els.codexDialog.classList.add("is-dragging");
  });

  header.addEventListener("pointermove", (e) => {
    if (!isDraggingCodex) return;

    const deltaX = e.clientX - codexDragStartX;
    const deltaY = e.clientY - codexDragStartY;

    const dialogWidth = els.codexDialog.offsetWidth || 500;
    const dialogHeight = els.codexDialog.offsetHeight || 300;

    const rawLeft = codexDragInitialLeft + deltaX;
    const rawTop = codexDragInitialTop + deltaY;

    const clampedLeft = Math.max(8, Math.min(rawLeft, window.innerWidth - dialogWidth - 8));
    const clampedTop = Math.max(8, Math.min(rawTop, window.innerHeight - dialogHeight - 8));

    els.codexDialog.style.position = "fixed";
    els.codexDialog.style.left = `${clampedLeft}px`;
    els.codexDialog.style.top = `${clampedTop}px`;
    els.codexDialog.style.margin = "0";
  });

  const stopDrag = (e) => {
    if (!isDraggingCodex) return;
    isDraggingCodex = false;
    els.codexDialog.classList.remove("is-dragging");
    try {
      if (header.hasPointerCapture(e.pointerId)) {
        header.releasePointerCapture(e.pointerId);
      }
    } catch {}
  };

  header.addEventListener("pointerup", stopDrag);
  header.addEventListener("pointercancel", stopDrag);
}

function initDrawerResizers() {
  try {
    const savedLeftW = localStorage.getItem("loreloop_left_drawer_w");
    if (savedLeftW) {
      const val = parseInt(savedLeftW, 10);
      if (val >= 500 && val <= 1200) {
        document.documentElement.style.setProperty("--drawer-left-w", `${val}px`);
      }
    }
    const savedRightW = localStorage.getItem("loreloop_right_drawer_w");
    if (savedRightW) {
      const val = parseInt(savedRightW, 10);
      if (val >= 440 && val <= 1200) {
        document.documentElement.style.setProperty("--drawer-right-w", `${val}px`);
      }
    }
  } catch (_) {}

  const bindResizer = (drawer, side) => {
    if (!drawer) return;
    let handle = drawer.querySelector(`.drawer-resize-handle-${side}`);
    if (!handle) {
      handle = node("div", `drawer-resize-handle drawer-resize-handle-${side}`);
      handle.title = "左右拖拽调整栏宽（双击恢复默认）";
      drawer.appendChild(handle);
    }

    const isRightEdge = side === "right";
    const defaultW = isRightEdge ? 500 : 440;

    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();

      const startX = e.clientX;
      const startW = drawer.getBoundingClientRect().width;
      const cssVar = isRightEdge ? "--drawer-left-w" : "--drawer-right-w";
      const storageKey = isRightEdge ? "loreloop_left_drawer_w" : "loreloop_right_drawer_w";

      handle.classList.add("is-dragging");
      document.body.classList.add("is-resizing-drawer");

      let currentW = startW;

      const onPointerMove = (moveEvent) => {
        const deltaX = moveEvent.clientX - startX;
        const rawW = isRightEdge ? startW + deltaX : startW - deltaX;
        const minW = Math.min(defaultW, window.innerWidth - 60);
        const maxW = Math.min(window.innerWidth - 120, 960);
        currentW = Math.max(minW, Math.min(maxW, rawW));
        document.documentElement.style.setProperty(cssVar, `${currentW}px`);
      };

      const onPointerUp = () => {
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", onPointerUp);
        window.removeEventListener("pointercancel", onPointerUp);
        handle.classList.remove("is-dragging");
        document.body.classList.remove("is-resizing-drawer");
        try {
          localStorage.setItem(storageKey, String(Math.round(currentW)));
        } catch (_) {}
      };

      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
      window.addEventListener("pointercancel", onPointerUp);
    });

    handle.addEventListener("dblclick", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const cssVar = isRightEdge ? "--drawer-left-w" : "--drawer-right-w";
      const storageKey = isRightEdge ? "loreloop_left_drawer_w" : "loreloop_right_drawer_w";
      document.documentElement.style.setProperty(cssVar, `${defaultW}px`);
      try {
        localStorage.removeItem(storageKey);
      } catch (_) {}
      showToast("已恢复默认栏宽");
    });
  };

  bindResizer(els.recordsDrawer, "right");
  bindResizer(els.leftDrawer, "right");
  bindResizer(els.drawer, "left");
}

initCodexDrag();
initDrawerResizers();
toggleCustomAbilityField();
toggleCustomWorldStyleField();
bootstrap().catch(showError);
