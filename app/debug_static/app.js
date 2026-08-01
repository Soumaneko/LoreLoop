const $ = (id) => document.getElementById(id);

const els = {
  statePanel: $("statePanel"),
  messages: $("messages"),
  statusText: $("statusText"),
  debugStatus: $("debugStatus"),
  tabs: $("tabs"),
  debugBody: $("debugBody"),
  input: $("input"),
  generateBtn: $("generateBtn"),
  confirmBtn: $("confirmBtn"),
  writeDbToggle: $("writeDbToggle"),
  difficultySelect: $("difficultySelect"),
  maxCharsInput: $("maxCharsInput"),
  refreshBtn: $("refreshBtn"),
  saveSelect: $("saveSelect"),
  createSaveBtn: $("createSaveBtn"),
  undoBtn: $("undoBtn"),
  redoBtn: $("redoBtn"),
  turnInfo: $("turnInfo"),
};

const tabs = [
  ["overview", "总览"],
  ["jobs", "后台任务"],
  ["diff", "状态变化"],
  ["generation", "生成"],
  ["confirmation", "确认"],
  ["continuity", "Continuity"],
  ["entity", "实体"],
  ["npc", "NPC档案"],
  ["director", "导演"],
  ["preview", "行动预览"],
  ["raw", "Raw"],
];

const continuityKindLabels = {
  commitment: "承诺",
  open_thread: "未决事项",
};

const state = {
  activeTab: "overview",
  serverState: null,
  traces: {},
  traceRequests: {},
  pendingTurnId: null,
  pendingGroupId: null,
  pendingVariants: [],
  pendingVariantIndex: -1,
  pendingSceneText: null,
  chatRestored: false,
  coreMemoryInitialized: true,
  initConfigSnapshot: null,
  initCampaignSetup: null,
  operationStatus: "Ready",
  activeOperationId: null,
  saves: [],
  activeSave: null,
  run: emptyRun(),
};

const RESUMABLE_OPERATION_TYPES = new Set([
  "generation",
  "retry",
  "confirmation",
  "initialization_campaign_setup",
  "initialization_opening",
]);

function emptyRun() {
  return {
    generation: { order: [], steps: {}, merged: {} },
    confirmation: { order: [], steps: {}, merged: {} },
    maintenance: { order: [], steps: {}, merged: {} },
    initialization: { order: [], steps: {}, merged: {} },
    errors: [],
  };
}

function newRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `request-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function resetRun(scope = "all") {
  if (scope === "all") {
    state.run = emptyRun();
    return;
  }
  state.run[scope] = { order: [], steps: {}, merged: {} };
}

function recordStep(scope, event) {
  const bucket = state.run[scope];
  if (!bucket) return;
  const step = event.step || "unknown";
  const data = event.data || {};
  if (!bucket.steps[step]) bucket.order.push(step);
  bucket.steps[step] = { step, data, at: new Date().toLocaleTimeString() };
  Object.assign(bucket.merged, data);
  const traceId = data.trace_id || bucket.merged.trace_id;
  if (traceId) scheduleTraceLoad(traceId);
}

function step(scope, name) {
  return state.run[scope]?.steps?.[name]?.data || null;
}

function merged(scope) {
  return state.run[scope]?.merged || {};
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function text(value, fallback = "无") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function chips(items, fallback = "无") {
  if (!items || !items.length) return `<span class="empty">${esc(fallback)}</span>`;
  return `<div class="chips">${items.map((item) => `<span class="chip">${esc(item)}</span>`).join("")}</div>`;
}

function list(items, fallback = "无") {
  if (!items || !items.length) return `<div class="empty">${esc(fallback)}</div>`;
  return `<ul class="list">${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
}

function metrics(items) {
  return `<div class="metrics">${items.filter(Boolean).join("")}</div>`;
}

function metric(label, value, tone = "") {
  return `<span class="metric ${tone}">${esc(label)} ${esc(value)}</span>`;
}

function card(title, body, extra = "") {
  return `<section class="card ${extra}"><div class="card-title"><span>${esc(title)}</span></div>${body}</section>`;
}

function details(title, body, open = false) {
  return `<details class="flat-details" ${open ? "open" : ""}><summary>${esc(title)}</summary><div class="details-body">${body}</div></details>`;
}

function kv(rows) {
  return `<div class="kv">${rows.map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`).join("")}</div>`;
}

function jsonBlock(value) {
  return `<pre class="json-block">${esc(JSON.stringify(value ?? null, null, 2))}</pre>`;
}

function textBlock(value) {
  return `<pre class="json-block">${esc(text(value, ""))}</pre>`;
}

function money(resources) {
  if (!resources || resources.money === null || resources.money === undefined || resources.money === "") return "无";
  return `${resources.money}${resources.money_unit ? ` ${resources.money_unit}` : ""}`;
}

function inventory(items) {
  if (!items || !items.length) return `<span class="empty">无</span>`;
  return chips(items.map((item) => {
    if (typeof item === "string") return item;
    const name = item?.name || "";
    const qty = Number(item?.quantity || 1);
    return qty > 1 ? `${name} x${qty}` : name;
  }));
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json();
  if (!payload.ok) {
    const err = new Error(payload.error || "请求失败");
    err.payload = payload;
    throw err;
  }
  return payload.data;
}

function scheduleTraceLoad(traceId) {
  if (!traceId) return;
  window.clearTimeout(state.traceRequests[traceId]);
  state.traceRequests[traceId] = window.setTimeout(() => loadTrace(traceId), 250);
}

async function loadTrace(traceId) {
  try {
    const data = await api(`/api/traces?trace_id=${encodeURIComponent(traceId)}`);
    state.traces[traceId] = data.traces || [];
    renderDebug();
  } catch (err) {
    state.traces[traceId] = { error: err.message || "trace 加载失败" };
  }
}

async function readEventStream(response, scope, onEvent) {
  if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let operationId = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop();
    for (const part of parts) {
      if (!part.startsWith("data: ")) continue;
      const event = JSON.parse(part.slice(6));
      if (!event.ok) {
        event.operationId = operationId;
        throw event;
      }
      if (event.step === "done") {
        state.activeOperationId = null;
        await reader.cancel();
        return operationId;
      }
      if (event.step === "operation_started") {
        operationId = event.data?.operation_id || operationId;
        state.activeOperationId = operationId;
      }
      const transient = event.step === "scene_stream_started" || event.step === "scene_text_delta";
      if (!transient) recordStep(scope, event);
      onEvent(event);
      if (!transient) renderDebug();
    }
  }
  const error = new Error("Operation event stream ended before completion");
  error.operationId = operationId;
  throw error;
}

async function acknowledgeOperation(operationId) {
  if (!operationId) return;
  try {
    await api(`/api/operations/${encodeURIComponent(operationId)}/ack`, {
      method: "POST",
    });
  } catch (_error) {
    // Leave it recoverable so a later refresh can replay it again.
  }
}

function setStatus(label, busy = false) {
  state.operationStatus = label;
  els.statusText.textContent = label;
  els.generateBtn.disabled = busy || !state.coreMemoryInitialized || Boolean(state.pendingTurnId);
  els.confirmBtn.disabled = busy || !state.pendingTurnId;
  els.input.disabled = busy || !state.coreMemoryInitialized || Boolean(state.pendingTurnId);
  els.writeDbToggle.disabled = busy || !state.coreMemoryInitialized;
  els.saveSelect.disabled = busy;
  els.createSaveBtn.disabled = busy;
  const history = state.serverState?.turn_history || {};
  els.undoBtn.disabled = busy || Boolean(state.pendingTurnId) || !history.can_undo;
  const redoPendingIds = history.redo_pending_turn_ids || [];
  const pendingBelongsToRedo = state.pendingTurnId && redoPendingIds.includes(state.pendingTurnId);
  els.redoBtn.disabled = busy || (!pendingBelongsToRedo && Boolean(state.pendingTurnId)) || !history.can_redo;
  const previous = document.querySelector(".scene-version-prev");
  const next = document.querySelector(".scene-version-next");
  const retry = document.querySelector(".scene-retry");
  const discard = document.querySelector(".scene-discard");
  if (previous) previous.disabled = busy || state.pendingVariantIndex <= 0;
  if (next) next.disabled = busy || state.pendingVariantIndex >= state.pendingVariants.length - 1;
  if (retry) retry.disabled = busy;
  if (discard) discard.disabled = busy;
  const edit = document.querySelector(".scene-edit");
  if (edit) edit.disabled = busy;
}

function renderSaveOptions() {
  els.saveSelect.replaceChildren();
  for (const save of state.saves) {
    const option = document.createElement("option");
    option.value = save.name;
    option.textContent = `${save.name}${save.initialized ? "" : " · 未初始化"}`;
    option.selected = save.name === state.activeSave;
    els.saveSelect.appendChild(option);
  }
}

async function loadSaves() {
  const data = await api("/api/saves");
  state.saves = data.saves || [];
  state.activeSave = data.active || null;
  renderSaveOptions();
  return data;
}

function resetClientForSave() {
  state.serverState = null;
  state.traces = {};
  state.traceRequests = {};
  state.pendingTurnId = null;
  state.pendingGroupId = null;
  state.pendingVariants = [];
  state.pendingVariantIndex = -1;
  state.pendingSceneText = null;
  state.chatRestored = false;
  state.coreMemoryInitialized = true;
  state.activeTab = "overview";
  resetRun("all");
  els.messages.innerHTML = "";
  els.turnInfo.textContent = "正在加载存档";
  renderDebug();
}

function approvePendingDiscard() {
  if (!state.pendingTurnId) return true;
  return window.confirm("当前存档有尚未确认的内容。切换后这些内容会被丢弃，是否继续？");
}

async function switchSave(name) {
  if (!name || name === state.activeSave) return;
  const previous = state.activeSave;
  if (!approvePendingDiscard()) {
    els.saveSelect.value = previous || "";
    return;
  }
  const discardPending = Boolean(state.pendingTurnId);
  let switched = false;
  setStatus("正在切换存档...", true);
  try {
    await api("/api/saves/switch", {
      method: "POST",
      body: JSON.stringify({ name, discard_pending: discardPending }),
    });
    switched = true;
    resetClientForSave();
    await loadSaves();
    await refreshState();
  } catch (err) {
    if (switched) {
      await loadSaves().catch(() => {});
      setStatus("存档已切换，但状态加载失败");
    } else {
      els.saveSelect.value = previous || "";
      setStatus("存档切换失败");
    }
    window.alert(err.message || (switched ? "状态加载失败" : "存档切换失败"));
  }
}

async function createSave() {
  const rawName = window.prompt("请输入新存档名称（名称同时也是文件夹名）");
  if (rawName === null) return;
  const name = rawName.trim();
  if (!name) return;
  if (!approvePendingDiscard()) return;
  const discardPending = Boolean(state.pendingTurnId);
  let created = false;
  setStatus("正在创建存档...", true);
  try {
    await api("/api/saves/create", {
      method: "POST",
      body: JSON.stringify({ name, activate: true, discard_pending: discardPending }),
    });
    created = true;
    resetClientForSave();
    await loadSaves();
    await refreshState();
  } catch (err) {
    if (created) {
      await loadSaves().catch(() => {});
      setStatus("存档已创建，但状态加载失败");
    } else {
      setStatus("存档创建失败");
    }
    window.alert(err.message || (created ? "状态加载失败" : "存档创建失败"));
  }
}

async function changeHistory(action) {
  const history = state.serverState?.turn_history || {};
  const redoPendingIds = history.redo_pending_turn_ids || [];
  const canRedoRestoredPending = action === "redo" && redoPendingIds.includes(state.pendingTurnId);
  if (state.pendingTurnId && !canRedoRestoredPending) return;
  setStatus(action === "undo" ? "正在撤销回合..." : "正在重做回合...", true);
  try {
    await api(`/api/history/${action}`, { method: "POST", body: "{}" });
    resetClientForSave();
    await refreshState();
    setStatus(action === "undo" ? "已撤销上一回合" : "已重做回合");
  } catch (err) {
    setStatus(action === "undo" ? "撤销失败" : "重做失败");
    window.alert(err.message || "历史操作失败");
  }
}

function setDebugStatus(label) {
  els.debugStatus.textContent = label;
}

function addMessage(role, body, meta = "", closingPrompt = "") {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  wrap.innerHTML = `<div class="bubble">${meta ? `<div class="bubble-meta">${esc(meta)}</div>` : ""}${esc(body)}${closingPrompt ? `<div class="scene-closing-prompt">${esc(closingPrompt)}</div>` : ""}</div>`;
  els.messages.appendChild(wrap);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function restoreChat(history) {
  els.messages.innerHTML = "";
  const turns = history || [];
  for (const [index, turn] of turns.entries()) {
    if (turn.user_input) addMessage("user", turn.user_input, "玩家行动");
    const latestClosingPrompt = index === turns.length - 1 ? turn.closing_prompt || "" : "";
    addMessage(
      "assistant",
      turn.scene_text,
      `Turn ${turn.turn_index} · ${turn.scene_id}`,
      latestClosingPrompt,
    );
  }
}

function addEditableScene(textValue, meta, closingPrompt = "") {
  removeStreamingScene();
  removePendingScene();
  state.pendingSceneText = textValue;
  const wrap = document.createElement("div");
  wrap.className = "message assistant";
  wrap.id = "pendingSceneBubble";
  wrap.innerHTML = `
    <div class="bubble scene-bubble">
      <div class="bubble-meta">
        <span class="scene-meta">${esc(meta)}</span>
        ${pendingVersionControls()}
        <button class="scene-edit">编辑</button>
      </div>
      <div class="scene-display"></div>
      <div class="scene-closing-prompt"></div>
      <div class="scene-editor-wrap" style="display:none">
        <textarea class="scene-editor"></textarea>
        <div class="scene-actions">
          <button class="primary-button scene-save">保存</button>
          <button class="scene-cancel">取消</button>
        </div>
      </div>
    </div>
  `;
  const display = wrap.querySelector(".scene-display");
  const closing = wrap.querySelector(".scene-closing-prompt");
  const editorWrap = wrap.querySelector(".scene-editor-wrap");
  const editor = wrap.querySelector(".scene-editor");
  display.textContent = textValue;
  closing.textContent = closingPrompt;
  closing.style.display = closingPrompt ? "block" : "none";
  editor.value = textValue;
  const previous = wrap.querySelector(".scene-version-prev");
  const next = wrap.querySelector(".scene-version-next");
  const retry = wrap.querySelector(".scene-retry");
  const discard = wrap.querySelector(".scene-discard");
  if (previous) previous.onclick = () => selectPendingVariant(state.pendingVariantIndex - 1);
  if (next) next.onclick = () => selectPendingVariant(state.pendingVariantIndex + 1);
  if (retry) retry.onclick = retryTurn;
  if (discard) discard.onclick = discardPendingTurn;
  wrap.querySelector(".scene-edit").onclick = () => {
    editor.value = state.pendingSceneText || "";
    display.style.display = "none";
    editorWrap.style.display = "block";
    editor.focus();
  };
  wrap.querySelector(".scene-save").onclick = () => {
    state.pendingSceneText = editor.value;
    updateSelectedVariantText(state.pendingSceneText);
    display.textContent = editor.value;
    editorWrap.style.display = "none";
    display.style.display = "block";
  };
  wrap.querySelector(".scene-cancel").onclick = () => {
    editorWrap.style.display = "none";
    display.style.display = "block";
  };
  els.messages.appendChild(wrap);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function messagesNearBottom() {
  const distance = els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight;
  return distance <= 48;
}

function startStreamingScene(attempt, versionIndex = null, revision = false) {
  let wrap = document.getElementById("streamingSceneBubble");
  const resolvedVersion = versionIndex || Number(wrap?.dataset.versionIndex) || state.pendingVariants.length + 1;
  if (!wrap) {
    removePendingScene();
    wrap = document.createElement("div");
    wrap.className = "message assistant";
    wrap.id = "streamingSceneBubble";
    wrap.innerHTML = `
      <div class="bubble scene-bubble">
        <div class="bubble-meta">
          <span class="scene-meta"></span>
          <span class="scene-version-controls">
            <button class="icon-button" disabled>‹</button>
            <span class="streaming-version-position"></span>
            <button class="icon-button" disabled>›</button>
            <button disabled>Retry</button>
          </span>
        </div>
        <div class="scene-display"></div>
      </div>
    `;
    els.messages.appendChild(wrap);
  }
  wrap.dataset.versionIndex = resolvedVersion;
  wrap.querySelector(".scene-meta").textContent = revision
    ? (attempt > 1 ? `正在修订正文 · 第 ${attempt} 次尝试` : "正在根据问题断言修订正文")
    : (attempt > 1 ? `正在重新生成正文 · 第 ${attempt} 次尝试` : "正在生成正文");
  wrap.querySelector(".streaming-version-position").textContent = `${resolvedVersion} / ${resolvedVersion}`;
  wrap.querySelector(".scene-display").textContent = "";
  els.messages.scrollTop = els.messages.scrollHeight;
}

function appendStreamingScene(delta) {
  if (!delta) return;
  if (!document.getElementById("streamingSceneBubble")) startStreamingScene(1);
  const display = document.querySelector("#streamingSceneBubble .scene-display");
  const shouldFollow = messagesNearBottom();
  display.textContent += delta;
  if (shouldFollow) els.messages.scrollTop = els.messages.scrollHeight;
}

function removeStreamingScene() {
  document.getElementById("streamingSceneBubble")?.remove();
}

function removePendingScene() {
  document.getElementById("pendingSceneBubble")?.remove();
}

function markPendingSceneConfirmed() {
  const bubble = document.getElementById("pendingSceneBubble");
  if (!bubble) return;
  bubble.removeAttribute("id");
  bubble.querySelector(".scene-edit")?.remove();
  bubble.querySelector(".scene-editor-wrap")?.remove();
  bubble.querySelector(".scene-version-controls")?.remove();
  const meta = bubble.querySelector(".scene-meta");
  if (meta) meta.insertAdjacentHTML("afterend", ` <span class="scene-confirmed">已确认</span>`);
}

function getSceneText() {
  capturePendingSceneText();
  return state.pendingSceneText || null;
}

function pendingVersionControls() {
  if (!state.pendingGroupId || !state.pendingVariants.length) return "";
  const position = state.pendingVariantIndex + 1;
  return `<span class="scene-version-controls">
    <button class="icon-button scene-version-prev" title="上一个版本" ${position <= 1 ? "disabled" : ""}>‹</button>
    <span>${position} / ${state.pendingVariants.length}</span>
    <button class="icon-button scene-version-next" title="下一个版本" ${position >= state.pendingVariants.length ? "disabled" : ""}>›</button>
    <button class="scene-retry" title="重新运行完整生成管线">Retry</button>
    <button class="scene-discard" title="删除全部未确认版本并返回生成前">删除</button>
  </span>`;
}

function capturePendingSceneText() {
  const editor = document.querySelector("#pendingSceneBubble .scene-editor");
  const editorWrap = document.querySelector("#pendingSceneBubble .scene-editor-wrap");
  if (editor && editorWrap?.style.display !== "none") {
    state.pendingSceneText = editor.value;
  }
  updateSelectedVariantText(state.pendingSceneText);
}

function updateSelectedVariantText(sceneText) {
  const variant = state.pendingVariants[state.pendingVariantIndex];
  if (variant && sceneText != null) variant.scene_text = sceneText;
}

function selectPendingVariant(index) {
  if (index < 0 || index >= state.pendingVariants.length) return;
  capturePendingSceneText();
  state.pendingVariantIndex = index;
  const variant = state.pendingVariants[index];
  state.pendingTurnId = variant.turn_id;
  addEditableScene(
    variant.scene_text,
    `待确认 · Turn ${variant.turn_index} · 版本 ${index + 1}`,
    variant.closing_prompt || "",
  );
  els.turnInfo.textContent = `Pending ${state.pendingTurnId}`;
  setStatus("正文已生成 - 待确认");
}

function appendPendingVariant(data) {
  removeStreamingScene();
  state.pendingGroupId = data.group_id;
  state.pendingVariants.push({
    turn_id: data.turn_id,
    version_index: data.version_index,
    turn_index: data.turn_index,
    scene_text: data.scene_result.scene_text,
    closing_prompt: data.scene_result.closing_prompt,
  });
  state.pendingVariants.sort((a, b) => a.version_index - b.version_index);
  selectPendingVariant(
    state.pendingVariants.findIndex((variant) => variant.turn_id === data.turn_id),
  );
}

function readInitConfig() {
  const abilityNames = [...document.querySelectorAll(".init-ability-name")];
  const abilityDescriptions = [...document.querySelectorAll(".init-ability-description")];
  const abilities = abilityNames.map((el, index) => {
    const name = el.value.trim();
    const description = abilityDescriptions[index].value.trim();
    if (Boolean(name) !== Boolean(description)) throw new Error(`能力 ${index + 1} 的名称和解释需要同时填写`);
    return name ? { name, description } : null;
  }).filter(Boolean).slice(0, 3);
  const age = $("initAge")?.value.trim();
  return {
    version: 3,
    director_enabled: true,
    world_name: $("initWorldName")?.value.trim() || null,
    world_style: $("initWorldStyle")?.value.trim() || null,
    ability_system: $("initAbilitySystem")?.value || null,
    custom_ability_system: $("initCustomAbilitySystem")?.value.trim() || null,
    protagonist_gender: $("initGender")?.value || "男",
    protagonist_name: $("initName")?.value.trim() || "",
    protagonist_age: age ? Number(age) : null,
    protagonist_introduction: $("initBackground")?.value.trim() || null,
    protagonist_abilities: abilities,
    story_expectation: $("initStoryExpectation")?.value.trim() || "",
    failure_severity: $("initFailureSeverity")?.value || "适中",
  };
}

function renderInitPanel() {
  state.coreMemoryInitialized = false;
  state.initConfigSnapshot = null;
  state.initCampaignSetup = null;
  setStatus("需要初始化 Core Memory");
  els.turnInfo.textContent = "未初始化";
  els.statePanel.innerHTML = `
    ${card("初始化", `<div class="empty">生成并编辑公开世界惯例，提交后再生成私有开局基础与开场。</div>`)}
    <section class="card init-grid">
      <input id="initName" placeholder="主角姓名" />
      <select id="initGender"><option>男</option><option>女</option><option>其他</option></select>
      <input id="initAge" type="number" min="0" placeholder="年龄，可选" />
      <textarea id="initBackground" placeholder="人物介绍，可选"></textarea>
      <input id="initWorldName" placeholder="原创世界名" />
      <input id="initWorldStyle" placeholder="世界风格" />
      <select id="initAbilitySystem">
        <option value="">能力体系，可选</option>
        <option value="无超自然">无超自然</option>
        <option value="低超自然">低超自然</option>
        <option value="魔法">魔法</option>
        <option value="灵能">灵能</option>
        <option value="科技奇迹">科技奇迹</option>
        <option value="自定义">自定义</option>
      </select>
      <input id="initCustomAbilitySystem" placeholder="自定义能力体系，仅选择自定义时填写" />
      <input class="init-ability-name" placeholder="能力 1 名称" />
      <input class="init-ability-description" placeholder="能力 1 解释" />
      <input class="init-ability-name" placeholder="能力 2 名称" />
      <input class="init-ability-description" placeholder="能力 2 解释" />
      <input class="init-ability-name" placeholder="能力 3 名称" />
      <input class="init-ability-description" placeholder="能力 3 解释" />
      <textarea id="initStoryExpectation" placeholder="希望长期体验的故事"></textarea>
      <select id="initFailureSeverity"><option>幻想世界</option><option selected>适中</option><option>真实世界</option></select>
      <button id="initPreviewBtn">预览</button>
      <button id="initSetupBtn" class="primary-button">生成公开世界惯例</button>
      <textarea id="initSetupJson" placeholder="生成后可编辑公开世界惯例 JSON" disabled></textarea>
      <button id="initSaveBtn" class="primary-button" disabled>确认惯例并生成开场</button>
      <pre id="initPreview" class="json-block">等待预览...</pre>
    </section>
  `;
  $("initPreviewBtn").onclick = previewInit;
  $("initSetupBtn").onclick = generateInitSetup;
  $("initSaveBtn").onclick = applyInit;
  const committed = state.serverState?.initialization;
  if (committed?.config && committed?.campaign_setup) {
    state.initConfigSnapshot = committed.config;
    state.initCampaignSetup = committed.campaign_setup;
    $("initSetupJson").disabled = true;
    $("initSetupJson").value = JSON.stringify(committed.campaign_setup, null, 2);
    $("initSetupBtn").disabled = true;
    $("initSaveBtn").disabled = false;
    $("initPreview").textContent = `初始化阶段：${committed.phase}\n公开 Proposal 已提交。`;
  }
}

async function previewInit() {
  const box = $("initPreview");
  try {
    const data = await api("/api/init/preview", { method: "POST", body: JSON.stringify(readInitConfig()) });
    box.textContent = data.valid ? JSON.stringify(data.identity_core_memory.items, null, 2) : JSON.stringify(data.validation_errors || [], null, 2);
  } catch (err) {
    box.textContent = err.message || "预览失败";
  }
}

async function applyInit() {
  const btn = $("initSaveBtn");
  btn.disabled = true;
  try {
    const proposal = JSON.parse($("initSetupJson").value);
    if (!state.initConfigSnapshot || !proposal) throw new Error("请先生成并确认公开世界惯例");
    const response = await fetch("/api/init/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        config: state.initConfigSnapshot,
        campaign_setup: proposal,
        request_id: newRequestId(),
      }),
    });
    resetRun("initialization");
    const operationId = await readEventStream(
      response,
      "initialization",
      handleInitializationPipelineEvent,
    );
    await refreshState({ preserveStatus: true });
    await acknowledgeOperation(operationId);
    setStatus(state.pendingTurnId ? "开场已生成 - 待确认" : "Core Memory 已初始化");
    els.turnInfo.textContent = state.pendingTurnId ? `Pending ${state.pendingTurnId}` : "等待玩家行动";
  } catch (err) {
    btn.disabled = false;
    $("initPreview").textContent = err.message || "保存失败";
    await acknowledgeOperation(err?.operationId);
  }
}

async function generateInitSetup() {
  const btn = $("initSetupBtn");
  btn.disabled = true;
  try {
    const response = await fetch("/api/init/campaign-setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...readInitConfig(),
        request_id: newRequestId(),
      }),
    });
    resetRun("initialization");
    const operationId = await readEventStream(
      response,
      "initialization",
      handleInitializationPipelineEvent,
    );
    await acknowledgeOperation(operationId);
  } catch (err) {
    $("initPreview").textContent = err.message || "公开世界惯例生成失败";
    await acknowledgeOperation(err?.operationId);
  } finally {
    btn.disabled = false;
  }
}

function handleInitializationPipelineEvent(event) {
  const data = event.data || {};
  if (event.step === "campaign_setup_done") {
    state.initConfigSnapshot = data.config;
    state.initCampaignSetup = data.campaign_setup;
    $("initSetupJson").disabled = false;
    $("initSetupJson").value = JSON.stringify(data.campaign_setup, null, 2);
    $("initSaveBtn").disabled = false;
    $("initPreview").textContent = JSON.stringify(data.campaign_setup, null, 2);
  }
  if (event.step === "initialization_pending") {
    state.coreMemoryInitialized = true;
    state.pendingTurnId = data.turn_id || null;
    state.pendingGroupId = null;
    state.pendingVariants = [];
    state.pendingVariantIndex = -1;
  }
}

function renderStatePanel() {
  const data = state.serverState;
  if (!data || data.needs_init) {
    renderInitPanel();
    return;
  }
  state.coreMemoryInitialized = true;
  const current = data.current_state || {};
  const scene = current.scene || {};
  const player = current.player || {};
  const resources = current.resources || {};
  const participants = scene.participants || [];
  const activeDirectorEvents = data.active_director_events || [];
  const activeArcEvent = activeDirectorEvents.find((event) => event.role === "arc");
  const activeAmbientEvent = activeDirectorEvents.find((event) => event.role === "ambient");
  const preparedDirectorNpcs = data.prepared_director_npcs || [];
  const introducedDirectorNpcs = data.introduced_director_npcs || [];
  const continuityItems = data.continuity_items || [];
  const activeContinuity = continuityItems.filter((item) => item.status === "active");
  const summaries = data.scene_summaries || [];
  const recentSummaries = data.recent_summaries || [];
  const coreItems = data.core_memory?.items || [];
  const jobs = data.maintenance_jobs || [];
  const jobCounts = data.maintenance_job_status_counts || {};
  els.statePanel.innerHTML = `
    ${card("当前场景", kv([
      ["场景", esc(text(scene.scene_id))],
      ["地点", esc(text(scene.location))],
      ["时间", esc(`第${current.world_clock?.day || "?"}日 ${current.world_clock?.phase || "?"}`)],
      ["在场", chips(participantNames(participants))],
    ]))}
    ${card("主角", kv([
      ["姓名", esc(text(player.name))],
      ["外观", esc(text(player.visible_status))],
    ]))}
    ${card("资源", kv([
      ["金钱", esc(money(resources))],
      ["物品", inventory(resources.inventory)],
      ["资产", chips(resources.fixed_assets)],
      ["临时", chips(resources.temporary_assets)],
    ]))}
    ${details(`Core Memory (${coreItems.length})`, coreItems.map(coreMemoryItemCard).join("") || `<div class="empty">暂无 Core Memory</div>`)}
    ${details(`在场人物 (${participants.length})`, participants.length ? participants.map(participantCard).join("") : `<div class="empty">当前无人</div>`)}
    ${details(`NPC 档案 (${data.npc_active_profile_count ?? 0}/${data.npc_profile_count ?? 0})`, renderNpcStateGroup(data.npc_profiles || []))}
    ${details("导演", `
      ${metrics([
        metric("事件", activeDirectorEvents.length, activeDirectorEvents.length ? "ok" : ""),
        metric("Arc", activeArcEvent ? "active" : "none", activeArcEvent ? "ok" : ""),
        metric("Ambient", activeAmbientEvent ? "active" : "none", activeAmbientEvent ? "ok" : ""),
        metric("已准备NPC", preparedDirectorNpcs.length, preparedDirectorNpcs.length ? "info" : ""),
        metric("已引入NPC", introducedDirectorNpcs.length, introducedDirectorNpcs.length ? "ok" : ""),
      ])}
      ${activeDirectorEvents.length ? sortDirectorEvents(activeDirectorEvents).map(directorCard).join("") : `<div class="empty">暂无 active 事件</div>`}
      ${preparedDirectorNpcs.slice(0, 3).map(directorNpcCard).join("")}
    `)}
    ${details(`场景摘要 (${summaries.length})`, `
      ${metrics([
        metric("活跃", summaries.length),
        metric("最近", recentSummaries.length, "info"),
      ])}
      ${(recentSummaries.length ? recentSummaries : summaries.slice(0, 8)).map(sceneSummaryCard).join("") || `<div class="empty">暂无场景摘要</div>`}
    `)}
    ${details(`实体档案 (${(data.entity_profiles || []).length})`, renderEntityGroups(data.entity_profiles || []))}
    ${details(`Continuity (${activeContinuity.length}/${continuityItems.length})`, activeContinuity.map(continuityItemCard).join("") || `<div class="empty">暂无活跃事项</div>`)}
    ${details(`后台任务 (${jobs.length})`, `
      ${metrics([
        metric("排队", jobCounts.queued || 0, jobCounts.queued ? "info" : ""),
        metric("运行", jobCounts.running || 0, jobCounts.running ? "warn" : ""),
        metric("失败", jobCounts.failed || 0, jobCounts.failed ? "bad" : ""),
      ])}
      ${jobs.slice(0, 5).map(maintenanceJobCard).join("") || `<div class="empty">暂无后台任务</div>`}
    `, (jobCounts.queued || 0) + (jobCounts.running || 0) + (jobCounts.failed || 0) > 0)}
  `;
}

function coreMemoryItemCard(item) {
  return `<div class="section-card">
    <div class="card-title"><span>${esc(item.category || "core")}</span>${metric("priority", item.priority ?? 100)}</div>
    <div>${esc(text(item.content))}</div>
    <div class="muted" style="margin-top:6px">${esc(text(item.id))}</div>
  </div>`;
}

function participantNames(participants) {
  return (participants || []).map((participant) => typeof participant === "string" ? participant : participant?.name).filter(Boolean);
}

function participantCard(participant) {
  if (typeof participant === "string") participant = { name: participant };
  return `<div class="section-card">${kv([
    ["姓名", esc(text(participant.name))],
    ["对玩家态度", esc(text(participant.attitude_to_player))],
  ])}</div>`;
}

function renderTabs() {
  els.tabs.innerHTML = tabs.map(([id, label]) => `<button class="tab ${state.activeTab === id ? "active" : ""}" data-tab="${id}">${esc(label)}</button>`).join("");
  els.tabs.querySelectorAll(".tab").forEach((btn) => {
    btn.onclick = () => {
      state.activeTab = btn.dataset.tab;
      renderDebug();
    };
  });
}

function renderDebug() {
  renderTabs();
  const renderers = {
    overview: renderOverview,
    jobs: renderJobs,
    diff: renderStateDiff,
    generation: renderGeneration,
    confirmation: renderConfirmation,
    continuity: renderContinuity,
    entity: renderEntityDebug,
    npc: renderNpcDebug,
    director: renderDirectorDebug,
    preview: renderPreview,
    raw: renderRaw,
  };
  els.debugBody.innerHTML = (renderers[state.activeTab] || renderOverview)();
  $("refreshJobsBtn")?.addEventListener("click", refreshMaintenanceJobs);
}

function renderOverview() {
  const gen = state.run.generation;
  const conf = state.run.confirmation;
  const maint = state.run.maintenance;
  const finish = step("confirmation", "finish");
  const scene = step("generation", "scene_written");
  const jobCounts = state.serverState?.maintenance_job_status_counts || {};
  return `
    ${card("运行总览", `
      ${metrics([
        metric("生成", gen.order.length, gen.order.length ? "info" : ""),
        metric("确认", conf.order.length, finish ? "ok" : ""),
        metric("维护", maint.order.length, maint.order.length ? "warn" : ""),
        metric("后台运行", (jobCounts.queued || 0) + (jobCounts.running || 0), (jobCounts.failed || 0) ? "bad" : "info"),
        jobCounts.failed ? metric("后台失败", jobCounts.failed, "bad") : "",
      ])}
      ${kv([
        ["Trace", esc(text(scene?.trace_id || finish?.trace_id || step("generation", "state_loaded")?.trace_id))],
        ["Turn", esc(text(scene?.turn_index || finish?.turn_index))],
        ["Pending", esc(text(state.pendingTurnId))],
        ["状态", esc(state.operationStatus)],
      ])}
    `)}
    ${timeline("generation", "生成流程")}
    ${timeline("confirmation", "确认流程")}
    ${timeline("maintenance", "维护流程")}
  `;
}

function renderJobs() {
  const server = state.serverState || {};
  const jobs = server.maintenance_jobs || [];
  const counts = server.maintenance_job_status_counts || {};
  const active = jobs.filter((job) => job.status === "queued" || job.status === "running");
  const failed = jobs.filter((job) => job.status === "failed");
  return `
    ${card("后台任务状态", `
      ${metrics([
        metric("排队", counts.queued || 0, counts.queued ? "info" : ""),
        metric("运行", counts.running || 0, counts.running ? "warn" : ""),
        metric("成功", counts.succeeded || 0, "ok"),
        metric("失败", counts.failed || 0, counts.failed ? "bad" : ""),
        metric("覆盖", counts.superseded || 0),
      ])}
      <div class="button-row" style="margin-top:10px">
        <button id="refreshJobsBtn">刷新任务</button>
      </div>
    `)}
    ${active.length ? card("进行中", active.map(maintenanceJobCard).join("")) : ""}
    ${failed.length ? card("失败", failed.map(maintenanceJobCard).join(""), "bad") : ""}
    ${details(`任务历史 (${jobs.length})`, jobs.map(maintenanceJobCard).join("") || `<div class="empty">暂无后台任务。</div>`, !active.length && !failed.length)}
  `;
}

function maintenanceJobCard(job) {
  const tone = job.status === "succeeded" ? "ok" : job.status === "failed" ? "bad" : job.status === "running" ? "warn" : job.status === "queued" ? "info" : "";
  const error = job.error_message ? `<div class="job-error">${esc(`${job.error_type || "Error"}: ${job.error_message}`)}</div>` : "";
  return `<div class="section-card job-card">
    <div class="card-title"><span>${esc(job.job_type || "maintenance")}</span>${metric(job.status || "unknown", job.turn_index == null ? "" : `T${job.turn_index}`, tone)}</div>
    ${kv([
      ["Job ID", `<code>${esc(text(job.job_id))}</code>`],
      ["Trace", `<code>${esc(text(job.trace_id))}</code>`],
      ["提交", esc(formatTimestamp(job.submitted_at))],
      ["开始", esc(formatTimestamp(job.started_at))],
      ["结束", esc(formatTimestamp(job.finished_at))],
      ["重跑自", job.rerun_of ? `<code>${esc(job.rerun_of)}</code>` : `<span class="empty">无</span>`],
    ])}
    ${error}
    ${Object.keys(job.metadata || {}).length ? details("Metadata", jsonBlock(job.metadata)) : ""}
  </div>`;
}

function formatTimestamp(value) {
  if (!value) return "无";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function timeline(scope, title) {
  const run = state.run[scope];
  if (!run.order.length) return card(title, `<div class="empty">暂无事件</div>`);
  return card(title, `<div class="timeline">${run.order.map((name) => {
    const ev = run.steps[name];
    const traceBody = renderStepTrace(scope, name, ev.data);
    return `
      <details class="flat-details">
        <summary>
          <div class="timeline-row">
            <span class="dot"></span>
            <span class="timeline-name">${esc(name)}</span>
            <span class="timeline-meta">${esc(ev.at)} · ${esc(stepSummary(name, ev.data))}</span>
          </div>
        </summary>
        <div class="details-body">
          ${traceBody || `<div class="empty">这个步骤没有对应的 LLM 调用。</div>`}
          ${details("事件数据", jsonBlock(ev.data))}
        </div>
      </details>
    `;
  }).join("")}</div>`);
}

function renderStepTrace(scope, stepName, data = {}) {
  const traceId = data.trace_id || merged(scope).trace_id;
  if (!traceId) return "";
  const traces = state.traces[traceId];
  if (!traces) return `<div class="empty">Trace ${esc(traceId)} 加载中...</div>`;
  if (traces.error) return `<div class="empty">${esc(traces.error)}</div>`;
  const matched = tracesForStep(scope, stepName, traces);
  if (!matched.length) return `<div class="empty">Trace ${esc(traceId)} 中暂无此步骤的 LLM 记录。</div>`;
  return matched.map(traceCard).join("");
}

function tracesForStep(scope, stepName, traces) {
  const tasks = stepTasks(scope, stepName);
  if (!tasks.length) return [];
  if (tasks.includes("*")) return traces;
  const matched = traces.filter((trace) => tasks.includes(trace.task));
  if (scope !== "generation") return matched;
  if (stepName === "outline_done") return matched.filter((trace) => !isRevisionComposerTrace(trace));
  if (stepName === "narrative_revision_started" || stepName === "narrative_revised") {
    return matched.filter(isRevisionComposerTrace);
  }
  if (stepName === "scene_written") {
    const revised = matched.filter(isRevisionComposerTrace);
    return revised.length ? revised : matched.filter((trace) => !isRevisionComposerTrace(trace));
  }
  return matched;
}

function isRevisionComposerTrace(trace) {
  const request = trace?.input_data?.revision_request_json;
  if (request && typeof request === "object") return Object.keys(request).length > 0;
  if (typeof request !== "string") return false;
  const normalized = request.trim();
  return normalized !== "" && normalized !== "null" && normalized !== "{}";
}

function stepTasks(scope, stepName) {
  const generation = {
    entity_profiles_loaded: [],
    context_facts_done: ["context_extract"],
    context_extract_failed: ["context_extract"],
    action_parsed: ["action_extract"],
    modifiers_done: ["roll_modifier"],
    turn_resolution_done: ["turn_resolution"],
    director_agent_staged: ["director_agent_decide_generation"],
    outline_done: ["narrative_compose"],
    canon_query_started: ["narrative_canon_query"],
    canon_queries_generated: ["narrative_canon_query"],
    canon_evidence_retrieved: [],
    narrative_check_started: ["narrative_consistency_check"],
    narrative_checked: ["narrative_consistency_check"],
    narrative_revision_started: ["narrative_compose"],
    narrative_revised: ["narrative_compose"],
    scene_written: ["narrative_compose"],
  };
  const confirmation = {
    scene_facts_done: ["scene_fact_extract"],
    state_update_done: ["state_update"],
    continuity_projected: ["continuity_project"],
    entity_extract_done: ["entity_extract"],
    npc_extract_done: ["npc_profile_extract"],
    action_preview_done: ["action_preview"],
    entity_profiles_updated: ["entity_profile_update", "npc_profile_update"],
    director_agent_confirmed: [
      "director_agent_decide_confirmation",
      "director_event_review",
      "director_event_select_arc",
      "director_event_select_ambient",
      "director_npc_create",
      "setting_anchor_prepare",
    ],
    finish: ["*"],
  };
  const maintenance = {};
  return ({ generation, confirmation, maintenance }[scope] || {})[stepName] || [];
}

function traceCard(trace) {
  return `<div class="trace-card">
    <div class="trace-head">
      <div>
        <div class="trace-task">${esc(trace.task || "LLM")}</div>
        <div class="trace-meta">${esc(trace.model || "model?")} · ${esc(trace.prompt_version || "v?")} · attempt ${esc(trace.attempt || 1)}</div>
      </div>
      <div class="metrics">
        ${metric(trace.schema_valid ? "valid" : "invalid", trace.latency_ms ? `${trace.latency_ms}ms` : "", trace.schema_valid ? "ok" : "bad")}
        ${trace.token_usage ? metric("tokens", trace.token_usage.total_tokens ?? 0, "info") : ""}
      </div>
    </div>
    ${trace.error ? `<div class="empty">${esc(trace.error)}</div>` : ""}
    <div class="trace-grid">
      ${tracePane("Input", jsonBlock(trace.input_data))}
      ${tracePane("Raw Output", textBlock(trace.raw_output))}
      ${tracePane("Parsed Output", jsonBlock(trace.parsed_output))}
    </div>
  </div>`;
}

function tracePane(title, body) {
  return `<section class="trace-pane"><div class="trace-pane-title">${esc(title)}</div>${body}</section>`;
}

function stepSummary(name, data) {
  if (name === "action_parsed") return `${data.action_parse_result?.atomic_actions?.length || 0} actions`;
  if (name === "context_facts_done") return `${data.context_facts?.facts?.length || 0} facts`;
  if (name === "context_extract_failed") return "context extract failed";
  if (name === "director_agent_staged") return `${data.director_stage?.materials?.length || 0} materials`;
  if (name === "rolls_done") return `${data.roll_results?.total_rolls || 0} rolls`;
  if (name === "director_agent_confirmed") return `${data.director_executions?.executions?.length || 0} skills`;
  if (name === "canon_query_started") return "query planning";
  if (name === "canon_queries_generated") return `${data.canon_query_set?.queries?.length || 0} queries`;
  if (name === "canon_evidence_retrieved") {
    const evidence = data.canon_evidence_set || {};
    return `${data.queries_with_evidence || 0}/${evidence.results?.length || 0} matched · ${evidence.indexed_turns || 0} turns`;
  }
  if (name === "narrative_checked") {
    const review = data.narrative_consistency_review || {};
    const assertions = review.assertions?.length || 0;
    return assertions ? `${review.decision || "revise"} · ${assertions} assertions` : (review.decision || "pass");
  }
  if (name === "narrative_check_started") return "semantic audit";
  if (name === "narrative_revision_started") return "Composer revision";
  if (name === "narrative_revised") return "Composer retried";
  if (name === "scene_written") return `turn ${data.turn_index || "?"}`;
  if (name === "state_update_done") return "state updated";
  if (name === "scene_facts_done") return `${data.scene_facts?.facts?.length || 0} facts`;
  if (name === "continuity_projected") return `${data.continuity_projection?.operations?.length || 0} operations`;
  if (name === "entity_extract_done") return `${data.entity_extraction?.retrieved_entities?.length || 0} entities`;
  if (name === "npc_extract_done") return `${data.npc_extraction?.mentions?.length || 0} npcs`;
  if (name === "finish") return data.status || "done";
  return Object.keys(data || {}).join(", ") || "event";
}

function renderGeneration() {
  const g = merged("generation");
  if (!state.run.generation.order.length) return `<div class="empty">暂无生成流程。</div>`;
  return `
    ${renderContextFacts(g)}
    ${card("Director Agent", g.director_stage ? jsonBlock({ decision: g.director_decision, stage: g.director_stage }) : `<div class="empty">本轮未启用导演 Agent</div>`)}
    ${card("Action / Roll Branch", `
      ${details("Action Parse", renderAction(g.action_parse_result, g.raw_action_parse_result, g.action_self_check_result), true)}
      ${details("Roll", renderRoll(g.roll_results))}
    `)}
    ${card("Turn Resolution", renderResolution(g.turn_resolution))}
    ${card("Narrative Composer · Plan", renderOutline(g.plot_outline))}
    ${card("Narrative Canon Queries", renderCanonQueries(g.canon_query_set))}
    ${card("Canon Evidence Retrieval", renderCanonEvidence(g.canon_evidence_set))}
    ${card("Narrative Consistency Check", renderNarrativeConsistency(g.narrative_consistency_review))}
    ${g.revision_instruction ? card("Composer Revision Instruction", textBlock(g.revision_instruction)) : ""}
    ${card("Narrative Composer · Scene", g.scene_result ? `${g.scene_result.scene_reasoning ? `<div class="section-card muted">${esc(g.scene_result.scene_reasoning)}</div>` : ""}<div class="section-card">${esc(g.scene_result.scene_text)}</div>${g.scene_result.closing_prompt ? `<div class="section-card muted">${esc(g.scene_result.closing_prompt)}</div>` : ""}` : `<div class="empty">未生成正文</div>`)}
    ${card("上下文档案", `
      ${metrics([
        metric("行动实体", g.entity_profiles?.length || 0),
        metric("行动NPC", g.npc_profiles?.length || 0),
        metric("Composer实体", g.composer_entity_profiles?.length || 0, "info"),
        metric("Composer NPC", g.composer_npc_profiles?.length || 0, "info"),
      ])}
      ${details("Composer 实体", (g.composer_entity_profiles || []).map(entityCard).join("") || `<div class="empty">无</div>`)}
      ${details("Composer NPC", (g.composer_npc_profiles || []).map(npcProfileCard).join("") || `<div class="empty">无</div>`)}
      ${g.recent_scene ? details("最近场景", jsonBlock(g.recent_scene)) : ""}
    `)}
    ${details("Context", jsonBlock(g.context))}
  `;
}

function renderContextFacts(g) {
  const facts = g.context_facts?.facts || [];
  const intents = g.context_search_plan?.intents || [];
  const evidence = g.context_history_evidence || {};
  const continuity = g.continuity_selection || {};
  return card("Context Facts Branch", `
    ${metrics([
      metric("事实", facts.length, "ok"),
      metric("检索意图", intents.length, "info"),
      metric("正文证据", evidence.hits?.length || 0, "info"),
      metric("连续性事项", continuity.items?.length || 0, "info"),
      metric("索引回合", evidence.indexed_turns || 0, "info"),
    ])}
    ${details("Facts", facts.length ? facts.map(contextFactCard).join("") : `<div class="empty">无事实</div>`, true)}
    ${details("Search Intents", jsonBlock(g.context_search_plan || { intents: [] }))}
    ${details("Confirmed History", jsonBlock(evidence))}
    ${details("Active Continuity", jsonBlock(continuity))}
  `);
}

function contextFactCard(fact) {
  return `<div class="section-card">
    <div class="card-title"><span>${esc(fact.fact_id || "?")}</span></div>
    <div>${esc(text(fact.fact))}</div>
  </div>`;
}

function renderConfirmation() {
  const c = merged("confirmation");
  if (!state.run.confirmation.order.length) return `<div class="empty">暂无确认流程。</div>`;
  const sceneFacts = c.scene_facts || step("confirmation", "scene_facts_done")?.scene_facts;
  const facts = sceneFacts?.facts || [];
  const npcExtraction = c.npc_extraction || step("confirmation", "npc_extract_done")?.npc_extraction;
  const updatedNpcIds = c.updated_npc_profile_ids || step("confirmation", "entity_profiles_updated")?.updated_npc_profile_ids;
  return `
    ${timeline("confirmation", "确认事件")}
    ${card("Scene Facts", facts.length ? facts.map(contextFactCard).join("") : `<div class="empty">暂无事实</div>`)}
    ${renderStateDiff()}
    ${card("Continuity Operations", renderContinuityProjection(c.continuity_projection || step("confirmation", "continuity_projected")?.continuity_projection))}
    ${card("Entity Updates", renderEntityExtraction(c.entity_extraction || step("confirmation", "entity_extract_done")?.entity_extraction, c.updated_entity_ids))}
    ${card("NPC Profile Updates", renderNpcExtraction(npcExtraction, updatedNpcIds))}
  `;
}

function renderContinuity() {
  const confirm = step("confirmation", "finish") || {};
  const server = state.serverState || {};
  const stored = server.continuity_items || [];
  const active = stored.filter((item) => item.status === "active");
  const closed = stored.filter((item) => item.status !== "active");
  return `
    ${confirm.continuity_projection ? card("本轮生命周期投影", renderContinuityProjection(confirm.continuity_projection)) : ""}
    ${card("Continuity Ledger", `
      ${metrics([
        metric("Active", active.length, "ok"),
        metric("Closed", closed.length, closed.length ? "info" : ""),
      ])}
      ${details(`Active (${active.length})`, active.map(continuityItemCard).join("") || `<div class="empty">暂无活跃事项</div>`, true)}
      ${details(`Closed (${closed.length})`, closed.map(continuityItemCard).join("") || `<div class="empty">暂无已关闭事项</div>`)}
    `)}
  `;
}

function renderEntityDebug() {
  const extraction = step("confirmation", "entity_extract_done")?.entity_extraction || merged("confirmation").entity_extraction;
  const updated = step("confirmation", "entity_profiles_updated")?.updated_entity_ids || merged("confirmation").updated_entity_ids;
  return `
    ${card("实体提取", renderEntityExtraction(extraction, updated))}
    ${card("实体档案快照", renderEntityGroups(state.serverState?.entity_profiles || []))}
  `;
}

function renderNpcDebug() {
  const extraction = step("confirmation", "npc_extract_done")?.npc_extraction || merged("confirmation").npc_extraction;
  const updatedIds = step("confirmation", "entity_profiles_updated")?.updated_npc_profile_ids || merged("confirmation").updated_npc_profile_ids;
  const server = state.serverState || {};
  const npcProfiles = server.npc_profiles || [];
  const npcCount = server.npc_profile_count ?? npcProfiles.length;
  const npcActiveCount = server.npc_active_profile_count ?? npcProfiles.filter((p) => p.active !== false).length;
  return `
    ${card("NPC 提取", renderNpcExtraction(extraction, updatedIds))}
    ${card(`NPC 档案 (${npcActiveCount}/${npcCount})`, npcProfiles.length ? npcProfiles.map(npcProfileCard).join("") : `<div class="empty">暂无 NPC 档案</div>`)}
  `;
}

function renderDirectorDebug() {
  const generationDecision = merged("generation").director_decision;
  const stage = merged("generation").director_stage;
  const confirmationDecision = merged("confirmation").director_decision;
  const executions = merged("confirmation").director_executions;
  const directorNpcEntityId = merged("confirmation").director_npc_profile_id;
  const server = state.serverState || {};
  const active = sortDirectorEvents(server.active_director_events || []);
  const resolved = server.resolved_director_events || [];
  const activeArc = active.find((event) => event.role === "arc");
  const activeAmbient = active.find((event) => event.role === "ambient");
  const preparedNpcs = server.prepared_director_npcs || [];
  const introducedNpcs = server.introduced_director_npcs || [];
  return `
    ${card("导演库存", metrics([
      metric("Active", active.length, active.length ? "ok" : ""),
      metric("Arc", activeArc ? "active" : "none", activeArc ? "ok" : ""),
      metric("Ambient", activeAmbient ? "active" : "none", activeAmbient ? "ok" : ""),
      metric("Resolved", resolved.length, resolved.length ? "info" : ""),
      metric("Prepared NPC", preparedNpcs.length, preparedNpcs.length ? "info" : ""),
      metric("Introduced NPC", introducedNpcs.length, introducedNpcs.length ? "ok" : ""),
    ]))}
    ${card("生成决策", generationDecision ? jsonBlock({ decision: generationDecision, stage }) : `<div class="empty">生成回合后展示。</div>`)}
    ${card("确认决策", confirmationDecision ? jsonBlock({ decision: confirmationDecision, executions, director_npc_profile_id: directorNpcEntityId }) : `<div class="empty">确认后展示。</div>`)}
    ${details("Prepared NPC", preparedNpcs.map(directorNpcCard).join("") || `<div class="empty">暂无 prepared NPC</div>`, true)}
    ${details("Introduced NPC", introducedNpcs.map(directorNpcCard).join("") || `<div class="empty">暂无 introduced NPC</div>`)}
    ${details("Active", active.map(directorCard).join("") || `<div class="empty">暂无 active 事件</div>`, true)}
    ${details("Resolved", resolved.map(directorCard).join("") || `<div class="empty">暂无 resolved 事件</div>`)}
  `;
}

function renderPreview() {
  const preview = merged("confirmation").action_preview;
  if (!preview) return `<div class="empty">确认后生成行动预览。</div>`;
  return card("行动预览", `
    ${(preview.previews || []).map((p) => `<div class="section-card">
      <div class="card-title"><span>${esc(p.label)}</span>${metric(p.level || "")}</div>
      ${kv([
        ["收益", esc(text(p.good_result))],
        ["风险", esc(text(p.risk))],
      ])}
    </div>`).join("")}
  `);
}

function renderRaw() {
  return `
    ${details("Generation", jsonBlock(state.run.generation), true)}
    ${details("Confirmation", jsonBlock(state.run.confirmation), true)}
    ${details("Maintenance", jsonBlock(state.run.maintenance), true)}
    ${details("Server State", jsonBlock(state.serverState))}
  `;
}

function renderAction(result, raw, selfCheck) {
  if (!result) return `<div class="empty">暂无行动解析。</div>`;
  return `
    ${metrics([
      metric("Raw actions", raw?.atomic_actions?.length || 0),
      metric("Clean actions", result.atomic_actions?.length || 0, "info"),
      metric("Anchors", result.anchors?.length || 0),
    ])}
    ${selfCheck ? details("Self Check", jsonBlock(selfCheck)) : ""}
    ${(result.atomic_actions || []).map((a) => `<div class="section-card">
      <div class="card-title"><span>${esc(a.action_id)} · #${esc(a.sequence)}</span>${metric(a.action_type, a.risk_level || "risk?")}</div>
      ${kv([
        ["行动者", chips(a.actor)],
        ["原文", esc(text(a.raw_text))],
        ["动词", esc(text(a.verb))],
        ["目标", esc(text(a.target))],
        ["方式", esc(text(a.method))],
        ["骰点", esc(a.likely_needs_roll ? `是 · ${a.roll_hint || ""}` : "否")],
      ])}
    </div>`).join("")}
    ${details("Anchors", (result.anchors || []).map((a) => `<div class="section-card">${kv([
      ["文本", esc(a.text)],
      ["标准化", esc(text(a.normalized))],
      ["类型", esc(a.kind)],
      ["来源", esc(a.source)],
    ])}</div>`).join("") || `<div class="empty">无锚点</div>`)}
  `;
}

function renderRoll(result) {
  if (!result) return `<div class="empty">暂无骰点。</div>`;
  return `
    ${metrics([metric("公式", result.dice_formula), metric("次数", result.total_rolls), metric("面数", result.dice_sides)])}
    ${(result.results || []).map((r) => `<div class="section-card">
      <div class="card-title"><span>${esc(r.action_id)} · ${esc(r.action_type)}</span>${metric("最终", r.value, r.raw_value !== r.value ? "warn" : "")}</div>
      ${kv([
        ["行动者", chips(r.actor)],
        ["骰子", esc(r.dice)],
        ["原始", esc((r.all_raw_values || []).join(", ") || r.raw_value)],
        ["修正", chips((r.modifiers || []).map((m) => `${m.label} ${m.value > 0 ? "+" : ""}${m.value}`))],
        ["提示", esc(text(r.roll_hint))],
      ])}
    </div>`).join("") || `<div class="empty">本回合无骰点</div>`}
  `;
}

function renderResolution(result) {
  if (!result) return `<div class="empty">暂无结果判定。</div>`;
  return `
    ${card("判定审计", esc(text(result.resolution_reasoning)))}
    ${metrics([metric("解释", result.total_resolutions || 0)])}
    ${(result.action_resolutions || []).map((o) => `<div class="section-card">
      <div class="card-title"><span>${esc(o.action_id)} · ${esc(o.outcome_band)}</span>${metric("type", o.action_type)}</div>
      ${kv([
        ["行动者", chips(o.actor)],
        ["序号", esc(text(o.sequence))],
        ["事实结论", esc(text(o.resolved_fact))],
      ])}
    </div>`).join("")}
  `;
}

function consistencyTone(value) {
  if (value === "pass") return "ok";
  if (value === "revise") return "warn";
  return "";
}

function renderCanonQueries(querySet) {
  if (!querySet) return `<div class="empty">尚未生成查询</div>`;
  const queries = querySet.queries || [];
  return `
    ${metrics([metric("查询", queries.length, queries.length ? "info" : "")])}
    ${querySet.thought_process ? details("查询分析", esc(querySet.thought_process), true) : ""}
    ${queries.map((query) => `<div class="section-card">
      <div class="card-title"><span>${esc(query.query_id || "?")} · ${esc(query.claim_type || "other")}</span>${metric("priority", query.priority || "?")}</div>
      <div class="consistency-copy">${esc(text(query.claim))}</div>
      ${kv([
        ["段落", chips(query.paragraph_ids || [])],
        ["实体", chips(query.entities || [])],
        ["检索句", list(query.lookup_queries || [])],
      ])}
    </div>`).join("") || `<div class="empty">正文没有需要回查的历史主张</div>`}
  `;
}

function renderCanonEvidence(evidenceSet) {
  if (!evidenceSet) return `<div class="empty">尚未检索</div>`;
  const results = evidenceSet.results || [];
  const matched = results.filter((result) => result.evidence?.length).length;
  return `
    ${metrics([
      metric("索引轮次", evidenceSet.indexed_turns || 0, "info"),
      metric("索引文档", evidenceSet.indexed_documents || 0),
      metric("命中查询", `${matched}/${results.length}`, matched ? "ok" : ""),
    ])}
    ${results.map((result) => details(
      `${result.query_id || "?"} · ${result.evidence?.length || 0} 条证据`,
      `<div class="section-card">${kv([["查询主张", esc(text(result.claim))]])}</div>` +
      ((result.evidence || []).map((item) => `<div class="section-card">
        <div class="card-title"><span>${esc(item.evidence_id || "?")}</span>${metric("turn", item.turn_index ?? "?")}</div>
        <div class="consistency-copy">${esc(text(item.text))}</div>
        ${item.context_before ? details("上文", esc(item.context_before)) : ""}
        ${item.context_after ? details("下文", esc(item.context_after)) : ""}
        ${kv([
          ["来源", esc(item.source_type || "?")],
          ["场景", esc(text(item.scene_id))],
          ["段落", esc(text(item.paragraph_index))],
          ["实体", chips(item.matched_entities || [])],
          ["匹配词", chips(item.matched_terms || [])],
          ["通道", chips(item.retrieval_channels || [])],
          ["分数", esc(text(item.score))],
        ])}
      </div>`).join("") || `<div class="empty">没有达到入选条件的历史原文</div>`),
    )).join("") || `<div class="empty">没有查询</div>`}
  `;
}

function renderConsistencyAssertions(assertions) {
  if (!assertions || !assertions.length) return `<div class="empty">无修订断言</div>`;
  return assertions.map((item, index) => `<div class="consistency-issue">
    <div class="card-title">
      <span>问题 ${index + 1} · ${esc(text(item.issue_type, "issue"))}</span>
      ${chips(item.paragraph_ids || [], "无段落")}
    </div>
    ${kv([
      ["正文错误", esc(text(item.incorrect_statement))],
      ["正确事实", esc(text(item.authoritative_fact))],
      ["修订要求", esc(text(item.required_revision))],
      ["权威来源", chips(item.authority_refs || [])],
    ])}
    ${item.canon_query_id ? `<div class="consistency-refs">${chips([
      `query:${item.canon_query_id}`,
      ...(item.evidence_ids || []).map((id) => `evidence:${id}`),
    ])}</div>` : ""}
  </div>`).join("");
}

function renderCanonFindings(findings) {
  if (!findings || !findings.length) return `<div class="empty">本轮没有历史查询判定</div>`;
  return findings.map((item) => `<div class="consistency-issue">
    <div class="card-title">
      <span>${esc(text(item.query_id, "query"))}</span>
      ${metric("verdict", item.verdict || "?", item.verdict === "contradiction" ? "warn" : (item.verdict === "explicitly_confirmed" ? "ok" : "neutral"))}
    </div>
    <div class="consistency-copy">${esc(text(item.reasoning))}</div>
    <div class="consistency-refs">${chips((item.evidence_ids || []).map((id) => `evidence:${id}`))}</div>
  </div>`).join("");
}

function renderNarrativeConsistency(review) {
  if (!review) return `<div class="empty">尚未检查</div>`;
  const assertions = review.assertions || [];
  const findings = review.canon_findings || [];
  const contradictions = findings.filter((item) => item.verdict === "contradiction").length;

  return `
    ${metrics([
      metric("判定", review.decision || "?", consistencyTone(review.decision)),
      metric("历史查询", findings.length, contradictions ? "warn" : "ok"),
      metric("修订断言", assertions.length, assertions.length ? "warn" : "ok"),
    ])}
    <div class="section-card">
      <div class="card-title"><span>整体结论</span>${metric("decision", review.decision || "?", consistencyTone(review.decision))}</div>
      <div class="consistency-copy">${esc(text(review.audit_summary))}</div>
    </div>
    ${details("逐查询历史判定", renderCanonFindings(findings), true)}
    ${renderConsistencyAssertions(assertions)}
    ${details("完整 JSON", jsonBlock(review))}
  `;
}

function renderOutline(outline) {
  if (!outline) return `<div class="empty">暂无大纲。</div>`;
  return `
    ${details("导演原因", esc(text(outline.director_reason)), true)}
    ${card("规划审计", esc(text(outline.planning_reasoning)))}
    ${details("必须成立", list(outline.must_establish_facts))}
    ${details("不得成立", list(outline.must_not_establish_facts))}
    ${details("信息边界", list(outline.information_boundaries))}
    ${card("停点", esc(text(outline.scene_stopping_point)))}
    ${(outline.planned_events || []).map((e) => `<div class="section-card">
      <div class="card-title"><span>${esc(e.event_id)}</span></div>
      <div style="margin-bottom:8px">${esc(text(e.event_fact))}</div>
      ${kv([
        ["结果", chips(e.linked_outcome_ids)],
        ["实体", chips(e.involved_entities)],
      ])}
    </div>`).join("")}
  `;
}

function renderStateDiff() {
  const finish = step("confirmation", "finish");
  const before = finish?.state_before || step("generation", "state_loaded")?.state_before;
  const after = finish?.state_after || step("confirmation", "state_update_done")?.state_after;
  const changes = step("confirmation", "state_update_done")?.state_changes;
  if (!before && !after) return `<div class="empty">暂无状态快照。</div>`;
  if (!after) return card("生成时状态", stateSummary(before));
  const rows = diffRows(before, after);
  return `
    ${card("状态变化", `${metrics([metric("变化", rows.filter((r) => r.changed).length, rows.some((r) => r.changed) ? "warn" : "ok"), metric("字段", rows.length)])}<div class="diff-grid">
      <div class="diff-head">字段</div><div class="diff-head">更新前</div><div class="diff-head">更新后</div>
      ${rows.map((r) => `<div>${esc(r.label)}</div><div class="${r.changed ? "changed" : ""}">${renderValue(r.before)}</div><div class="${r.changed ? "changed" : ""}">${renderValue(r.after)}</div>`).join("")}
    </div>`)}
    <div class="two-col">${card("更新前", stateSummary(before))}${card("更新后", stateSummary(after))}</div>
    ${details("类型化状态操作", jsonBlock(changes || { operations: [] }), true)}
    ${details("完整 state_before", jsonBlock(before))}
    ${details("完整 state_after", jsonBlock(after))}
  `;
}

function diffRows(before, after) {
  const fields = [
    ["scene.scene_id", "场景ID"],
    ["scene.location", "地点"],
    ["scene.participants", "在场"],
    ["world_clock.day", "日期"],
    ["world_clock.phase", "时段"],
    ["player.visible_status", "主角外观"],
    ["resources.money", "金钱"],
    ["resources.inventory", "物品"],
    ["resources.fixed_assets", "固定资产"],
    ["resources.temporary_assets", "临时资产"],
  ];
  return fields.map(([path, label]) => {
    const oldValue = getPath(before, path);
    const newValue = getPath(after, path);
    return { label, before: oldValue, after: newValue, changed: JSON.stringify(oldValue ?? null) !== JSON.stringify(newValue ?? null) };
  });
}

function getPath(obj, path) {
  return path.split(".").reduce((cur, key) => cur == null ? undefined : cur[key], obj);
}

function renderValue(value) {
  if (Array.isArray(value)) {
    if (!value.length) return `<span class="empty">无</span>`;
    if (value.every((item) => typeof item !== "object" || item === null)) return chips(value);
    return `<pre style="margin:0;white-space:pre-wrap">${esc(JSON.stringify(value, null, 2))}</pre>`;
  }
  if (value && typeof value === "object") return `<pre style="margin:0;white-space:pre-wrap">${esc(JSON.stringify(value, null, 2))}</pre>`;
  return esc(text(value));
}

function stateSummary(s) {
  if (!s) return `<div class="empty">无状态</div>`;
  return kv([
    ["场景", esc(text(s.scene?.scene_id))],
    ["地点", esc(text(s.scene?.location))],
    ["时间", esc(`第${s.world_clock?.day || "?"}日 ${s.world_clock?.phase || "?"}`)],
    ["在场", chips(participantNames(s.scene?.participants))],
    ["主角", esc(text(s.player?.name))],
    ["物品", inventory(s.resources?.inventory)],
  ]);
}

function renderEntityExtraction(data, updatedIds = []) {
  if (!data) return `<div class="empty">暂无实体提取。</div>`;
  const aliasMatches = data.alias_matches || [];
  const retrieved = data.retrieved_entities || [];
  return `
    ${metrics([metric("别名匹配", aliasMatches.length, "info"), metric("实体", retrieved.length), metric("更新", updatedIds?.length || 0, "ok")])}
    ${aliasMatches.map((m) => `<div class="section-card">${kv([
      ["称呼", esc(text(m.extracted_text))],
      ["匹配", esc(text(m.matched_entity_name))],
      ["类型", esc(text(m.matched_entity_type))],
      ["新增别名", esc(text(m.new_alias))],
    ])}</div>`).join("")}
    ${retrieved.map((e) => `<div class="section-card">${kv([
      ["名称", esc(text(e.name))],
      ["类型", esc(text(e.entity_type))],
      ["来源", esc(text(e.source))],
      ["匹配", esc(text(e.matched_entity_name))],
      ["原文", esc(text(e.context_span))],
    ])}</div>`).join("")}
    ${updatedIds?.length ? card("更新实体ID", chips(updatedIds)) : ""}
  `;
}

function renderNpcExtraction(data, updatedIds = []) {
  if (!data) return `<div class="empty">No write_db or 暂无 NPC 提取。</div>`;
  const mentions = data.mentions || [];
  return `
    ${metrics([metric("NPC 称呼", mentions.length), metric("更新档案", updatedIds?.length || 0, "ok")])}
    ${mentions.map((mention) => `<div class="section-card">${kv([
      ["称呼", esc(text(mention.name))],
      ["证据", esc(text(mention.context_span))],
    ])}</div>`).join("")}
    ${updatedIds?.length ? card("更新 NPC 档案ID", chips(updatedIds)) : ""}
  `;
}

function npcProfileCard(profile) {
  const aliasHits = Object.entries(profile.alias_hit_counts || {}).sort((a, b) => Number(b[1]) - Number(a[1])).map(([k, v]) => `${k} ×${v}`);
  const active = profile.active !== false;
  return `<div class="section-card" style="${active ? "" : "opacity:.6"}">
    <div class="card-title"><span>${esc(profile.name)}</span>${metric(active ? "active" : "inactive", `hit ${profile.name_hit_count ?? 0}`, active ? "ok" : "warn")}</div>
    ${profile.public_summary || profile.role ? `<div style="margin-bottom:8px">${esc(text(profile.public_summary || profile.role))}</div>` : ""}
    ${kv([
      ["ID", esc(text(profile.npc_id))],
      ["别名", chips(profile.aliases)],
      ["别名命中", chips(aliasHits)],
      ["角色", esc(text(profile.role))],
      ["外观", esc(text(profile.appearance))],
      ["性格", chips(profile.personality_traits)],
      ["能力", chips(profile.known_abilities)],
      ["派系", esc(text(profile.faction))],
      ["所在地", esc(text(profile.current_location))],
      ["态度", esc(text(profile.attitude_to_player))],
      ["意图", esc(text(profile.current_intent))],
      ["缺席回合", esc(text(profile.absent_turns))],
      ["描述", esc(text(profile.description))],
      ["信心", esc(fmt(profile.confidence))],
      ["来源场景", chips(profile.source_scene_ids)],
      ["来源导演NPC", esc(text(profile.source_director_npc_id))],
      ["创建回合", esc(text(profile.created_turn))],
      ["引入回合", esc(text(profile.introduced_turn))],
      ["更新时间", esc(text(profile.updated_at))],
    ])}
    ${profile.core_motivation || profile.hidden_secret ? details("导演视图", `
      ${kv([
        ["核心动机", esc(text(profile.core_motivation))],
        ["秘密", esc(text(profile.hidden_secret))],
        ["冲突风格", esc(text(profile.conflict_style))],
        ["社交风格", esc(text(profile.social_style))],
        ["出场潜力", chips(profile.encounter_potentials)],
        ["导演备注", esc(text(profile.director_notes))],
      ])}
    `) : ""}
  </div>`;
}

function renderEntityGroups(profiles) {
  if (!profiles.length) return `<div class="empty">暂无实体档案。</div>`;
  const groups = {};
  for (const profile of profiles) {
    const type = profile.entity_type || "unknown";
    if (!groups[type]) groups[type] = [];
    groups[type].push(profile);
  }
  const labels = { npc: "NPC", item: "物品", location: "地点", organization: "组织" };
  return Object.entries(groups).map(([type, items]) => details(`${labels[type] || type} (${items.length})`, items.map(entityCard).join(""), type === "npc")).join("");
}

function renderNpcStateGroup(profiles) {
  if (!profiles.length) return `<div class="empty">暂无 NPC 档案。</div>`;
  const activeProfiles = profiles.filter((p) => p.active !== false).slice(0, 10);
  return activeProfiles.map(npcProfileCard).join("") || `<div class="empty">暂无活跃 NPC 档案。</div>`;
}

function directorNpcCard(npc) {
  if (!npc) return `<div class="empty">无</div>`;
  return `<div class="section-card">
    <div class="card-title"><span>${esc(npc.name || npc.draft_id)}</span>${metric(npc.status || "prepared", `T${text(npc.created_turn)}`)}</div>
    <div style="margin-bottom:8px">${esc(text(npc.appearance))}</div>
    ${kv([
      ["ID", esc(text(npc.draft_id))],
      ["角色", esc(text(npc.role))],
      ["派系", esc(text(npc.faction))],
      ["引入回合", esc(text(npc.introduced_turn))],
      ["性格", chips(npc.personality_traits)],
      ["能力", chips(npc.known_abilities)],
      ["核心动机", esc(text(npc.core_motivation))],
      ["秘密", esc(text(npc.hidden_secret))],
      ["冲突风格", esc(text(npc.conflict_style))],
      ["社交风格", esc(text(npc.social_style))],
      ["出场潜力", chips(npc.encounter_potentials)],
    ])}
  </div>`;
}

function entityCard(profile) {
  const aliasHits = Object.entries(profile.alias_hit_counts || {}).sort((a, b) => Number(b[1]) - Number(a[1])).map(([k, v]) => `${k} ×${v}`);
  return `<div class="section-card">
    <div class="card-title"><span>${esc(profile.name)} · ${esc(profile.entity_type)}</span>${metric("hit", profile.name_hit_count ?? 0)}</div>
    ${profile.summary ? `<div style="margin-bottom:8px">${esc(profile.summary)}</div>` : ""}
    ${kv([
      ["ID", esc(text(profile.entity_id))],
      ["别名", chips(profile.aliases)],
      ["别名命中", chips(aliasHits)],
      ["来源场景", chips(profile.source_scene_ids)],
      ["更新", esc(text(profile.updated_at))],
    ])}
  </div>`;
}

function directorCard(event) {
  if (!event) return `<div class="empty">无</div>`;
  const hooks = event.hook_pool || [];
  const hookCards = hooks.map((hook, index) => `
    <div class="entry-point">
      <div class="muted">Hook ${index + 1} · ${esc(text(hook.kind))}${hook.consumed ? " · 已消耗" : ""}</div>
      ${kv([
        ["内容", esc(text(hook.content))],
        ["触发锚点", chips(hook.trigger_anchors)],
      ])}
    </div>
  `).join("");
  const worldImpact = event.world_impact || null;
  return `<div class="section-card">
    <div class="card-title"><span>${esc(event.title || event.event_id)}</span>${metric(event.role || "role?", event.stage || "active", event.stage === "resolved" ? "info" : "ok")}</div>
    ${event.macro_intent ? `<div style="margin-bottom:8px">${esc(event.macro_intent)}</div>` : ""}
    ${kv([
      ["ID", esc(text(event.event_id))],
      ["行动方", esc(text(event.subject))],
      ["意图", esc(text(event.intent))],
      ["手段", esc(text(event.method))],
      ["演员引用", chips((event.actor_refs || []).map((ref) => `${text(ref.kind)}:${text(ref.resource_id)}`))],
      ["创建/更新", esc(`T${text(event.created_turn)} / T${text(event.updated_turn)}`)],
      ["解决", event.resolved_turn ? esc(`T${event.resolved_turn}`) : `<span class="empty">无</span>`],
      ["解决原因", esc(text(event.resolved_reason))],
      ["接触记录", list((event.player_contacts || []).map((item) =>
        `T${text(item.turn_index)} · ${text(item.contact)}`
      ))],
      ["最近呈现", event.last_presented_turn === null || event.last_presented_turn === undefined ? `<span class="empty">无</span>` : esc(`T${event.last_presented_turn}`)],
      ["参与者", chips(event.actors)],
      ["world_impact", worldImpact ? esc(text(worldImpact.description)) : `<span class="empty">无</span>`],
    ])}
    ${hookCards || `<div class="empty">无 Hook</div>`}
  </div>`;
}

function sortDirectorEvents(events) {
  const roleOrder = { arc: 0, ambient: 1 };
  return [...events].sort((a, b) => (roleOrder[a.role] ?? 2) - (roleOrder[b.role] ?? 2));
}

function sceneSummaryCard(summary) {
  return `<div class="section-card">
    <div class="card-title"><span>${esc(`L${summary.level || "?"} · Turn ${text(summary.turn_start)}-${text(summary.turn_end)}`)}</span>${metric(summary.active === false ? "inactive" : "active", (summary.scene_ids || []).length, summary.active === false ? "warn" : "ok")}</div>
    <div style="margin-bottom:8px">${esc(text(summary.content))}</div>
    ${kv([
      ["ID", esc(text(summary.id))],
      ["场景", chips(summary.scene_ids)],
      ["来源", chips(summary.source_ids)],
      ["压缩到", esc(text(summary.condensed_into))],
      ["创建", esc(text(summary.created_at))],
    ])}
  </div>`;
}

function continuityItemCard(item) {
  return `<div class="section-card">
    <div class="card-title"><span>${esc(item.subject || item.id || "未命名")}</span>${metric(continuityKindLabels[item.kind] || item.kind || "kind", item.status || "")}</div>
    <div style="margin-bottom:8px">${esc(text(item.statement))}</div>
    ${kv([
      ["ID", esc(text(item.id))],
      ["实体", chips(item.entities)],
      ["地点", esc(text(item.location))],
      ["期限", esc(text(item.deadline))],
      ["状态", esc(text(item.status))],
      ["创建/更新", esc(`${text(item.created_turn)} / ${text(item.updated_turn)}`)],
      ["关闭回合", esc(text(item.closed_turn))],
    ])}
  </div>`;
}

function renderContinuityProjection(projection) {
  const operations = projection?.operations || [];
  return operations.map((operation) => `<div class="section-card">
    <div class="card-title"><span>${esc(operation.item?.subject || operation.item_id || "operation")}</span>${metric(operation.operation || "op", operation.item?.kind || "")}</div>
    <div style="margin-bottom:8px">${esc(text(operation.item?.statement || operation.reason))}</div>
    ${kv([
      ["Item ID", esc(text(operation.item_id))],
      ["实体", chips(operation.item?.entities)],
      ["地点", esc(text(operation.item?.location))],
      ["期限", esc(text(operation.item?.deadline))],
      ["证据", chips(operation.evidence_ids)],
      ["理由", esc(text(operation.reason))],
    ])}
  </div>`).join("") || `<div class="empty">本轮无 Continuity 变化</div>`;
}

function fmt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "0";
  return Number(value).toFixed(4);
}

async function refreshState({ preserveStatus = false } = {}) {
  const previousStatus = state.operationStatus;
  const data = await api("/api/state");
  state.serverState = data;
  if (data.difficulty) els.difficultySelect.value = data.difficulty;
  if (data.max_chars) els.maxCharsInput.value = data.max_chars;
  renderStatePanel();
  if (!state.chatRestored) {
    state.chatRestored = true;
    restoreChat(data.chat_history || []);
  }
  if (!data.needs_init) {
    state.coreMemoryInitialized = true;
    if (!state.pendingTurnId && data.pending_turn_groups?.length) {
      const group = data.pending_turn_groups[0];
      state.pendingGroupId = group.group_id;
      state.pendingVariants = group.variants || [];
      selectPendingVariant(state.pendingVariants.length - 1);
    } else if (!state.pendingTurnId && (data.pending_openings?.length || data.pending_opening_ids?.length)) {
      const opening = data.pending_openings?.[0];
      state.pendingTurnId = opening?.turn_id || data.pending_opening_ids[0];
      state.pendingSceneText = opening?.scene_result?.scene_text || null;
      if (opening?.scene_result?.scene_text) {
        addEditableScene(
          opening.scene_result.scene_text,
          "开场 · 待确认",
          opening.scene_result.closing_prompt || "",
        );
      }
      els.turnInfo.textContent = `Pending ${state.pendingTurnId}`;
    }
    setStatus(preserveStatus ? previousStatus : (state.pendingTurnId ? "正文已生成 - 待确认" : "Ready"));
  }
}

async function refreshMaintenanceJobs() {
  if (!state.serverState) return;
  const jobs = state.serverState.maintenance_jobs || [];
  const hasActiveJobs = jobs.some((job) => ["queued", "running"].includes(job.status));
  if (!hasActiveJobs) return;
  try {
    const data = await api("/api/maintenance-jobs?limit=100");
    state.serverState.maintenance_jobs = data.jobs || [];
    state.serverState.maintenance_job_status_counts = data.status_counts || {};
    if (state.activeTab === "jobs" || state.activeTab === "overview") renderDebug();
  } catch (err) {
    if (state.activeTab === "jobs") {
      els.debugBody.insertAdjacentHTML("afterbegin", `<div class="job-error">${esc(err.message || "后台任务刷新失败")}</div>`);
    }
  }
}

function handleGenerationPipelineEvent(event, { retry = false } = {}) {
  const data = event.data || {};
  if (event.step === "state_loaded") setDebugStatus("状态加载完成");
  if (event.step === "retry_started") {
    startStreamingScene(1, data.version_index || state.pendingVariants.length + 1);
  }
  if (event.step === "action_parsed") state.activeTab = "generation";
  if (event.step === "scene_stream_started") {
    startStreamingScene(data.attempt || 1, null, Boolean(data.revision));
  }
  if (event.step === "scene_text_delta") appendStreamingScene(data.delta || "");
  if (event.step === "narrative_checked") setDebugStatus("一致性检查完成");
  if (event.step === "narrative_revision_started") {
    setDebugStatus("正在根据问题断言重写正文");
  }
  if (event.step === "narrative_revised") setDebugStatus("正文修订完成");
  if (event.step === "scene_written") {
    appendPendingVariant(data);
    setStatus(retry ? "新版本已生成 - 待确认" : "正文已生成 - 待确认");
    setDebugStatus("等待确认");
  }
}

function handleConfirmationPipelineEvent(event) {
  if (event.step === "state_update_done") state.activeTab = "diff";
  if (event.step === "scene_facts_done") state.activeTab = "confirmation";
  if (event.step === "entity_extract_done") state.activeTab = "entity";
  if (event.step === "npc_extract_done") state.activeTab = "npc";
  if (event.step === "action_preview_done") state.activeTab = "preview";
  if (event.step === "finish") {
    const data = event.data || {};
    if (data.write_db) {
      markPendingSceneConfirmed();
      state.pendingTurnId = null;
      state.pendingGroupId = null;
      state.pendingVariants = [];
      state.pendingVariantIndex = -1;
      state.pendingSceneText = null;
      els.input.value = "";
      addMessage("assistant", "已确认采用，并写入状态、历史与 Continuity。", "系统");
      setStatus("确认完成");
      els.turnInfo.textContent = "等待玩家行动";
    } else {
      addMessage("assistant", "Dry Run 完成，候选版本仍可切换和确认。", "系统");
      setStatus("Dry Run 完成 - 仍待确认");
      els.turnInfo.textContent = `Pending ${state.pendingTurnId}`;
    }
    setDebugStatus(data.status || "committed");
    state.activeTab = "diff";
  }
}

async function replayOperation(operation, scope, onEvent) {
  const response = await fetch(
    `/api/operations/${encodeURIComponent(operation.operation_id)}/events?after=0`,
  );
  await readEventStream(response, scope, onEvent);
}

async function failResumedOperation(err, label, operationId) {
  state.activeOperationId = null;
  state.run.errors.push(err);
  setStatus(label);
  setDebugStatus(label);
  renderError(err);
  await acknowledgeOperation(operationId || err?.operationId);
}

async function resumeInitializationOperation(operation) {
  const campaignSetup = (
    operation.operation_type === "initialization_campaign_setup"
  );
  state.activeOperationId = operation.operation_id;
  resetRun("initialization");
  setStatus(
    campaignSetup ? "正在恢复公开世界惯例..." : "正在恢复开场生成...",
    true,
  );
  setDebugStatus(campaignSetup ? "公开世界惯例" : "开场生成中");
  renderStatePanel();
  renderDebug();
  try {
    await replayOperation(
      operation,
      "initialization",
      handleInitializationPipelineEvent,
    );
    if (campaignSetup) {
      setStatus("公开世界惯例已生成");
    } else {
      await refreshState({ preserveStatus: true });
      setStatus("开场已生成 - 待确认");
    }
    await acknowledgeOperation(operation.operation_id);
  } catch (err) {
    await failResumedOperation(err, "初始化失败", operation.operation_id);
  }
}

async function resumeConfirmationOperation(operation) {
  state.activeOperationId = operation.operation_id;
  resetRun("confirmation");
  setStatus("正在恢复确认...", true);
  setDebugStatus("确认中");
  state.activeTab = "overview";
  renderDebug();
  try {
    await replayOperation(
      operation,
      "confirmation",
      handleConfirmationPipelineEvent,
    );
    if (operation.metadata?.write_db !== false) {
      await refreshState({ preserveStatus: true });
    }
    await acknowledgeOperation(operation.operation_id);
  } catch (err) {
    await failResumedOperation(err, "确认失败", operation.operation_id);
  }
}

async function resumeGenerationOperation(operation) {
  const retry = operation.operation_type === "retry";
  state.activeOperationId = operation.operation_id;
  resetRun("generation");
  setStatus(retry ? "正在恢复Retry..." : "正在恢复生成...", true);
  setDebugStatus(retry ? "Retry 生成中" : "生成中");
  state.activeTab = "overview";
  renderDebug();
  try {
    await replayOperation(operation, "generation", (event) => {
      handleGenerationPipelineEvent(event, { retry });
    });
    await refreshState({ preserveStatus: true });
    await acknowledgeOperation(operation.operation_id);
  } catch (err) {
    await failResumedOperation(
      err,
      retry ? "Retry 失败" : "生成失败",
      operation.operation_id,
    );
  }
}

async function resumeActivePipelineOperation() {
  if (state.activeOperationId) return;
  const data = await api("/api/operations/recoverable");
  const operation = (data.operations || []).find(
    (item) => RESUMABLE_OPERATION_TYPES.has(item.operation_type),
  );
  if (!operation) return;
  if (operation.operation_type.startsWith("initialization_")) {
    await resumeInitializationOperation(operation);
  } else if (operation.operation_type === "confirmation") {
    await resumeConfirmationOperation(operation);
  } else {
    await resumeGenerationOperation(operation);
  }
}

async function generateTurn() {
  const userInput = els.input.value.trim();
  if (!state.coreMemoryInitialized) {
    renderInitPanel();
    return;
  }
  if (!userInput) return;
  addMessage("user", userInput, "玩家行动");
  resetRun("all");
  state.pendingTurnId = null;
  state.pendingGroupId = null;
  state.pendingVariants = [];
  state.pendingVariantIndex = -1;
  state.pendingSceneText = null;
  removePendingScene();
  setStatus("正在执行管线...", true);
  setDebugStatus("生成中");
  els.turnInfo.textContent = "生成中";
  state.activeTab = "overview";
  renderDebug();
  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_input: userInput,
        difficulty: els.difficultySelect.value,
        max_chars: parseInt(els.maxCharsInput.value, 10) || 1500,
        request_id: newRequestId(),
      }),
    });
    const operationId = await readEventStream(response, "generation", (event) => {
      handleGenerationPipelineEvent(event);
    });
    await acknowledgeOperation(operationId);
  } catch (err) {
    removeStreamingScene();
    state.run.errors.push(err);
    setStatus("生成失败");
    setDebugStatus("生成失败");
    state.pendingTurnId = null;
    state.pendingGroupId = null;
    state.pendingVariants = [];
    state.pendingVariantIndex = -1;
    const failedTraceId = merged("generation").trace_id;
    if (failedTraceId) await loadTrace(failedTraceId);
    renderError(err);
    await acknowledgeOperation(err?.operationId);
  }
}

async function retryTurn() {
  if (!state.pendingTurnId || !state.pendingGroupId) return;
  capturePendingSceneText();
  const retryFromTurnId = state.pendingTurnId;
  const retryFromVariantIndex = state.pendingVariantIndex;
  resetRun("generation");
  setStatus("正在重新生成...", true);
  setDebugStatus("Retry 生成中");
  state.activeTab = "overview";
  renderDebug();
  try {
    const response = await fetch("/api/retry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        turn_id: retryFromTurnId,
        request_id: newRequestId(),
      }),
    });
    const operationId = await readEventStream(response, "generation", (event) => {
      handleGenerationPipelineEvent(event, { retry: true });
    });
    await acknowledgeOperation(operationId);
  } catch (err) {
    removeStreamingScene();
    selectPendingVariant(retryFromVariantIndex);
    state.run.errors.push(err);
    setStatus("重新生成失败，原版本仍可确认");
    setDebugStatus("Retry 失败");
    const failedTraceId = merged("generation").trace_id;
    if (failedTraceId) await loadTrace(failedTraceId);
    renderError(err);
    await acknowledgeOperation(err?.operationId);
  }
}

async function discardPendingTurn() {
  if (!state.pendingTurnId || !state.pendingGroupId) return;
  if (!window.confirm("删除本次输入生成的全部未确认版本，并返回生成前状态？")) return;
  const turnId = state.pendingTurnId;
  setStatus("正在删除未确认正文...", true);
  setDebugStatus("删除 Pending");
  try {
    const data = await api("/api/pending/discard", {
      method: "POST",
      body: JSON.stringify({ turn_id: turnId }),
    });
    removeStreamingScene();
    removePendingScene();
    state.pendingTurnId = null;
    state.pendingGroupId = null;
    state.pendingVariants = [];
    state.pendingVariantIndex = -1;
    state.pendingSceneText = null;
    els.input.value = data.user_input || "";
    resetRun("all");
    await refreshState({ preserveStatus: true });
    restoreChat(state.serverState?.chat_history || []);
    els.input.focus();
    els.turnInfo.textContent = "等待玩家行动";
    setStatus("未确认正文已删除，可修改输入");
    setDebugStatus("Ready");
    renderDebug();
  } catch (err) {
    setStatus("删除未确认正文失败");
    setDebugStatus("删除失败");
    renderError(err);
  }
}

async function confirmTurn() {
  if (!state.pendingTurnId) return;
  resetRun("confirmation");
  setStatus("正在确认...", true);
  setDebugStatus("确认中");
  state.activeTab = "overview";
  renderDebug();
  try {
    const writeDb = els.writeDbToggle.checked;
    const body = { turn_id: state.pendingTurnId, write_db: writeDb };
    body.request_id = newRequestId();
    const sceneText = getSceneText();
    if (sceneText != null) body.scene_text = sceneText;
    const response = await fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const operationId = await readEventStream(
      response,
      "confirmation",
      handleConfirmationPipelineEvent,
    );
    if (writeDb) await refreshState({ preserveStatus: true });
    await acknowledgeOperation(operationId);
  } catch (err) {
    state.run.errors.push(err);
    setStatus("确认失败");
    setDebugStatus("确认失败");
    renderError(err);
    await acknowledgeOperation(err?.operationId);
  }
}

function renderError(err) {
  state.activeTab = "raw";
  els.debugBody.innerHTML = card("错误", `<div class="empty">${esc(err.message || err.error || "Unknown")}</div>${err.traceback ? jsonBlock(err.traceback) : ""}`, "bad");
}

els.generateBtn.onclick = generateTurn;
els.confirmBtn.onclick = confirmTurn;
els.refreshBtn.onclick = async () => {
  await refreshState();
  await resumeActivePipelineOperation();
};
els.saveSelect.onchange = () => switchSave(els.saveSelect.value);
els.createSaveBtn.onclick = createSave;
els.undoBtn.onclick = () => changeHistory("undo");
els.redoBtn.onclick = () => changeHistory("redo");
els.difficultySelect.onchange = () => api("/api/difficulty", { method: "POST", body: JSON.stringify({ difficulty: els.difficultySelect.value }) }).catch(console.error);
els.maxCharsInput.onchange = () => api("/api/max-chars", { method: "POST", body: JSON.stringify({ max_chars: parseInt(els.maxCharsInput.value, 10) || 1500 }) }).catch(console.error);

renderTabs();
renderDebug();
Promise.all([loadSaves(), refreshState()])
  .then(() => resumeActivePipelineOperation())
  .catch((err) => {
    els.statePanel.innerHTML = card("状态加载失败", esc(err.message || "Unknown"));
  });
window.setInterval(refreshMaintenanceJobs, 2000);
