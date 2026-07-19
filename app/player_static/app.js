import { request, stream } from "./api.js";
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
  streamingCard: $("streamingCard"), streamingText: $("streamingText"), streamingLabel: $("streamingLabel"),
  pendingCard: $("pendingCard"), suggestions: $("suggestions"), suggestionList: $("suggestionList"),
  errorCard: $("errorCard"), errorMessage: $("errorMessage"), dismissErrorBtn: $("dismissErrorBtn"),
  playerInput: $("playerInput"), generateBtn: $("generateBtn"), inputHint: $("inputHint"),
  drawer: $("drawer"), drawerBackdrop: $("drawerBackdrop"), openDrawerBtn: $("openDrawerBtn"), closeDrawerBtn: $("closeDrawerBtn"),
  saveSelect: $("saveSelect"), newSaveBtn: $("newSaveBtn"), playerSummary: $("playerSummary"), resourceSummary: $("resourceSummary"),
  difficultySelect: $("difficultySelect"), undoBtn: $("undoBtn"), redoBtn: $("redoBtn"),
  saveDialog: $("saveDialog"), saveForm: $("saveForm"), saveNameInput: $("saveNameInput"), saveCancelBtn: $("saveCancelBtn"),
  initDialog: $("initDialog"), initForm: $("initForm"), initSubmitBtn: $("initSubmitBtn"), initError: $("initError"),
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
};
let streamBuffer = "";
let streamFrame = 0;
let followStream = true;
const HISTORY_PAGE_SIZE = 5;

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
  setMode(state.playerState?.needs_init ? "needs_init" : pending ? "pending" : "idle");
  renderControls();
}

function renderControls() {
  const busy = ["booting", "generating", "confirming", "switching"].includes(state.mode);
  const pending = Boolean(activePending());
  els.statusPill.dataset.mode = state.mode;
  els.statusPill.querySelector("span").textContent = modeLabels[state.mode] || "等待行动";
  els.playerInput.disabled = busy || pending || state.playerState?.needs_init;
  els.generateBtn.disabled = busy || pending || state.playerState?.needs_init || !els.playerInput.value.trim();
  els.saveSelect.disabled = busy;
  els.newSaveBtn.disabled = busy;
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

  if (pending.user_input) els.pendingCard.appendChild(node("div", "player-action", pending.user_input));
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
  const edit = node("button", "soft-button", "编辑正文");
  const retry = node("button", "soft-button", "重新生成");
  const discard = node("button", "soft-button danger-button", "删除");
  const confirm = node("button", "primary-action", "确认采用");
  edit.onclick = () => beginEdit(display, closing, actions, variant);
  retry.disabled = pending.kind === "opening" || ["generating", "confirming"].includes(state.mode);
  retry.onclick = retryTurn;
  discard.onclick = discardPending;
  confirm.onclick = confirmTurn;
  actions.append(edit, retry);
  if (pending.kind !== "opening") actions.appendChild(discard);
  actions.appendChild(confirm);
  els.pendingCard.append(toolbar, display);
  if (closing) els.pendingCard.appendChild(closing);
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
    option.value = save.name;
    option.selected = save.name === state.activeSave;
    els.saveSelect.appendChild(option);
  });
}

function renderSuggestions() {
  els.suggestionList.replaceChildren();
  els.suggestions.hidden = state.suggestions.length === 0 || Boolean(activePending());
  state.suggestions.forEach((preview) => {
    const button = node("button", "suggestion");
    button.append(node("strong", "", preview.label), node("small", "", `${preview.level} · ${preview.risk}`));
    button.onclick = () => { els.playerInput.value = preview.label; autoSizeInput(); renderControls(); els.playerInput.focus(); };
    els.suggestionList.appendChild(button);
  });
}

function renderAll() {
  renderHeader(); renderHistory(); renderPending(); renderDrawer(); renderSaves(); renderSuggestions(); renderControls();
}

async function loadAll({ scroll = false } = {}) {
  state.historyPagingEnabled = false;
  const [saves, playerState] = await Promise.all([
    request("/api/player/saves"), request("/api/player/state"),
  ]);
  state.saves = saves.saves || [];
  state.activeSave = saves.active;
  state.playerState = playerState;
  state.selectedPendingIndex = Math.max(0, (activePending()?.variants?.length || 1) - 1);
  setMode(playerState.needs_init ? "needs_init" : activePending() ? "pending" : "idle");
  renderAll();
  if (playerState.needs_init && !els.initDialog.open) els.initDialog.showModal();
  if (scroll) scrollToBottom(scroll, () => { state.historyPagingEnabled = true; });
  else window.requestAnimationFrame(() => { state.historyPagingEnabled = true; });
}

function setProgress(step) {
  const label = progressLabels[step];
  if (label) els.streamingLabel.textContent = label;
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

async function generateTurn() {
  const userInput = els.playerInput.value.trim();
  if (!userInput || state.mode !== "idle") return;
  clearError(); setMode("generating"); state.suggestions = []; renderSuggestions(); renderControls();
  startStreaming("正在理解你的行动");
  try {
    await stream("/api/player/generate", { user_input: userInput, difficulty: els.difficultySelect.value }, (event) => {
      setProgress(event.step);
      if (event.step === "scene_stream_started") startStreaming(
        event.data?.revision ? "正在根据问题断言重新编写" : "世界正在回应"
      );
      if (event.step === "scene_text_delta") appendStreamingText(event.data?.delta || "");
    });
    els.playerInput.value = ""; autoSizeInput(); stopStreaming();
    await loadAll({ scroll: "smooth" });
  } catch (error) {
    stopStreaming(); showError(error);
  }
}

async function retryTurn() {
  const pending = activePending(); const variant = selectedVariant();
  if (!pending || !variant || pending.kind === "opening") return;
  setMode("generating"); renderControls(); startStreaming("正在重新推演这一回合");
  try {
    await stream("/api/player/retry", { turn_id: variant.turn_id }, (event) => {
      setProgress(event.step);
      if (event.step === "scene_stream_started") startStreaming(
        event.data?.revision ? "正在根据问题断言重新编写" : "正在重新推演这一回合"
      );
      if (event.step === "scene_text_delta") appendStreamingText(event.data?.delta || "");
    });
    stopStreaming(); await loadAll({ scroll: "smooth" }); showToast("新版本已生成，旧版本仍然保留");
  } catch (error) { stopStreaming(); setMode("pending"); renderAll(); showError(error); }
}

async function confirmTurn() {
  const pending = activePending(); const variant = selectedVariant();
  if (!pending || !variant) return;
  setMode("confirming"); renderControls();
  try {
    await stream("/api/player/confirm", { turn_id: variant.turn_id, scene_text: readDraft(variant) }, (event) => {
      setProgress(event.step);
      if (event.step === "action_preview_done" || event.step === "finish") {
        const previews = event.data?.action_preview?.previews;
        if (previews) state.suggestions = previews;
      }
    });
    clearDrafts(pending); await loadAll({ scroll: "smooth" }); showToast("这一回合已经写入旅程");
  } catch (error) { setMode("pending"); renderAll(); showError(error); }
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

async function switchSave(name) {
  if (!name || name === state.activeSave) return;
  const hasPending = Boolean(activePending());
  if (hasPending && !window.confirm("当前存档有未确认内容。切换后这些内容会被删除，是否继续？")) { els.saveSelect.value = state.activeSave; return; }
  setMode("switching"); renderControls();
  try {
    await request("/api/player/saves/switch", { method: "POST", body: JSON.stringify({ name, discard_pending: hasPending }) });
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

async function initialize(form) {
  const data = Object.fromEntries(new FormData(form));
  const payload = { ...data, fanfic: false, version: 1, protagonist_abilities: [], extra_core_memory_items: [] };
  setMode("generating"); els.initSubmitBtn.disabled = true; els.initSubmitBtn.textContent = "正在生成开场……"; els.initError.hidden = true;
  try { await request("/api/player/init", { method: "POST", body: JSON.stringify(payload) }); els.initDialog.close(); await loadAll({ scroll: "instant" }); }
  catch (error) { setMode("needs_init"); els.initError.textContent = error.message; els.initError.hidden = false; }
  finally { els.initSubmitBtn.disabled = false; els.initSubmitBtn.textContent = "生成开场"; renderControls(); }
}

function openDrawer() { els.drawer.classList.add("open"); els.drawer.setAttribute("aria-hidden", "false"); els.drawerBackdrop.hidden = false; }
function closeDrawer() { els.drawer.classList.remove("open"); els.drawer.setAttribute("aria-hidden", "true"); els.drawerBackdrop.hidden = true; }
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
els.openDrawerBtn.onclick = openDrawer; els.closeDrawerBtn.onclick = closeDrawer; els.drawerBackdrop.onclick = closeDrawer;
els.saveSelect.onchange = () => switchSave(els.saveSelect.value);
els.newSaveBtn.onclick = () => { els.saveNameInput.value = ""; els.saveDialog.showModal(); window.setTimeout(() => els.saveNameInput.focus(), 0); };
els.saveCancelBtn.onclick = () => els.saveDialog.close();
els.saveForm.addEventListener("submit", async (event) => { event.preventDefault(); const name = els.saveNameInput.value.trim(); if (!name) return; els.saveDialog.close(); try { await createSave(name); } catch (error) { showError(error); } });
els.difficultySelect.onchange = async () => { try { await request("/api/player/difficulty", { method: "POST", body: JSON.stringify({ difficulty: els.difficultySelect.value }) }); state.playerState.difficulty = els.difficultySelect.value; showToast("难度已更新"); } catch (error) { showError(error); } };
els.undoBtn.onclick = () => changeHistory("undo"); els.redoBtn.onclick = () => changeHistory("redo");
els.initForm.addEventListener("submit", (event) => { event.preventDefault(); initialize(els.initForm); });
els.initDialog.addEventListener("cancel", (event) => event.preventDefault());
window.addEventListener("scroll", () => {
  if (window.scrollY <= 220) loadEarlierHistory();
}, { passive: true });

loadAll({ scroll: "instant" }).catch(showError);
