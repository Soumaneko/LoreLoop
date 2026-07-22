import { acknowledgeOperation, downloadSave, importSaveArchive, request, stream, subscribe } from "./api.js";
import {
  activePending,
  clearDrafts,
  modeLabels,
  readDraft,
  selectedVariant,
  setMode,
  state,
  writeDraft,
} from "./state.js";

const $ = (id) => document.getElementById(id);
const els = {
  worldTime: $("worldTime"), worldLocation: $("worldLocation"), statusPill: $("statusPill"),
  story: $("story"), welcome: $("welcome"), history: $("history"),
  activePlayerAction: $("activePlayerAction"),
  streamingCard: $("streamingCard"), streamingText: $("streamingText"), streamingLabel: $("streamingLabel"),
  pendingCard: $("pendingCard"), suggestions: $("suggestions"), suggestionList: $("suggestionList"),
  errorCard: $("errorCard"), errorMessage: $("errorMessage"), dismissErrorBtn: $("dismissErrorBtn"),
  playerInput: $("playerInput"), generateBtn: $("generateBtn"), inputHint: $("inputHint"),
  drawer: $("drawer"), leftDrawer: $("leftDrawer"), drawerBackdrop: $("drawerBackdrop"), openDrawerBtn: $("openDrawerBtn"), closeDrawerBtn: $("closeDrawerBtn"), openLeftDrawerBtn: $("openLeftDrawerBtn"), closeLeftDrawerBtn: $("closeLeftDrawerBtn"),
  saveSelect: $("saveSelect"), newSaveBtn: $("newSaveBtn"), manageSavesBtn: $("manageSavesBtn"), playerSummary: $("playerSummary"), resourceSummary: $("resourceSummary"),
  modelSettingsBtn: $("modelSettingsBtn"), modelConfigDialog: $("modelConfigDialog"), modelConfigForm: $("modelConfigForm"),
  closeModelConfigBtn: $("closeModelConfigBtn"), modelConfigIntro: $("modelConfigIntro"), modelApiKey: $("modelApiKey"),
  modelKeyHint: $("modelKeyHint"), modelBaseUrl: $("modelBaseUrl"), modelName: $("modelName"),
  modelThinking: $("modelThinking"), modelTestResult: $("modelTestResult"), testModelBtn: $("testModelBtn"), saveModelBtn: $("saveModelBtn"),
  difficultySelect: $("difficultySelect"), undoBtn: $("undoBtn"), redoBtn: $("redoBtn"),
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
const HISTORY_PAGE_SIZE = 5;
const PLAYER_SHELL_CACHE_KEY = "tvg:player-shell";
const CONFIRMATION_IN_FLIGHT_KEY = "tvg:confirmation-in-flight";

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

function renderControls() {
  const busy = saveManagerBusy || modelConfigBusy || ["booting", "needs_model", "generating", "confirming", "switching"].includes(state.mode);
  const pending = Boolean(activePending());
  els.statusPill.dataset.mode = state.mode;
  els.statusPill.querySelector("span").textContent = modeLabels[state.mode] || "等待行动";
  els.playerInput.disabled = busy || pending || state.playerState?.needs_init;
  els.generateBtn.disabled = busy || pending || state.playerState?.needs_init || !els.playerInput.value.trim();
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
}

function renderHeader() {
  const campaign = state.playerState?.campaign || {};
  els.worldTime.textContent = campaign.world_time || "尚未启程";
  els.worldLocation.textContent = campaign.location || "等待世界载入";
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function appendHighlightedText(element, text) {
  const highlights = state.playerState?.highlights || [];
  if (!text || !highlights.length) {
    element.textContent = text || "";
    return element;
  }
  const terms = [...highlights].sort((a, b) => b.surface.length - a.surface.length);
  const lookup = new Map(terms.map((item) => [item.surface.toLocaleLowerCase(), item]));
  const pattern = terms.map((item) => escapeRegExp(item.surface)).join("|");
  if (!pattern) {
    element.textContent = text;
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
    element.appendChild(mark);
    cursor = start + value.length;
  }
  if (cursor < text.length) element.appendChild(document.createTextNode(text.slice(cursor)));
  return element;
}

function renderTurn(turn, { showClosing = false } = {}) {
  const wrap = node("section", "turn");
  wrap.dataset.turnIndex = String(turn.turn_index);
  wrap.appendChild(node("div", "player-action", turn.user_input || "继续故事"));
  const card = node("article", "narrative-card");
  const kicker = node("div", "narrative-kicker");
  kicker.append(node("span", "quill", "✦"), node("span", "", `第 ${turn.turn_index} 回合`));
  card.append(kicker, appendHighlightedText(node("div", "scene-text"), turn.scene_text));
  if (showClosing && turn.closing_prompt) {
    card.appendChild(node("div", "closing-prompt", turn.closing_prompt));
  }
  wrap.appendChild(card);
  return wrap;
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

function showActivePlayerAction(text) {
  els.activePlayerAction.textContent = text || "";
  els.activePlayerAction.hidden = !text;
}

async function loadEarlierHistory() {
  const pagination = state.playerState?.history_pagination || {};
  const history = state.playerState?.history || [];
  if (!state.historyPagingEnabled || state.historyLoading || !pagination.has_more || !history.length) return;

  state.historyLoading = true;
  const saveName = state.activeSave;
  const previousHeight = document.documentElement.scrollHeight;
  const previousY = window.scrollY;
  try {
    const page = await request(
      `/api/player/history?before_turn=${encodeURIComponent(history[0].turn_index)}&limit=${HISTORY_PAGE_SIZE}`,
    );
    if (saveName !== state.activeSave) return;
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
    if (earlier.length) {
      state.playerState.history = [...earlier, ...state.playerState.history];
      const fragment = document.createDocumentFragment();
      earlier.forEach((turn) => fragment.appendChild(renderTurn(turn)));
      els.history.prepend(fragment);
      const addedHeight = document.documentElement.scrollHeight - previousHeight;
      window.scrollTo({ top: previousY + addedHeight, behavior: "auto" });
    }
    state.playerState.history_pagination = page.pagination || { has_more: false };
  } catch (error) {
    showToast(error.message || "更早的故事暂时无法载入");
  } finally {
    state.historyLoading = false;
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

  showActivePlayerAction(pending.user_input || "");
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
  const edit = node("button", "soft-button", "编辑正文");
  const rephrase = node("button", "soft-button", "重新措辞");
  const reroll = node("button", "soft-button", "重新判定");
  const discard = node("button", "soft-button danger-button", "删除");
  const confirm = node("button", "primary-action", "确认采用");
  edit.onclick = () => beginEdit(display, closing, actions, variant);
  edit.disabled = confirming;
  rephrase.disabled = pending.kind === "opening" || ["generating", "confirming"].includes(state.mode);
  rephrase.title = "保留本次判定与导演输入，只重新生成正文";
  rephrase.onclick = () => retryTurn("rephrase");
  reroll.disabled = rephrase.disabled;
  reroll.title = "重新运行完整生成管线并重新投骰";
  reroll.onclick = () => retryTurn("reroll");
  discard.disabled = confirming;
  discard.onclick = discardPending;
  confirm.disabled = confirming;
  confirm.textContent = confirming ? "正在确认……" : "确认采用";
  confirm.onclick = confirmTurn;
  actions.append(edit, rephrase, reroll);
  if (pending.kind !== "opening") actions.appendChild(discard);
  actions.appendChild(confirm);
  els.pendingCard.append(toolbar, display);
  if (closing) els.pendingCard.appendChild(closing);
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
  els.pendingCard.insertBefore(editor, actions);
  els.pendingCard.insertBefore(editActions, actions);
  cancel.onclick = () => renderPending();
  save.onclick = () => {
    writeDraft(variant, editor.value);
    renderPending();
    showToast("修改已保存在此浏览器中");
  };
  editActions.append(cancel, save);
  editor.focus();
}

function selectVariant(index) {
  const pending = activePending();
  if (!pending || index < 0 || index >= pending.variants.length) return;
  state.selectedPendingIndex = index;
  renderPending();
}

function renderDrawer() {
  const campaign = state.playerState?.campaign || {};
  const player = campaign.player || {};
  const conditions = [...(player.conditions || []), ...(campaign.scene_conditions || [])];
  els.playerSummary.replaceChildren();
  els.playerSummary.appendChild(node("strong", "", player.name || "尚未命名"));
  els.playerSummary.appendChild(node("div", "", player.visible_status || "状态正常"));
  if (conditions.length) {
    const tags = node("div", "tags");
    conditions.forEach((item) => tags.appendChild(node("span", "tag", item)));
    els.playerSummary.appendChild(tags);
  }

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
  els.difficultySelect.value = state.playerState?.difficulty || "困难";
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
}

function renderSuggestions() {
  els.suggestionList.replaceChildren();
  els.suggestions.hidden = state.suggestions.length === 0 || Boolean(activePending());
  state.suggestions.forEach((preview) => {
    const button = node("button", "suggestion");
    button.append(node("strong", "", preview.label), node("small", "", `${preview.level} · ${preview.risk}`));
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
      activeSave: state.activeSave,
      saves: state.saves,
      playerState: state.playerState,
    }));
  } catch (_error) {
    // The cache is only a refresh-time display fallback.
  }
}

function restorePlayerShell() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(PLAYER_SHELL_CACHE_KEY) || "null");
    if (!cached?.playerState) return false;
    state.activeSave = cached.activeSave || null;
    state.saves = cached.saves || [];
    state.playerState = cached.playerState;
    state.suggestions = cached.playerState.action_preview?.previews || [];
    state.selectedPendingIndex = Math.max(0, (activePending()?.variants?.length || 1) - 1);
    const confirmationInFlight = sessionStorage.getItem(CONFIRMATION_IN_FLIGHT_KEY) === "1";
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
  renderSaves();
  renderControls();

  const recoverableOperations = resumeOperation
    ? (await request("/api/player/operations/recoverable")).operations || []
    : [];
  const firstOperation = recoverableOperations[0] || null;
  const preserveCachedState = recoverableOperations.some(
    (operation) => ["confirmation", "initialization"].includes(
      operation.recovery_kind,
    ),
  );
  const playerState = preserveCachedState && state.playerState
    ? state.playerState
    : await request("/api/player/state");
  if (firstOperation?.recovery_kind !== "confirmation") {
    sessionStorage.removeItem(CONFIRMATION_IN_FLIGHT_KEY);
  }
  state.playerState = playerState;
  state.suggestions = playerState.action_preview?.previews || [];
  state.selectedPendingIndex = Math.max(0, (activePending()?.variants?.length || 1) - 1);
  setMode(
    firstOperation
      ? firstOperation.recovery_kind === "confirmation" ? "confirming" : "generating"
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

function startStreaming(label = "世界正在回应") {
  streamBuffer = "";
  followStream = true;
  if (streamFrame) window.cancelAnimationFrame(streamFrame);
  streamFrame = 0;
  els.streamingText.replaceChildren();
  els.streamingLabel.textContent = label;
  els.streamingCard.hidden = false;
  els.pendingCard.hidden = true;
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

function stopStreaming() { flushStreamingText(); els.streamingCard.hidden = true; }

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
  if (event.step === "operation_events_truncated") {
    els.streamingLabel.textContent = "正在恢复最新生成进度";
  }
  setProgress(event.step);
  if (event.step === "scene_stream_started") startStreaming(
    event.data?.revision
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
  const retryMode = ["rephrase", "reroll"].includes(operation.operation_type)
    ? operation.operation_type
    : null;
  const userInput = operation.metadata?.user_input || "";
  setMode("generating");
  renderControls();
  showActivePlayerAction(userInput);
  startStreaming(
    retryMode === "rephrase"
      ? "正在恢复重新措辞"
      : retryMode === "reroll"
        ? "正在恢复重新判定"
        : "正在恢复生成进度",
  );
  try {
    await subscribe(
      `/api/player/operations/${encodeURIComponent(operation.operation_id)}/events?after=0`,
      (event) => handleGenerationEvent(event, { retryMode }),
    );
    await acknowledgeHandledOperation(operation.operation_id);
    stopStreaming();
    if (retryMode) showToast("新版本已生成，旧版本仍然保留");
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
    if (pending) clearDrafts(pending);
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

async function generateTurn() {
  const userInput = els.playerInput.value.trim();
  if (!userInput || state.mode !== "idle") return;
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
    stopStreaming();
    await loadAll({ scroll: "smooth" });
  } catch (error) {
    stopStreaming(); showActivePlayerAction("");
    els.playerInput.value = userInput; autoSizeInput();
    showError(error);
    await acknowledgeFailedOperation(error);
  }
}

async function retryTurn(retryMode) {
  const pending = activePending(); const variant = selectedVariant();
  if (!pending || !variant || pending.kind === "opening") return;
  const rephrase = retryMode === "rephrase";
  const endpoint = rephrase ? "rephrase" : "reroll";
  setMode("generating"); renderControls();
  startStreaming(rephrase ? "正在重新措辞" : "正在重新判定");
  try {
    const operationId = await stream(`/api/player/${endpoint}`, {
      turn_id: variant.turn_id,
      request_id: newRequestId(),
    }, (event) => handleGenerationEvent(event, { retryMode }));
    await acknowledgeHandledOperation(operationId);
    stopStreaming(); await loadAll({ scroll: "smooth" }); showToast("新版本已生成，旧版本仍然保留");
  } catch (error) {
    stopStreaming(); setMode("pending"); renderAll(); showError(error);
    await acknowledgeFailedOperation(error);
  }
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
    clearDrafts(pending); await loadAll({ scroll: "smooth" }); showToast("这一回合已经写入旅程");
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
  if (!pending || !variant || !window.confirm("删除全部未确认版本，并回到输入行动之前？")) return;
  setMode("switching"); renderControls();
  try {
    const result = await request("/api/player/pending/discard", { method: "POST", body: JSON.stringify({ turn_id: variant.turn_id }) });
    clearDrafts(pending); await loadAll(); els.playerInput.value = result.user_input || ""; autoSizeInput(); renderControls(); els.playerInput.focus(); showToast("未确认内容已删除");
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

async function switchSave(saveId) {
  if (!saveId || saveId === state.activeSave) return;
  const hasPending = Boolean(activePending());
  if (hasPending && !window.confirm("当前存档有未确认内容。切换后这些内容会被删除，是否继续？")) { els.saveSelect.value = state.activeSave; return; }
  setMode("switching"); renderControls();
  try {
    await request("/api/player/saves/switch", { method: "POST", body: JSON.stringify({ save_id: saveId, discard_pending: hasPending }) });
    state.suggestions = []; await loadAll({ scroll: "instant" }); closeDrawer();
  } catch (error) { els.saveSelect.value = state.activeSave; showError(error); }
}

async function createSave(name) {
  const hasPending = Boolean(activePending());
  if (hasPending && !window.confirm("创建并切换存档会删除当前未确认内容，是否继续？")) return;
  setMode("switching"); renderControls();
  await request("/api/player/saves/create", { method: "POST", body: JSON.stringify({ name, activate: true, discard_pending: hasPending }) });
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

els.playerInput.addEventListener("input", () => { autoSizeInput(); renderControls(); });
els.playerInput.addEventListener("focus", autoSizeInput);
els.playerInput.addEventListener("blur", () => { els.playerInput.style.height = ""; });
els.playerInput.addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); generateTurn(); } });
els.generateBtn.onclick = generateTurn;
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
els.undoBtn.onclick = () => changeHistory("undo"); els.redoBtn.onclick = () => changeHistory("redo");
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

toggleInitWorldFields();
bootstrap().catch(showError);
