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
  turnInfo: $("turnInfo"),
};

const tabs = [
  ["overview", "总览"],
  ["diff", "状态变化"],
  ["generation", "生成"],
  ["confirmation", "确认"],
  ["memory", "记忆"],
  ["entity", "实体"],
  ["director", "导演事件"],
  ["preview", "行动预览"],
  ["raw", "Raw"],
];

const narrativeTypes = [
  ["advance", "推进"],
  ["relationship_shift", "关系变化"],
  ["reveal_information", "揭示信息"],
  ["escalate_pressure", "升压"],
  ["complication", "麻烦"],
  ["transition", "过渡"],
  ["respite", "休整"],
];

const memoryKindLabels = {
  commitment: "承诺",
  open_thread: "未决线索",
  action_constraint: "行动约束",
  resource_access: "资源/权限",
  relationship_state: "关系状态",
  belief: "信念",
};

const evidenceLabels = {
  observed: "亲见",
  reported: "转述",
  believed: "信念",
  inferred: "推断",
};

const state = {
  activeTab: "overview",
  serverState: null,
  traces: {},
  traceRequests: {},
  pendingTurnId: null,
  pendingSceneText: null,
  selectedNarrativeType: "advance",
  chatRestored: false,
  coreMemoryInitialized: true,
  operationStatus: "Ready",
  run: emptyRun(),
};

function emptyRun() {
  return {
    generation: { order: [], steps: {}, merged: {} },
    confirmation: { order: [], steps: {}, merged: {} },
    maintenance: { order: [], steps: {}, merged: {} },
    errors: [],
  };
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
  return `${resources.money} ${resources.money_unit || "金币"}`;
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
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop();
    for (const part of parts) {
      if (!part.startsWith("data: ")) continue;
      const event = JSON.parse(part.slice(6));
      if (!event.ok) throw event;
      recordStep(scope, event);
      onEvent(event);
      renderDebug();
    }
  }
}

function setStatus(label, busy = false) {
  state.operationStatus = label;
  els.statusText.textContent = label;
  els.generateBtn.disabled = busy || !state.coreMemoryInitialized;
  els.confirmBtn.disabled = busy || !state.pendingTurnId;
  els.input.disabled = !state.coreMemoryInitialized;
  els.writeDbToggle.disabled = busy || !state.coreMemoryInitialized;
}

function setDebugStatus(label) {
  els.debugStatus.textContent = label;
}

function addMessage(role, body, meta = "") {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  wrap.innerHTML = `<div class="bubble">${meta ? `<div class="bubble-meta">${esc(meta)}</div>` : ""}${esc(body)}</div>`;
  els.messages.appendChild(wrap);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function restoreChat(history) {
  els.messages.innerHTML = "";
  for (const turn of history || []) {
    if (turn.user_input) addMessage("user", turn.user_input, "玩家行动");
    addMessage("assistant", turn.scene_text, `Turn ${turn.turn_index} · ${turn.scene_id}`);
  }
}

function addEditableScene(textValue, meta, narrativeType) {
  removePendingScene();
  state.pendingSceneText = textValue;
  state.selectedNarrativeType = narrativeType || "advance";
  const wrap = document.createElement("div");
  wrap.className = "message assistant";
  wrap.id = "pendingSceneBubble";
  wrap.innerHTML = `
    <div class="bubble scene-bubble">
      <div class="bubble-meta">
        <span class="scene-meta">${esc(meta)}</span>
        <button class="scene-edit">编辑</button>
      </div>
      <div class="scene-display"></div>
      <div class="scene-narrative-options">
        ${narrativeTypes.map(([id, label]) => `<button class="scene-type ${state.selectedNarrativeType === id ? "selected" : ""}" data-type="${id}">${esc(label)}</button>`).join("")}
      </div>
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
  const editorWrap = wrap.querySelector(".scene-editor-wrap");
  const editor = wrap.querySelector(".scene-editor");
  display.textContent = textValue;
  editor.value = textValue;
  wrap.querySelector(".scene-edit").onclick = () => {
    editor.value = state.pendingSceneText || "";
    display.style.display = "none";
    editorWrap.style.display = "block";
    editor.focus();
  };
  wrap.querySelector(".scene-save").onclick = () => {
    state.pendingSceneText = editor.value;
    display.textContent = editor.value;
    editorWrap.style.display = "none";
    display.style.display = "block";
  };
  wrap.querySelector(".scene-cancel").onclick = () => {
    editorWrap.style.display = "none";
    display.style.display = "block";
  };
  wrap.querySelectorAll(".scene-type").forEach((btn) => {
    btn.onclick = () => {
      state.selectedNarrativeType = btn.dataset.type;
      wrap.querySelectorAll(".scene-type").forEach((other) => other.classList.toggle("selected", other === btn));
    };
  });
  els.messages.appendChild(wrap);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function removePendingScene() {
  document.getElementById("pendingSceneBubble")?.remove();
}

function markPendingSceneConfirmed() {
  const bubble = document.getElementById("pendingSceneBubble");
  if (!bubble) return;
  bubble.removeAttribute("id");
  bubble.querySelector(".scene-edit")?.remove();
  bubble.querySelector(".scene-narrative-options")?.remove();
  bubble.querySelector(".scene-editor-wrap")?.remove();
  const meta = bubble.querySelector(".scene-meta");
  if (meta) meta.insertAdjacentHTML("afterend", ` <span class="scene-confirmed">已确认</span>`);
}

function getSceneText() {
  return state.pendingSceneText || null;
}

function readInitConfig() {
  const fanfic = $("initFanfic")?.checked || false;
  const abilities = [...document.querySelectorAll(".init-ability")].map((el) => el.value.trim()).filter(Boolean).slice(0, 3);
  let extraItems = [];
  const extraRaw = $("initExtraItems")?.value.trim();
  if (extraRaw) extraItems = JSON.parse(extraRaw);
  const age = $("initAge")?.value.trim();
  const config = {
    version: 1,
    fanfic,
    work_name: $("initWorkName")?.value.trim() || null,
    birth_location: $("initBirthLocation")?.value.trim() || null,
    story_era: $("initStoryEra")?.value.trim() || null,
    magic_level: $("initMagicLevel")?.value || null,
    world_name: $("initWorldName")?.value.trim() || null,
    world_style: $("initWorldStyle")?.value.trim() || null,
    protagonist_gender: $("initGender")?.value || "男",
    protagonist_name: $("initName")?.value.trim() || "",
    protagonist_age: age ? Number(age) : null,
    protagonist_background: $("initBackground")?.value.trim() || null,
    protagonist_abilities: abilities,
    narrative_tone: $("initTone")?.value.trim() || null,
    story_expectation: $("initStoryExpectation")?.value.trim() || null,
    extra_core_memory_items: extraItems,
  };
  if (fanfic) {
    config.magic_level = null;
    config.world_name = null;
    config.world_style = null;
  } else {
    config.work_name = null;
    config.birth_location = null;
    config.story_era = null;
  }
  return config;
}

function renderInitPanel() {
  state.coreMemoryInitialized = false;
  setStatus("需要初始化 Core Memory");
  els.turnInfo.textContent = "未初始化";
  els.statePanel.innerHTML = `
    ${card("初始化", `<div class="empty">创建核心记忆后会生成开场场景，并进入待确认状态。</div>`)}
    <section class="card init-grid">
      <label><input id="initFanfic" type="checkbox" /> 同人世界</label>
      <input id="initName" placeholder="主角姓名" />
      <select id="initGender"><option>男</option><option>女</option><option>其他</option></select>
      <input id="initAge" type="number" min="0" placeholder="年龄，可选" />
      <textarea id="initBackground" placeholder="主角背景，可选"></textarea>
      <input id="initWorkName" placeholder="同人作品名" />
      <input id="initBirthLocation" placeholder="同人出生地点" />
      <input id="initStoryEra" placeholder="同人故事时代" />
      <select id="initMagicLevel"><option value="普通">普通魔法</option><option value="低魔">低魔</option><option value="高魔">高魔</option></select>
      <input id="initWorldName" placeholder="原创世界名" />
      <input id="initWorldStyle" placeholder="世界风格" />
      <input class="init-ability" placeholder="能力 1" />
      <input class="init-ability" placeholder="能力 2" />
      <input class="init-ability" placeholder="能力 3" />
      <input id="initTone" placeholder="叙事基调，可选" />
      <textarea id="initStoryExpectation" placeholder="剧情主旨与游玩期待"></textarea>
      <textarea id="initExtraItems" placeholder='额外核心记忆 JSON，可选'></textarea>
      <button id="initPreviewBtn">预览</button>
      <button id="initSaveBtn" class="primary-button">保存初始化</button>
      <pre id="initPreview" class="json-block">等待预览...</pre>
    </section>
  `;
  $("initPreviewBtn").onclick = previewInit;
  $("initSaveBtn").onclick = applyInit;
}

async function previewInit() {
  const box = $("initPreview");
  try {
    const data = await api("/api/init/preview", { method: "POST", body: JSON.stringify(readInitConfig()) });
    box.textContent = data.valid ? JSON.stringify(data.core_memory.items, null, 2) : JSON.stringify(data.validation_errors || [], null, 2);
  } catch (err) {
    box.textContent = err.message || "预览失败";
  }
}

async function applyInit() {
  const btn = $("initSaveBtn");
  btn.disabled = true;
  try {
    const data = await api("/api/init/apply", { method: "POST", body: JSON.stringify(readInitConfig()) });
    state.coreMemoryInitialized = true;
    state.pendingTurnId = data.turn_id || null;
    state.selectedNarrativeType = "transition";
    await refreshState({ preserveStatus: true });
    if (data.opening_scene?.scene_text) addEditableScene(data.opening_scene.scene_text, "开场 · 待确认", "transition");
    setStatus(state.pendingTurnId ? "开场已生成 - 待确认" : "Core Memory 已初始化");
    els.turnInfo.textContent = state.pendingTurnId ? `Pending ${state.pendingTurnId}` : "等待玩家行动";
  } catch (err) {
    btn.disabled = false;
    $("initPreview").textContent = err.message || "保存失败";
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
  const npcs = current.npcs || [];
  const activeEvent = data.active_director_event;
  const memories = data.memories || [];
  const activeMemories = memories.filter((m) => (m.status || "active") === "active").slice(0, 12);
  const summaries = data.scene_summaries || [];
  const recentSummaries = data.recent_summaries || [];
  const pendingVisible = filterVisiblePending(data.pending_memory_items || []);
  const pendingStats = pendingMemoryStats(data);
  els.statePanel.innerHTML = `
    ${card("当前场景", kv([
      ["场景", esc(text(scene.scene_id))],
      ["地点", esc(text(scene.location))],
      ["时间", esc(`第${current.world_clock?.day || "?"}日 ${current.world_clock?.phase || "?"}`)],
      ["气氛", esc(text(scene.atmosphere))],
      ["在场", chips(scene.participants)],
    ]))}
    ${card("主角", kv([
      ["姓名", esc(text(player.name))],
      ["位置", esc(text(player.position))],
      ["外观", esc(text(player.visible_status))],
      ["状态", chips(player.condition)],
    ]))}
    ${card("资源", kv([
      ["金钱", esc(money(resources))],
      ["物品", inventory(resources.inventory)],
      ["关键物", chips(resources.key_items)],
      ["资产", chips(resources.fixed_assets)],
      ["临时", chips(resources.temporary_assets)],
    ]))}
    ${details(`NPC (${npcs.length})`, npcs.length ? npcs.map(npcCard).join("") : `<div class="empty">暂无 NPC</div>`)}
    ${details("导演事件", activeEvent ? directorCard(activeEvent) : `<div class="empty">暂无 active 事件</div>`)}
    ${details(`场景摘要 (${summaries.length})`, `
      ${metrics([
        metric("活跃", summaries.length),
        metric("最近", recentSummaries.length, "info"),
      ])}
      ${(recentSummaries.length ? recentSummaries : summaries.slice(0, 8)).map(sceneSummaryCard).join("") || `<div class="empty">暂无场景摘要</div>`}
    `)}
    ${details(`实体档案 (${(data.entity_profiles || []).length})`, renderEntityGroups(data.entity_profiles || []))}
    ${details(`长期记忆 (${data.active_memory_count || 0}/${data.memory_count || 0})`, activeMemories.length ? activeMemories.map(memoryRecordCard).join("") : `<div class="empty">暂无活跃记忆</div>`)}
    ${details(`待定记忆 (${pendingStats.unclassified}/${pendingStats.total})`, `
      ${metrics([
        metric("未分类", pendingStats.unclassified, pendingStats.unclassified ? "warn" : "ok"),
        metric("总数", pendingStats.total),
        metric("回合", data.pending_unclassified_turn_count || 0, "info"),
      ])}
      <div class="button-row" style="margin-bottom:10px">
        <button id="batchBtn">触发批分类</button>
        <button id="maintainBtn">触发记忆维护</button>
      </div>
      ${pendingVisible.map(pendingMemoryCard).join("") || `<div class="empty">暂无未分类待定记忆</div>`}
    `)}
  `;
  $("batchBtn")?.addEventListener("click", triggerBatchClassify);
  $("maintainBtn")?.addEventListener("click", triggerMemoryMaintain);
}

function npcCard(npc) {
  return `<div class="section-card">${kv([
    ["姓名", esc(text(npc.name))],
    ["角色", esc(text(npc.role))],
    ["地点", esc(text(npc.location))],
    ["态度", esc(text(npc.attitude_to_player))],
    ["意图", esc(text(npc.current_intent))],
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
    diff: renderStateDiff,
    generation: renderGeneration,
    confirmation: renderConfirmation,
    memory: renderMemory,
    entity: renderEntityDebug,
    director: renderDirectorDebug,
    preview: renderPreview,
    raw: renderRaw,
  };
  els.debugBody.innerHTML = (renderers[state.activeTab] || renderOverview)();
}

function renderOverview() {
  const gen = state.run.generation;
  const conf = state.run.confirmation;
  const maint = state.run.maintenance;
  const finish = step("confirmation", "finish");
  const scene = step("generation", "scene_written");
  return `
    ${card("运行总览", `
      ${metrics([
        metric("生成", gen.order.length, gen.order.length ? "info" : ""),
        metric("确认", conf.order.length, finish ? "ok" : ""),
        metric("维护", maint.order.length, maint.order.length ? "warn" : ""),
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
  return traces.filter((trace) => tasks.includes(trace.task));
}

function stepTasks(scope, stepName) {
  const generation = {
    entity_profiles_loaded: ["anchor_extract"],
    action_parsed: ["action_extract"],
    modifiers_done: ["roll_modifier"],
    turn_resolution_done: ["turn_resolution"],
    outline_done: ["plot_outline"],
    scene_written: ["scene_write"],
  };
  const confirmation = {
    state_update_done: ["state_update"],
    memory_draft_extract_done: ["memory_draft_extract"],
    entity_extract_done: ["entity_extract"],
    action_preview_done: ["action_preview"],
    entity_profiles_updated: ["entity_profile_update", "director_event_review", "director_event_select"],
    finish: ["*"],
  };
  const maintenance = {
    batch_classified: ["memory_batch_classify", "memory_maintain"],
    memory_maintain_done: ["memory_maintain"],
  };
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
  if (name === "retrieval_done") return `${data.retrieved_memories?.total_returned || 0} memories`;
  if (name === "rolls_done") return `${data.roll_results?.total_rolls || 0} rolls`;
  if (name === "scene_written") return `turn ${data.turn_index || "?"}`;
  if (name === "state_update_done") return "state updated";
  if (name === "memory_draft_extract_done") return `${data.pending_items_count || 0} pending`;
  if (name === "entity_extract_done") return `${data.entity_extraction?.retrieved_entities?.length || 0} entities`;
  if (name === "batch_classified") return `${data.final_memories?.length || 0} final`;
  if (name === "memory_maintain_done") return `${data.changed || 0} changed`;
  if (name === "finish") return data.status || "done";
  return Object.keys(data || {}).join(", ") || "event";
}

function renderGeneration() {
  const g = merged("generation");
  if (!state.run.generation.order.length) return `<div class="empty">暂无生成流程。</div>`;
  return `
    ${card("Action", renderAction(g.action_parse_result, g.raw_action_parse_result, g.action_self_check_result))}
    ${card("Retrieval", renderRetrieval(g.retrieved_memories, g.selected_memory_ids, g.selected_memories))}
    ${card("Roll", renderRoll(g.roll_results))}
    ${card("Turn Resolution", renderResolution(g.turn_resolution))}
    ${card("Outline", renderOutline(g.plot_outline))}
    ${card("Writer", g.scene_result ? `<div class="section-card">${esc(g.scene_result.scene_text)}</div>${list(g.scene_result.changes_summary)}` : `<div class="empty">未生成正文</div>`)}
    ${details("Context", jsonBlock(g.context))}
  `;
}

function renderConfirmation() {
  const c = merged("confirmation");
  if (!state.run.confirmation.order.length) return `<div class="empty">暂无确认流程。</div>`;
  return `
    ${timeline("confirmation", "确认事件")}
    ${renderStateDiff()}
    ${card("Pending Memories", filterVisiblePending(c.memory_extraction?.pending_items || c.pending_items || []).map(pendingMemoryCard).join("") || `<div class="empty">暂无未分类候选</div>`)}
    ${card("Entity Updates", renderEntityExtraction(c.entity_extraction || step("confirmation", "entity_extract_done")?.entity_extraction, c.updated_entity_ids))}
  `;
}

function renderMemory() {
  const batch = step("maintenance", "batch_classified");
  const batchDone = step("maintenance", "batch_classify_done") || {};
  const maintain = step("maintenance", "memory_maintain_done") || batch?.maintenance;
  const confirm = step("confirmation", "finish")?.memory_extraction;
  return `
    ${timeline("maintenance", "维护事件")}
    ${confirm ? card("本轮待定记忆", `
      ${metrics([metric("候选", confirm.pending_items?.length || 0), metric("保存", confirm.saved_pending_ids?.length || 0), metric("库内", confirm.store_total || 0)])}
      ${filterVisiblePending(confirm.pending_items || []).map(pendingMemoryCard).join("") || `<div class="empty">无未分类候选</div>`}
    `) : ""}
    ${batch ? renderBatch({...batch, ...batchDone}) : card("MemoryBatchClassifier", `<div class="empty">尚未触发批分类。</div>`)}
    ${maintain ? renderMaintainer(maintain) : card("MemoryMaintainer", `<div class="empty">尚未触发生命周期维护。</div>`)}
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

function renderDirectorDebug() {
  const result = merged("confirmation").director_event_result;
  const server = state.serverState || {};
  const active = server.active_director_event ? [server.active_director_event] : [];
  const suspended = server.suspended_director_events || [];
  const archived = server.archived_director_events || [];
  return `
    ${card("事件库存", metrics([
      metric("Active", active.length, active.length ? "ok" : ""),
      metric("Suspended", suspended.length, suspended.length ? "warn" : ""),
      metric("Archived", archived.length, archived.length ? "info" : ""),
    ]))}
    ${details("Active", active.map(directorCard).join("") || `<div class="empty">暂无 active 事件</div>`, true)}
    ${result ? card("本轮结果", `
      ${kv([
        ["模式", esc(text(result.mode))],
        ["变更事件", esc(text(result.changed_event_id))],
        ["备注", chips(result.notes)],
      ])}
      ${result.review_result ? details("Review", jsonBlock(result.review_result), true) : ""}
      ${result.selection_result ? details("Select/Create", jsonBlock(result.selection_result), true) : ""}
    `) : card("本轮结果", `<div class="empty">确认正式回合后展示。</div>`)}
    ${details("Suspended", suspended.map(directorCard).join("") || `<div class="empty">暂无 suspended 事件</div>`)}
    ${details("Archived", archived.map(directorCard).join("") || `<div class="empty">暂无 archived 事件</div>`)}
  `;
}

function renderPreview() {
  const preview = merged("confirmation").action_preview;
  if (!preview) return `<div class="empty">确认后生成行动预览。</div>`;
  return card("行动预览", `
    <div class="section-card">${esc(text(preview.scene_context))}</div>
    ${(preview.previews || []).map((p) => `<div class="section-card">
      <div class="card-title"><span>${esc(p.label)}</span>${metric(p.level || "", p.action_type || "")}</div>
      ${kv([
        ["说明", esc(text(p.description))],
        ["收益", esc(text(p.good_result))],
        ["风险", esc(text(p.risk))],
        ["动词", esc(text(p.verb))],
        ["目标", esc(text(p.target))],
        ["方式", esc(text(p.method))],
        ["目的", esc(text(p.goal))],
        ["骰点", esc(p.likely_needs_roll ? "是" : "否")],
        ["锚点", chips((p.anchors || []).map((a) => `${a.text} · ${a.kind}`))],
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
    ${card("Turn Intent", esc(text(result.turn_intent)))}
    ${(result.atomic_actions || []).map((a) => `<div class="section-card">
      <div class="card-title"><span>${esc(a.action_id)} · #${esc(a.sequence)}</span>${metric(a.action_type, a.risk_level || "risk?")}</div>
      ${kv([
        ["行动者", esc(text(a.actor))],
        ["原文", esc(text(a.raw_text))],
        ["动词", esc(text(a.verb))],
        ["目标", esc(text(a.target))],
        ["方式", esc(text(a.method))],
        ["目的", esc(text(a.goal))],
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

function renderRetrieval(result, selectedIds = [], selected = []) {
  if (!result) return `<div class="empty">暂无检索。</div>`;
  return `
    ${metrics([metric("扫描", result.total_scanned || 0), metric("返回", result.total_returned || 0, "info"), metric("选用", selectedIds?.length || 0, "ok")])}
    ${selected?.length ? details("Scene Writer 选用", selected.map(memoryCandidateCard).join(""), true) : ""}
    ${(result.candidates || []).map((c) => memoryCandidateCard(c, selectedIds?.includes(c.memory_id))).join("") || `<div class="empty">无候选</div>`}
    ${filterVisiblePending(result.pending_notes || []).length ? details("Pending Notes", filterVisiblePending(result.pending_notes || []).map(pendingMemoryCard).join("")) : ""}
  `;
}

function renderRoll(result) {
  if (!result) return `<div class="empty">暂无骰点。</div>`;
  return `
    ${metrics([metric("公式", result.dice_formula), metric("次数", result.total_rolls), metric("面数", result.dice_sides)])}
    ${(result.results || []).map((r) => `<div class="section-card">
      <div class="card-title"><span>${esc(r.action_id)} · ${esc(r.action_type)}</span>${metric("最终", r.value, r.raw_value !== r.value ? "warn" : "")}</div>
      ${kv([
        ["行动者", esc(r.actor)],
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
    ${metrics([metric("类型", result.selected_type, "info"), metric("解释", result.total_resolutions || 0)])}
    ${(result.action_resolutions || []).map((o) => `<div class="section-card">
      <div class="card-title"><span>${esc(o.action_id)} · ${esc(o.outcome_band)}</span>${metric("type", o.action_type)}</div>
      ${kv([
        ["行动者", esc(text(o.actor))],
        ["序号", esc(text(o.sequence))],
        ["成立方式", esc(text(o.resolved_effect))],
      ])}
    </div>`).join("")}
    ${details("叙事类型过滤", kv([
      ["可选", chips(result.available_types)],
      ["前置", chips(result.previous_types)],
      ["Roll", list(result.roll_filter_notes)],
      ["冷却", list(result.cooldown_filter_notes)],
    ]))}
  `;
}

function renderOutline(outline) {
  if (!outline) return `<div class="empty">暂无大纲。</div>`;
  return `
    ${card("摘要", esc(text(outline.outline_summary)))}
    ${(outline.beats || []).map((b) => `<div class="section-card">
      <div class="card-title"><span>${esc(b.beat_id)}</span>${metric("记忆", (b.required_memory_indices || []).length)}</div>
      <div style="margin-bottom:8px">${esc(text(b.summary))}</div>
      ${kv([
        ["用户行动", esc(text(b.user_action_execution))],
        ["主角反应", esc(text(b.protagonist_response_guidance))],
        ["结果", chips(b.linked_outcome_ids)],
        ["实体", chips(b.related_entities)],
        ["状态影响", list(b.state_implications)],
      ])}
    </div>`).join("")}
    ${details("写作约束", list(outline.constraints_for_scene_writer))}
    ${details("风险规避", list(outline.risks_to_avoid))}
  `;
}

function renderStateDiff() {
  const finish = step("confirmation", "finish");
  const before = finish?.state_before || step("generation", "state_loaded")?.state_before;
  const after = finish?.state_after || step("confirmation", "state_update_done")?.state_after;
  if (!before && !after) return `<div class="empty">暂无状态快照。</div>`;
  if (!after) return card("生成时状态", stateSummary(before));
  const rows = diffRows(before, after);
  return `
    ${card("状态变化", `${metrics([metric("变化", rows.filter((r) => r.changed).length, rows.some((r) => r.changed) ? "warn" : "ok"), metric("字段", rows.length)])}<div class="diff-grid">
      <div class="diff-head">字段</div><div class="diff-head">更新前</div><div class="diff-head">更新后</div>
      ${rows.map((r) => `<div>${esc(r.label)}</div><div class="${r.changed ? "changed" : ""}">${renderValue(r.before)}</div><div class="${r.changed ? "changed" : ""}">${renderValue(r.after)}</div>`).join("")}
    </div>`)}
    <div class="two-col">${card("更新前", stateSummary(before))}${card("更新后", stateSummary(after))}</div>
    ${details("完整 state_before", jsonBlock(before))}
    ${details("完整 state_after", jsonBlock(after))}
  `;
}

function diffRows(before, after) {
  const fields = [
    ["scene.scene_id", "场景ID"],
    ["scene.location", "地点"],
    ["scene.atmosphere", "气氛"],
    ["scene.participants", "在场"],
    ["world_clock.day", "日期"],
    ["world_clock.phase", "时段"],
    ["player.position", "主角位置"],
    ["player.visible_status", "主角外观"],
    ["player.condition", "主角状态"],
    ["resources.money", "金钱"],
    ["resources.inventory", "物品"],
    ["resources.key_items", "关键物"],
    ["resources.fixed_assets", "固定资产"],
    ["resources.temporary_assets", "临时资产"],
    ["npcs", "NPC明细"],
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
    ["在场", chips(s.scene?.participants)],
    ["主角", esc(`${text(s.player?.name)} · ${text(s.player?.position)}`)],
    ["状态", chips(s.player?.condition)],
    ["NPC", chips((s.npcs || []).map((n) => n.name))],
    ["物品", inventory(s.resources?.inventory)],
  ]);
}

function renderBatch(data) {
  const raw = data.raw_candidates || [];
  const finalMemories = data.final_memories || [];
  return `
    ${card("MemoryBatchClassifier", `
      ${metrics([metric("候选", raw.length, "info"), metric("最终", finalMemories.length, "ok"), metric("丢弃", data.discarded_count || 0, "warn"), metric("实体链接", data.entity_links_created || 0)])}
      ${kv([["保存ID", chips(data.saved_ids)], ["标记Pending", chips(data.classified_pending_ids)], ["批次数", esc(text(data.batch_classify_count))]])}
    `)}
    ${data.self_check_result ? details("Self Check", checkSummary(data.self_check_result), true) : ""}
    ${details("最终记忆", finalMemories.map(memoryRecordCard).join("") || `<div class="empty">暂无最终记忆</div>`, true)}
    ${details("分类候选", raw.map(candidateCard).join("") || `<div class="empty">暂无候选</div>`)}
  `;
}

function renderMaintainer(data) {
  const proposals = data.proposals || [];
  return `
    ${card("MemoryMaintainer", metrics([
      metric("摘要", data.summaries ?? data.summary_count ?? 0),
      metric("变更", data.changed ?? data.changed_count ?? 0, data.changed || data.changed_count ? "warn" : "ok"),
      metric("游标", `${text(data.cursor_before)} → ${text(data.cursor_after)}`),
    ]))}
    ${proposals.length ? proposals.map(proposalCard).join("") : `<div class="empty">没有生命周期 proposal。</div>`}
    ${details("完整输出", jsonBlock(data))}
  `;
}

function checkSummary(check) {
  if (!check) return `<div class="empty">无检查结果</div>`;
  return `
    ${metrics([
      check.original_count !== undefined ? metric("原始", check.original_count) : "",
      check.final_count !== undefined ? metric("最终", check.final_count, "ok") : "",
      check.warnings ? metric("警告", check.warnings.length, "warn") : "",
      check.fixes ? metric("修复", check.fixes.length, "info") : "",
      check.changes ? metric("变更", check.changes.length, "warn") : "",
    ])}
    ${details("警告", list(check.warnings))}
    ${details("修复", list(check.fixes))}
    ${details("变更", list((check.changes || []).map((c) => `${c.memory_index ?? "-"} · ${c.field}: ${c.reason || ""}`)))}
  `;
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
      ["来源记忆", chips(profile.source_memory_ids)],
      ["更新", esc(text(profile.updated_at))],
    ])}
  </div>`;
}

function directorCard(event) {
  if (!event) return `<div class="empty">无</div>`;
  return `<div class="section-card">
    <div class="card-title"><span>${esc(event.title || event.event_id)}</span>${metric(event.status || "active", event.phase || "")}</div>
    ${event.backstage_premise ? `<div style="margin-bottom:8px">${esc(event.backstage_premise)}</div>` : ""}
    <div style="margin-bottom:8px">${esc(text(event.stages?.[event.phase]))}</div>
    ${kv([
      ["ID", esc(text(event.event_id))],
      ["阶段回合", esc(text(event.phase_turns_elapsed))],
      ["创建/更新", esc(`T${text(event.created_turn)} / T${text(event.updated_turn)}`)],
      ["解决", event.resolved_turn ? esc(`T${event.resolved_turn}`) : `<span class="empty">无</span>`],
      ["解决Hook", esc(text(event.resolved_by_hook))],
      ["暂停原因", esc(text(event.suspended_reason))],
      ["解决原因", esc(text(event.resolved_reason))],
      ["关联NPC", chips(event.related_npcs)],
      ["地点限制", chips(event.location_constraints)],
      ["Manifest Hooks", list(event.manifesting_prevention_hooks)],
      ["Crisis Hooks", list(event.crisis_resolution_hooks)],
      ["已知", list(event.known_to_player)],
      ["隐藏", list(event.hidden_developments)],
    ])}
  </div>`;
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

function memoryCandidateCard(c, selected = false) {
  return `<div class="section-card">
    <div class="card-title"><span>${esc(c.subject || c.memory_id)}</span>${metric(selected ? "已引用" : "score", selected ? "" : fmt(c.score), selected ? "ok" : "")}</div>
    <div style="margin-bottom:8px">${esc(text(c.content || c.raw_text))}</div>
    ${kv([
      ["ID", esc(text(c.memory_id || c.pending_id))],
      ["类型", esc(`${text(c.memory_kind)} · ${text(c.evidence_mode)}`)],
      ["实体", chips(c.entities)],
      ["锚点", chips(c.matched_anchors)],
      ["行动", chips(c.matched_actions)],
    ])}
  </div>`;
}

function memoryRecordCard(mem) {
  return `<div class="section-card">
    <div class="card-title"><span>${esc(mem.subject || mem.id || "未命名")}</span>${metric(memoryKindLabels[mem.memory_kind] || mem.memory_kind || "kind", evidenceLabels[mem.evidence_mode] || mem.evidence_mode || "")}</div>
    <div style="margin-bottom:8px">${esc(text(mem.content))}</div>
    ${kv([
      ["ID", esc(text(mem.id))],
      ["实体", chips(mem.entities)],
      ["可见", chips(mem.visibility)],
      ["标签", chips(mem.tags)],
      ["地点", esc(text(mem.location))],
      ["时间", esc(text(mem.time_label))],
      ["证据", list(mem.source_text_span)],
    ])}
  </div>`;
}

function pendingMemoryCard(item) {
  return `<div class="section-card">
    <div class="card-title"><span>${esc(item.raw_text || item.subject || item.id || "候选")}</span>${metric(item.memory_kind || "kind", item.importance_hint || "")}</div>
    ${kv([
      ["ID", esc(text(item.id || item.pending_id))],
      ["场景", esc(`${text(item.scene_id)} / Turn ${text(item.turn_index)}`)],
      ["主体", esc(text(item.subject))],
      ["实体", chips(item.entities)],
      ["地点", esc(text(item.location))],
      ["时间", esc(text(item.time_label))],
      ["证据", list(item.source_text_span)],
    ])}
  </div>`;
}

function filterVisiblePending(items) {
  return (items || []).filter((item) => !item.classified && !item.discarded);
}

function pendingMemoryStats(data) {
  const items = data?.pending_memory_items || [];
  const visible = filterVisiblePending(items);
  const total = data?.pending_memory_count ?? items.length;
  const unclassified = data?.pending_unclassified_count ?? visible.length;
  return { total, unclassified };
}

function candidateCard(mem) {
  const keep = mem.keep !== false;
  return `<div class="section-card" style="${keep ? "" : "opacity:.7"}">
    <div class="card-title"><span>${esc(mem.subject || mem.content || "候选")}</span>${metric(keep ? "keep" : "discard", mem.memory_kind || "", keep ? "ok" : "bad")}</div>
    <div style="margin-bottom:8px">${esc(text(mem.content || mem.discard_reason))}</div>
    ${kv([
      ["类型", esc(`${text(mem.memory_kind)} · ${text(mem.evidence_mode)}`)],
      ["实体", chips(mem.entities)],
      ["可见", chips(mem.visibility)],
      ["Pending", chips(mem.source_pending_ids)],
      ["证据", list(mem.source_text_span)],
      ["舍弃原因", esc(text(mem.discard_reason))],
    ])}
  </div>`;
}

function proposalCard(p) {
  return `<div class="section-card">
    <div class="card-title"><span>${esc(p.action || "proposal")}</span>${metric("mem", (p.source_memory_ids || []).length)}</div>
    <div style="margin-bottom:8px">${esc(text(p.reason))}</div>
    ${kv([
      ["来源记忆", chips(p.source_memory_ids)],
      ["证据摘要", chips(p.evidence_summary_ids)],
      ["替代", p.replacement ? esc(text(p.replacement.content || p.replacement.subject)) : `<span class="empty">无</span>`],
    ])}
  </div>`;
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
    if (!state.pendingTurnId && data.pending_turn_ids?.length) {
      state.pendingTurnId = data.pending_turn_ids[0];
      els.turnInfo.textContent = `Pending ${state.pendingTurnId}`;
    }
    setStatus(preserveStatus ? previousStatus : (state.pendingTurnId ? "正文已生成 - 待确认" : "Ready"));
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
      }),
    });
    await readEventStream(response, "generation", (event) => {
      const data = event.data || {};
      if (event.step === "state_loaded") setDebugStatus("状态加载完成");
      if (event.step === "action_parsed") state.activeTab = "generation";
      if (event.step === "scene_written") {
        state.pendingTurnId = data.turn_id;
        addEditableScene(data.scene_result.scene_text, `待确认 · Turn ${data.turn_index}`, merged("generation").turn_resolution?.selected_type);
        els.turnInfo.textContent = `Pending ${state.pendingTurnId}`;
        setStatus("正文已生成 - 待确认");
        setDebugStatus("等待确认");
      }
    });
  } catch (err) {
    state.run.errors.push(err);
    setStatus("生成失败");
    setDebugStatus("生成失败");
    state.pendingTurnId = null;
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
    const sceneText = getSceneText();
    if (sceneText != null) body.scene_text = sceneText;
    if (state.selectedNarrativeType) body.narrative_type = state.selectedNarrativeType;
    const response = await fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await readEventStream(response, "confirmation", (event) => {
      if (event.step === "state_update_done") state.activeTab = "diff";
      if (event.step === "memory_draft_extract_done") state.activeTab = "confirmation";
      if (event.step === "entity_extract_done") state.activeTab = "entity";
      if (event.step === "action_preview_done") state.activeTab = "preview";
      if (event.step === "finish") {
        const data = event.data || {};
        state.pendingTurnId = null;
        markPendingSceneConfirmed();
        els.input.value = "";
        addMessage("assistant", data.write_db ? "已确认采用，并写入状态、短期记忆和记忆流程。" : "Dry Run 完成，未写入数据库。", "系统");
        setStatus(data.memory_extraction?.batch_queued ? "确认完成 · 后台维护中" : "确认完成");
        setDebugStatus(data.status || "committed");
        els.turnInfo.textContent = data.memory_extraction?.batch_queued ? "后台维护中" : "等待玩家行动";
        state.activeTab = "diff";
      }
    });
    if (writeDb) await refreshState({ preserveStatus: true });
  } catch (err) {
    state.run.errors.push(err);
    setStatus("确认失败");
    setDebugStatus("确认失败");
    renderError(err);
  }
}

async function triggerBatchClassify() {
  resetRun("maintenance");
  setStatus("批分类中...", true);
  setDebugStatus("MemoryBatchClassifier");
  state.activeTab = "memory";
  renderDebug();
  try {
    const response = await fetch("/api/batch-classify", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    await readEventStream(response, "maintenance", (event) => {
      if (event.step === "batch_classified") setDebugStatus("批分类完成");
      if (event.step === "batch_classify_done") setStatus("批分类完成");
    });
    await refreshState({ preserveStatus: true });
  } catch (err) {
    state.run.errors.push(err);
    setStatus("批分类失败");
    renderError(err);
  }
}

async function triggerMemoryMaintain() {
  resetRun("maintenance");
  setStatus("记忆维护中...", true);
  setDebugStatus("MemoryMaintainer");
  state.activeTab = "memory";
  renderDebug();
  try {
    const response = await fetch("/api/memory-maintain", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    await readEventStream(response, "maintenance", (event) => {
      if (event.step === "memory_maintain_done") setStatus("记忆维护完成");
    });
    await refreshState({ preserveStatus: true });
  } catch (err) {
    state.run.errors.push(err);
    setStatus("记忆维护失败");
    renderError(err);
  }
}

function renderError(err) {
  state.activeTab = "raw";
  els.debugBody.innerHTML = card("错误", `<div class="empty">${esc(err.message || err.error || "Unknown")}</div>${err.traceback ? jsonBlock(err.traceback) : ""}`, "bad");
}

els.generateBtn.onclick = generateTurn;
els.confirmBtn.onclick = confirmTurn;
els.refreshBtn.onclick = () => refreshState();
els.difficultySelect.onchange = () => api("/api/difficulty", { method: "POST", body: JSON.stringify({ difficulty: els.difficultySelect.value }) }).catch(console.error);
els.maxCharsInput.onchange = () => api("/api/max-chars", { method: "POST", body: JSON.stringify({ max_chars: parseInt(els.maxCharsInput.value, 10) || 1500 }) }).catch(console.error);

renderTabs();
renderDebug();
refreshState().catch((err) => {
  els.statePanel.innerHTML = card("状态加载失败", esc(err.message || "Unknown"));
});
