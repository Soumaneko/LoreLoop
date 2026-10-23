import pytest

import shutil
import subprocess

from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "player_static"


def test_provider_expansion_and_explicit_selection_gate():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r'''
import assert from "node:assert/strict";
const { renderProviderOptionsView } = await import(process.argv[1]);
function element() {
  return {value: "", children: [], setAttribute(k, v) {this[k] = v;},
    replaceChildren() {this.children = [];}, append(x) {this.children.push(x);}};
}
const els = Object.fromEntries([
  "modelConnectionFields", "modelConfigActions", "modelTestResult", "modelProviderPanel",
  "modelCustomBtn", "modelRefreshBtn", "modelProviderSelected", "modelProviderChange", "modelProviderLabel",
  "modelProviderSearch", "modelProviderExpand", "modelProviderResults",
].map(k => [k, element()]));
globalThis.document = {createElement: element};
let modelDraftIsNew = true, modelProviderPickerOpen = true, modelProvidersExpanded = false;
let modelSelection = {preset_id: "custom"}, modelDraftOriginal = null;
let modelConfigsCache = {services: []}, modelCatalogLoading = false, modelCatalogError = null;
const catalogProviderById = (c, id) => c.providers.find(p => p.provider_id === id);
const chooseProvider = () => {};
const catalog = {providers: [
  {provider_id: "openai", provider_name: "OpenAI"},
  {provider_id: "other", provider_name: "Other service"},
]};
const renderProviderOptions = catalog => renderProviderOptionsView(catalog, { els, modelDraftIsNew, modelProviderPickerOpen, modelSelection,   modelDraftOriginal, modelConfigsCache, modelProvidersExpanded,   modelCatalogLoading, modelCatalogError, catalogProviderById, chooseProvider });
renderProviderOptions(catalog);
assert.deepEqual(els.modelProviderResults.children.map(x => x.textContent), ["OpenAI"]);
assert.equal(els.modelConnectionFields.hidden, true);
assert.equal(els.modelConnectionFields.disabled, true);
assert.equal(els.modelConfigActions.hidden, true);
assert.equal(els.modelRefreshBtn.hidden, false);
modelProvidersExpanded = true;
renderProviderOptions(catalog);
assert.equal(els.modelProviderResults.children.length, 2);
assert.equal(els.modelProviderExpand["aria-expanded"], "true");
modelProvidersExpanded = false;
els.modelProviderSearch.value = "other";
renderProviderOptions(catalog);
assert.deepEqual(els.modelProviderResults.children.map(x => x.textContent), ["Other service"]);
els.modelProviderSearch.value = "";
renderProviderOptions(catalog);
assert.equal(els.modelProviderResults.children.length, 1);
for (const id of ["custom", "openai"]) {
  modelSelection.preset_id = id;
  modelProviderPickerOpen = false;
  renderProviderOptions(catalog);
  assert.equal(els.modelConnectionFields.hidden, false);
  assert.equal(els.modelConnectionFields.disabled, false);
  assert.equal(els.modelConfigActions.hidden, false);
  assert.equal(els.modelRefreshBtn.hidden, true);
}
modelProviderPickerOpen = true;
renderProviderOptions(catalog);
assert.equal(els.modelConnectionFields.disabled, true);
assert.equal(els.modelRefreshBtn.hidden, false);
modelDraftIsNew = false;
renderProviderOptions(catalog);
assert.equal(els.modelConnectionFields.hidden, false);
assert.equal(els.modelProviderChange.hidden, true);
assert.equal(els.modelRefreshBtn.hidden, true);
'''
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script,
         (STATIC_DIR / "model_configs.js").resolve().as_uri()],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_editable_model_input_keyboard_and_refresh():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r'''
import assert from "node:assert/strict";
const {createModelCombobox} = await import(process.argv[1]);
function element() {
  return { value: "", id: "models", children: [], listeners: {},
    addEventListener(type, fn) {this.listeners[type] = fn;},
    setAttribute() {}, removeAttribute() {}, scrollIntoView() {},
    replaceChildren() {this.children = [];}, append(x) {this.children.push(x);} };
}
globalThis.document = {createElement: element};
const input = element(), menu = element();
let selected;
const box = createModelCombobox({input, menu, onSelect: v => {selected = v;}});
const key = value => {
  let prevented = false;
  input.listeners.keydown({key: value, preventDefault() {prevented = true;}, stopPropagation() {}});
  return prevented;
};
box.setModels([{value: "one", label: "First"}, {value: "two", label: "Second"}]);
input.listeners.focus();
key("ArrowDown"); key("ArrowDown");
assert.equal(key("Enter"), true);
assert.equal(selected, "two");
assert.equal(menu.hidden, true);
input.value = "unlisted-id";
input.listeners.input();
box.setModels([{value: "new", label: "New model"}]);
assert.equal(input.value, "unlisted-id");
assert.equal(key("Enter"), true);
assert.equal(selected, "two");
input.listeners.focus();
key("Escape");
assert.equal(menu.hidden, true);
input.listeners.focus();
key("Tab");
assert.equal(menu.hidden, true);
'''
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script,
         (STATIC_DIR / "model_combobox.js").resolve().as_uri()],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_player_frontend_javascript_is_syntactically_valid():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    for path in STATIC_DIR.glob("*.js"):
        source = path.read_text(encoding="utf-8")
        result = subprocess.run(
            [node, "--input-type=module", "--check"],
            input=source,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_one_click_confirm_guard_only_allows_turn_kind_when_enabled():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "state.js").resolve().as_uri()
    script = f"""
const {{ shouldAutoConfirmTurn }} = await import("{module_url}");
if (shouldAutoConfirmTurn({{ enabled: false, pendingKind: "turn" }})) {{
  throw new Error("disabled preference must never auto-confirm");
}}
if (shouldAutoConfirmTurn({{ enabled: true, pendingKind: "opening" }})) {{
  throw new Error("opening pending must never auto-confirm");
}}
if (!shouldAutoConfirmTurn({{ enabled: true, pendingKind: "turn" }})) {{
  throw new Error("turn pending with enabled preference should auto-confirm");
}}
if (!shouldAutoConfirmTurn({{ enabled: true }})) {{
  throw new Error("missing pending kind should be treated as a regular turn");
}}
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_model_selection_row_switch_and_metadata_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "model_selection.js").resolve().as_uri()
    script = f"""
const m = await import("{module_url}");
const eq = (a, b, msg) => {{
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(msg + ": " + JSON.stringify(a));
}};
const row = {{ model_id: "grok-4.6", protocol: "openai_responses", base_url: "https://go.example/v1" }};
// 1. Untouched catalog defaults move as a whole (stale supplier must not survive).
eq(
  m.applyRowDecision(row, {{ protocol: "openai_chat", base_url: "https://deepseek.example", model: "" }}),
  {{ protocol: "openai_responses", base_url: "https://go.example/v1", model: "grok-4.6" }},
  "catalog row change",
);
// Explicit advanced edits still win while the selected model is updated.
eq(
  m.applyRowDecision(row, {{ protocol: "openai_chat", base_url: "https://mine.example", model: "mine" }}, {{ advancedTouched: true }}),
  {{ protocol: "openai_chat", base_url: "https://mine.example", model: "grok-4.6" }},
  "row change keeps user edits",
);
// 2. Manual input keeps its text as model_id; matched rows carry the row id.
eq(
  m.selectionOnModelInput("moonshot", "my-custom-model", null),
  {{ preset_id: "moonshot", model_id: "my-custom-model" }},
  "manual model_id",
);
eq(
  m.selectionOnModelInput("moonshot", "kimi-k3", {{ model_id: "kimi-k3" }}),
  {{ preset_id: "moonshot", model_id: "kimi-k3" }},
  "matched model_id",
);
// Re-display: preset hit keeps stored metadata (no reset to custom/"").
const catalog = {{ byId: {{ go: {{ provider_id: "go", models: [row] }} }} }};
eq(
  m.lookupSelection(catalog, "retired", "old-model", "old-model"),
  {{ provider: null, row: null, matchedPreset: false }},
  "delisted preset",
);
const hit = m.lookupSelection(catalog, "go", "grok-4.6", "grok-4.6");
if (!hit.matchedPreset || hit.row.model_id !== "grok-4.6") throw new Error("preset hit");
// 3. Thinking clears on non-chat, never re-checks on chat.
if (!m.needsThinkingClear("anthropic_messages", true)) throw new Error("must clear");
if (m.needsThinkingClear("openai_chat", true)) throw new Error("must not clear on chat");
if (m.needsThinkingClear("gemini", false)) throw new Error("must not clear when off");
// 4. Model options show name and id (only once when identical).
const prov = {{ models: [
  {{ model_id: "gpt-4o", model_name: "GPT-4o" }},
  {{ model_id: "kimi-k3", model_name: "kimi-k3" }},
] }};
eq(
  m.modelOptions(prov),
  [{{ value: "gpt-4o", label: "GPT-4o (gpt-4o)" }}, {{ value: "kimi-k3", label: "kimi-k3" }}],
  "model options dedup",
);
// 5. Filter by id or label substring; empty query returns all.
eq(
  m.filterModels(prov, "4o").map((o) => o.value),
  ["gpt-4o"],
  "filter by label",
);
eq(
  m.filterModels(prov, "kimi").map((o) => o.value),
  ["kimi-k3"],
  "filter by id",
);
eq(m.filterModels(prov, "").length, 2, "filter empty");
eq(m.filterModels(prov, "zzz").length, 0, "filter no match");
// 6. Known-model check distinguishes listed from manual/delisted.
if (!m.isKnownModel(prov, "gpt-4o")) throw new Error("known model");
if (m.isKnownModel(prov, "my-custom-model")) throw new Error("unknown model");
if (m.isKnownModel(null, "x")) throw new Error("no provider");
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_model_configs_list_and_add_candidates():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "model_configs.js").resolve().as_uri()
    script = f"""
const m = await import("{module_url}");
const eq = (a, b, msg) => {{
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(msg + ": " + JSON.stringify(a));
}};
const catalog = {{
  providers: [
    {{ provider_id: "moonshot", provider_name: "Moonshot" }},
    {{ provider_id: "openai", provider_name: "OpenAI" }},
    {{ provider_id: "anthropic", provider_name: "Anthropic" }},
    {{ provider_id: "deepseek", provider_name: "DeepSeek" }},
    {{ provider_id: "google", provider_name: "Google Gemini" }},
    {{ provider_id: "opencode-go", provider_name: "OpenCode Go" }},
    {{ provider_id: "some-obscure", provider_name: "Obscure" }},
    {{ provider_id: "custom", provider_name: "Custom" }},
  ],
}};
const services = [
  {{ config_id: "a", name: "Moonshot", model: "kimi-k3", preset_id: "moonshot", has_api_key: true }},
];
// Configured non-Custom provider excluded from add candidates; Custom always addable.
const candidates = m.buildAddCandidates(catalog, services);
eq(candidates.map((c) => c.preset_id), ["openai", "anthropic", "deepseek", "google", "opencode-go", "some-obscure", "custom"], "add candidates");
if (!m.isConfiguredProvider("moonshot", services)) throw new Error("moonshot not configured");
if (m.isConfiguredProvider("openai", services)) throw new Error("openai configured");
// Default common list shows only the common providers (in catalog order), never
// obscure ones or already-configured providers.
eq(
  m.commonProviderCandidates(catalog, services).map((c) => c.preset_id),
  ["openai", "anthropic", "google", "deepseek", "opencode-go"],
  "common candidates",
);
// Search reveals all addable providers by name/id substring; configured excluded.
eq(
  m.searchProviderCandidates(catalog, services, "mo").map((c) => c.preset_id),
  [],
  "search mo excludes configured moonshot",
);
eq(
  m.searchProviderCandidates(catalog, services, "open").map((c) => c.preset_id),
  ["openai", "opencode-go"],
  "search open",
);
eq(
  m.searchProviderCandidates(catalog, services, "OBSCURE").map((c) => c.preset_id),
  ["some-obscure"],
  "search case-insensitive name",
);
// Empty query returns every addable provider.
eq(
  m.searchProviderCandidates(catalog, services, "").map((c) => c.preset_id),
  ["openai", "anthropic", "deepseek", "google", "opencode-go", "some-obscure"],
  "search empty",
);
// Deleted provider becomes addable again.
eq(
  m.buildAddCandidates(catalog, []).map((c) => c.preset_id),
  ["moonshot", "openai", "anthropic", "deepseek", "google", "opencode-go", "some-obscure", "custom"],
  "candidates after delete",
);
// Unknown config_id must not fall back to a service.
if (m.findService(services, "missing") !== null) throw new Error("unknown id found");
if (m.findService(services, "a")?.config_id !== "a") throw new Error("known id not found");
// List rows mark active and carry names.
eq(
  m.listRows(services, "a"),
  [{{ config_id: "a", name: "Moonshot", model: "kimi-k3", preset_id: "moonshot", has_api_key: true, active: true }}],
  "list rows",
);
// A draft needs a test only when new or a connection field changed; renaming a
// Custom alone does not.
if (m.shouldRequireTest(true, false) !== true) throw new Error("new needs test");
if (m.shouldRequireTest(false, true) !== true) throw new Error("changed needs test");
if (m.shouldRequireTest(false, false) !== false) throw new Error("rename only no test");
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_custom_select_option_click_and_search():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r'''
import assert from "node:assert/strict";

function makeEl(tag) {
  const el = {
    tag, className: "", textContent: "", type: "", value: "", hidden: false, disabled: false,
    dataset: {}, attributes: {}, children: [], parent: null, nextElementSibling: null,
    _listeners: {},
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      toggle(c, f) { if (f === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); } else if (f) this._s.add(c); else this._s.delete(c); return this._s.has(c); },
      contains(c) { return this._s.has(c); },
    },
    addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); },
    dispatchEvent(ev) { (this._listeners[ev.type] || []).forEach((fn) => fn.call(this, ev)); },
    setAttribute(k, v) { this.attributes[k] = v; },
    getAttribute(k) { return this.attributes[k]; },
    append(...kids) { for (const k of kids) { k.parent = this; this.children.push(k); } },
    appendChild(k) { k.parent = this; this.children.push(k); },
    replaceChildren(...kids) { this.children = []; for (const k of kids) { k.parent = this; this.children.push(k); } },
    remove() { const p = this.parent; if (p) { const i = p.children.indexOf(this); if (i >= 0) p.children.splice(i, 1); } },
    contains(node) { if (node === this) return true; return this.children.some((c) => c.contains(node)); },
    focus() { this.focused = true; },
    querySelector(sel) { return query(this, sel)[0] || null; },
    querySelectorAll(sel) { return query(this, sel); },
    after(...nodes) { const p = this.parent; if (p) { const i = p.children.indexOf(this); nodes.forEach((n, j) => { n.parent = p; p.children.splice(i + 1 + j, 0, n); }); this.nextElementSibling = nodes[0] || null; } },
    get selectedOptions() { return [this.selectedOption]; },
    get options() { return this.children.filter((c) => c.tag === "option"); },
    get labels() { return [{ textContent: "选择" }]; },
  };
  el.classList.contains = (c) => el.classList._s.has(c) || el.className.split(/\s+/).includes(c);
  return el;
}
function matches(el, sel) {
  if (sel.startsWith(".")) return el.classList.contains(sel.slice(1));
  if (sel.startsWith("#")) return el.dataset.id === sel.slice(1);
  return el.tag === sel;
}
function query(root, sel) {
  const out = [];
  (function walk(node) {
    if (matches(node, sel)) out.push(node);
    for (const c of node.children) walk(c);
  })(root);
  return out;
}
const doc = makeEl("document");
doc.createElement = (tag) => makeEl(tag);
doc.querySelectorAll = (sel) => { const out = []; for (const c of doc.children) query(c, sel).forEach((x) => out.push(x)); return out; };
doc.addEventListener = (t, fn) => { (doc._listeners[t] ||= []).push(fn); };
doc.dispatchEvent = (ev) => { (doc._listeners[ev.type] || []).forEach((fn) => fn.call(doc, ev)); };
globalThis.document = doc;
globalThis.Event = class { constructor(type, opts) { this.type = type; this.bubbles = opts?.bubbles; } };

const root = makeEl("div");
const select = makeEl("select");
root.appendChild(select);
for (const o of [
  { value: "openai", text: "OpenAI" },
  { value: "anthropic", text: "Anthropic" },
  { value: "__search__", text: "搜索全部服务…" },
  { value: "custom", text: "Custom" },
]) {
  const opt = makeEl("option"); opt.value = o.value; opt.textContent = o.text; select.appendChild(opt);
}
select.value = "";
select.dataset.searchable = "true";

const {enhanceCustomSelect, openCustomSelect} = await import(process.argv[1]);

enhanceCustomSelect(select);
const wrapper = select.nextElementSibling;
assert.ok(wrapper.classList.contains("codex-custom-select-wrapper"));
const menu = wrapper.querySelector(".codex-custom-select-menu");
const search = wrapper.querySelector(".codex-custom-select-search");
assert.ok(search, "searchable select renders a search box");

// Opening the menu exposes options and focuses the search box.
openCustomSelect(select);
assert.equal(menu.hidden, false);
assert.equal(search.focused, true);

// Clicking an option must not throw (trigger was out of scope before) and must
// select the value while closing the menu.
let changed = null;
select.addEventListener("change", () => { changed = select.value; });
const firstOption = menu.querySelectorAll(".codex-custom-select-option")[0];
firstOption.onclick({ stopPropagation() {} });
assert.equal(select.value, "openai");
assert.equal(changed, "openai");
assert.equal(menu.hidden, true);

// A change handler that reopens the menu (the "搜索全部服务" entry) keeps it open.
let reopened = false;
select.addEventListener("change", () => {
  if (select.value === "__search__") { openCustomSelect(select); reopened = true; }
});
const searchOption = menu.querySelectorAll(".codex-custom-select-option").find((o) => o.dataset.value === "__search__");
searchOption.onclick({ stopPropagation() {} });
assert.equal(reopened, true);
assert.equal(menu.hidden, false);

// Typing in the search box filters the options.
search.value = "anth";
search.oninput();
assert.deepEqual(menu.querySelectorAll(".codex-custom-select-option").map((o) => o.dataset.value), ["anthropic"]);
'''
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script, (STATIC_DIR / "custom_select.js").resolve().as_uri()],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_player_frontend_protocol_auth_and_saved_draft_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    api_url = (STATIC_DIR / "api.js").resolve().as_uri()
    state_url = (STATIC_DIR / "state.js").resolve().as_uri()
    script = f"""
let token = "desktop-token";
globalThis.document = {{
  querySelector() {{
    return {{ getAttribute() {{ return token; }} }};
  }},
}};
const api = await import("{api_url}");
const state = await import("{state_url}");

const authenticated = api.desktopHeaders({{ Accept: "application/json" }});
if (authenticated["X-LoreLoop-Token"] !== token) {{
  throw new Error("desktop requests must include the session token");
}}
token = "";
if ("X-LoreLoop-Token" in api.desktopHeaders()) {{
  throw new Error("web requests must not include an empty desktop token");
}}

const first = api.parseSSEFrames(
  'data: {{"step":"one"}}\\r\\n\\r\\ndata: {{"step":"two"',
);
if (first.frames[0]?.step !== "one") {{
  throw new Error("complete CRLF frame was not parsed");
}}
const second = api.parseSSEFrames(first.remainder + '}}\\n\\n');
if (second.frames[0]?.step !== "two") {{
  throw new Error("split frame was not reassembled");
}}
const multiline = api.parseSSEFrames(
  'data: {{"step":\\ndata: "three"}}\\n\\n',
);
if (multiline.frames[0]?.step !== "three") {{
  throw new Error("multiline SSE data was not parsed");
}}

const variant = {{
  scene_text: "original",
  saved_scene_text: null,
}};
state.applySavedDraft(variant, {{
  scene_text: "original",
  saved_scene_text: "saved edit",
}});
if (!state.hasDraft(variant) || state.readDraft(variant) !== "saved edit") {{
  throw new Error("saved draft was not preferred over original text");
}}
state.applySavedDraft(variant, {{
  scene_text: "original",
  saved_scene_text: null,
}});
if (state.hasDraft(variant) || state.readDraft(variant) !== "original") {{
  throw new Error("restoring original text did not clear the saved draft");
}}
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_progress_labels_show_next_phase_after_completion():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "progress_labels.js").resolve().as_uri()
    script = f"""
const {{ progressLabel, isConfirmationTailStep, tailProgressLabel }} = await import("{module_url}");
const sceneFacts = progressLabel("scene_facts_done");
if (!sceneFacts.includes("正在更新世界状态")) {{
  throw new Error("scene_facts_done must point at the next phase: " + sceneFacts);
}}
if (sceneFacts === "正在提取场景事实") {{
  throw new Error("scene_facts_done must not keep the stale extracting copy");
}}
if (!progressLabel("state_update_done").includes("回合摘要")) {{
  throw new Error("state_update_done must point at the summary phase");
}}
if (!progressLabel("scene_summarized").includes("并行确认收尾")) {{
  throw new Error("scene_summarized must point at the parallel tail wave");
}}
if (progressLabel("continuity_projected") === "正在整理未决事项") {{
  throw new Error("continuity_projected must not keep the in-progress copy");
}}
if (progressLabel("unknown_step") !== "") {{
  throw new Error("unknown steps must have no label");
}}
if (!isConfirmationTailStep("continuity_projected")) {{
  throw new Error("continuity_projected must be a tail step");
}}
if (isConfirmationTailStep("scene_summarized")) {{
  throw new Error("scene_summarized must not be a tail step");
}}
// The stalled-wave case from the issue: continuity_project stuck while the
// other two finished must name the waiter, not a stale sibling label.
const stuck = tailProgressLabel(["action_preview_done", "npc_profile_updated"]);
if (stuck !== "已完成下一步方向、人物信息 · 正在等待未决事项") {{
  throw new Error("stalled tail must name the waiter: " + stuck);
}}
const first = tailProgressLabel(["continuity_projected"]);
if (first !== "已完成未决事项 · 正在等待下一步方向、人物信息") {{
  throw new Error("partial tail order must follow pipeline order: " + first);
}}
if (tailProgressLabel([]) !== "正在并行确认收尾 · 等待下一步方向、未决事项、人物信息") {{
  throw new Error("empty tail must list all waiters");
}}
if (tailProgressLabel(["action_preview_done", "continuity_projected", "npc_profile_updated"]) !== "并行确认收尾完成 · 正在写入旅程") {{
  throw new Error("finished tail must hand off to the commit phase");
}}
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_timeout_feedback_keeps_server_message_without_model_config_action():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "error_feedback.js").resolve().as_uri()
    script = f"""
const {{ playerErrorFeedback }} = await import("{module_url}");
const backend = "确认回合的模型请求超过120秒，已停止等待。";
const plain = playerErrorFeedback({{ code: "timeout", message: backend }});
if (plain.title !== "模型请求超时") {{
  throw new Error("timeout title mismatch: " + plain.title);
}}
if (plain.message !== backend) {{
  throw new Error("timeout must keep the server message");
}}
if ("action" in plain) {{
  throw new Error("timeout must not suggest opening model settings");
}}
const preserved = playerErrorFeedback(
  {{ code: "timeout", message: backend }},
  "fallback",
  {{ pendingPreserved: true }},
);
if (!preserved.title.includes("待确认内容仍在")) {{
  throw new Error("confirmation timeout must mention preserved pending");
}}
if (!preserved.message.includes(backend) || !preserved.message.includes("仍然保留在界面中")) {{
  throw new Error("confirmation timeout must combine both messages");
}}
if ("action" in preserved) {{
  throw new Error("confirmation timeout must not suggest opening model settings");
}}
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_records_polling_pauses_while_work_is_active():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "records_policy.js").resolve().as_uri()
    script = f"""
const {{ isRecordsPollingPaused, shouldSuppressRecordsPollError }} = await import("{module_url}");
for (const mode of ["generating", "confirming"]) {{
  if (!isRecordsPollingPaused(mode)) throw new Error(mode + " must pause polling");
}}
for (const mode of ["idle", "pending", "error", "booting"]) {{
  if (isRecordsPollingPaused(mode)) throw new Error(mode + " must not pause polling");
}}
if (!shouldSuppressRecordsPollError({{ startedMode: "confirming", currentMode: "pending" }})) {{
  throw new Error("requests started during confirmation must stay silent");
}}
if (!shouldSuppressRecordsPollError({{ startedMode: "idle", currentMode: "generating" }})) {{
  throw new Error("late failures during active work must stay silent");
}}
if (shouldSuppressRecordsPollError({{ startedMode: "idle", currentMode: "pending" }})) {{
  throw new Error("stable-state failures must still surface");
}}
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr

def test_campaign_setup_submit_validation_accepts_player_edits():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "campaign_setup.js").resolve().as_uri()
    script = r'''
const { validateCampaignSetupForSubmit, CAMPAIGN_SETUP_LIMITS } =
  await import("__MODULE_URL__");
const base = {
  currency_unit: "两",
  public_power_baseline: {
    source: "武学",
    limitation: "不能飞升；违者走火入魔。\n（例外极罕见）",
  },
  civilization_baseline: "农耕为主\n手工业辅助",
};
validateCampaignSetupForSubmit(base);
function mustReject(setup, label) {
  try {
    validateCampaignSetupForSubmit(setup);
  } catch (error) {
    if (!error.message.includes(label)) {
      throw new Error("expected " + label + " in: " + error.message);
    }
    return;
  }
  throw new Error("expected rejection for " + label);
}
mustReject({
  ...base,
  public_power_baseline: { ...base.public_power_baseline, limitation: "   " },
}, "力量限制");
mustReject({
  ...base,
  civilization_baseline: "字".repeat(CAMPAIGN_SETUP_LIMITS.civilizationBaseline + 1),
}, "文明与技术基准");
'''.replace("__MODULE_URL__", module_url)
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_initialization_draft_helpers_preserve_unfinished_edits():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "initialization_draft.js").resolve().as_uri()
    script = r'''
const {
  abilityRowHasContent,
  campaignSetupValuesToRestore,
  collectCampaignSetupDraft,
  rawCampaignSetupFields,
} = await import("__MODULE_URL__");
if (!abilityRowHasContent({ name: "", description: "只填了说明" })) {
  throw new Error("description-only ability row must be kept");
}
if (abilityRowHasContent({ name: "  ", description: "  " })) {
  throw new Error("empty ability row must be dropped");
}
const draft = collectCampaignSetupDraft({
  currencyUnit: "",
  publicPowerBaseline: { source: " 武学 ", limitation: "" },
  peoplesAndBeings: "人类\n\n精灵",
  majorWorldConflicts: "",
  dominantPowers: "",
  civilizationBaseline: "",
});
if (draft.currency_unit !== "") throw new Error("blank currency must survive");
if (draft.public_power_baseline.source !== " 武学 ") {
  throw new Error("draft must not trim raw text");
}
if (draft.peoples_and_beings.length !== 3 || draft.peoples_and_beings[1] !== "") {
  throw new Error("blank list lines must survive: " + JSON.stringify(draft.peoples_and_beings));
}
if (rawCampaignSetupFields({ world_name: "艾兰" }).size !== 0) {
  throw new Error("config-only fields must not count as raw setup");
}
if (!rawCampaignSetupFields({ setup_power_limitation: "" }).has("setup_power_limitation")) {
  throw new Error("present setup field must count even when empty");
}
const structured = {
  currency_unit: "银币",
  public_power_baseline: {
    source: "魔法",
    prevalence: "少数人掌握",
    learning_difficulty: "困难",
    limitation: "不能复活死者。",
  },
  peoples_and_beings: ["人类"],
  major_world_conflicts: ["冲突"],
  dominant_powers: ["帝国"],
  civilization_baseline: "冷兵器为主",
};
const partial = campaignSetupValuesToRestore(structured, { setup_power_limitation: "" });
if (partial.setup_currency_unit !== "银币") {
  throw new Error("absent raw field must fall back to structured value");
}
if (partial.setup_power_source !== "魔法") {
  throw new Error("partial raw fields must not wipe other structured values");
}
if ("setup_power_limitation" in partial) {
  throw new Error("present raw field must be preserved even when empty");
}
const allRaw = campaignSetupValuesToRestore(
  structured,
  Object.fromEntries([
    "setup_currency_unit",
    "setup_power_source",
    "setup_power_prevalence",
    "setup_power_learning_difficulty",
    "setup_power_limitation",
    "setup_peoples_and_beings",
    "setup_major_world_conflicts",
    "setup_dominant_powers",
    "setup_civilization_baseline",
  ].map((name) => [name, ""])),
);
if (Object.keys(allRaw).length !== 0) {
  throw new Error("fully raw drafts must not be overwritten by the snapshot");
}
'''.replace("__MODULE_URL__", module_url)
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_confirmation_failure_checks_commit_instead_of_pending_ui():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r'''
import assert from "node:assert/strict";
const state = {activeSave: "save-1"};
const variant = {turn_id: "variant-1"};
const activePending = () => ({variants: [variant]});
const selectedVariant = () => variant;
const sessionStorage = {setItem() {}, removeItem() {}};
const CONFIRMATION_IN_FLIGHT_KEY = "confirmation";
const setMode = () => {}, renderPending = () => {}, renderControls = () => {}, renderAll = () => {};
const readDraft = () => "text", newRequestId = () => "request";
const handleConfirmationEvent = () => {}, acknowledgeHandledOperation = async () => {};
const acknowledgeFailedOperation = async () => {}, refreshRecords = () => {};
const refreshRecordsAfterTurn = () => {}, showToast = () => {};
let feedback, streams = 0, disconnected = false, lookup = "unknown", refreshFails = false;
const showError = (error, fallback, options) => { feedback = {error, ...options}; };
const stream = async () => { streams++; if (disconnected) throw new Error("disconnected"); return "op"; };
const request = async () => ({outcome: lookup});
const loadAll = async () => { if (refreshFails) throw new Error("refresh failed"); };
const tokenUsage = {getVariantUsage: () => null, getCurrentPendingUsage: () => ({}),
  saveFullVariantUsage() {}, cleanUpUnconfirmedVariants() {}, clearPendingUsage() {}};
const {createConfirmationActions} = await import("__MODULE_URL__");
const {confirmTurn} = createConfirmationActions({
  state, activePending, selectedVariant, sessionStorage, CONFIRMATION_IN_FLIGHT_KEY,
  setMode, renderPending, renderControls, renderAll, readDraft, newRequestId,
  handleConfirmationEvent, acknowledgeHandledOperation, acknowledgeFailedOperation,
  refreshRecords, refreshRecordsAfterTurn, showToast, showError, stream, subscribe: async () => {},
  request, loadAll, tokenUsage
});
refreshFails = true;
await confirmTurn();
assert.equal(feedback.confirmationOutcome, "confirmed");
assert.equal(feedback.error.message, "refresh failed");
assert.equal(streams, 1);
refreshFails = false;
disconnected = true;
await confirmTurn();
assert.equal(feedback.confirmationOutcome, "unknown");
const previous = streams;
await confirmTurn();
assert.equal(streams, previous, "unknown outcome must not resubmit confirmation");
lookup = "confirmed";
await feedback.recheckConfirmation();
assert.equal(feedback.confirmationOutcome, "confirmed");
'''.replace("__MODULE_URL__", (STATIC_DIR / "confirmation.js").resolve().as_uri())
    result = subprocess.run([node, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_pending_feedback_preserves_backend_cause_and_confirmation_truth():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r'''
import assert from "node:assert/strict";
const {playerErrorFeedback} = await import(process.argv[1]);
for (const code of ["internal_error", "operation_failed", "file_access_failed", "timeout", "validation_error", "auth_failed"]) {
  const error = {code, message: "真实原因：测试文件无法写入"};
  const result = playerErrorFeedback(error, undefined, {pendingPreserved:true});
  assert.ok(result.message.includes(error.message), code);
  assert.ok(!result.message.includes("没有写入正式故事"), code);
}
const error = {code:"internal_error", message:"刷新失败"};
const confirmed = playerErrorFeedback(error, undefined, {confirmationOutcome:"confirmed"});
assert.ok(confirmed.message.includes("正式故事已写入"));
assert.ok(confirmed.message.includes(error.message));
const unknown = playerErrorFeedback(error, undefined, {confirmationOutcome:"unknown"});
assert.ok(unknown.message.includes("暂勿重复确认"));
assert.ok(!unknown.message.includes("没有写入"));
'''
    result = subprocess.run([node, "--input-type=module", "--eval", script,
                             (STATIC_DIR / "error_feedback.js").resolve().as_uri()], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_provider_feedback_adds_guidance_once_without_replacing_backend_details():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r'''
import assert from "node:assert/strict";
const {playerErrorFeedback} = await import(process.argv[1]);
const cases = [
  ["auth_failed", "API Key 无效或没有访问权限", "请前往【设置与检查】更新密钥。"],
  ["permission_denied", "API Key 有效，但没有访问当前服务或模型的权限", "请前往【设置与检查】检查配置或更换模型。"],
  ["model_not_found", "服务可访问，但没有找到指定模型", "请前往【设置与检查】核对模型名称。"],
  ["thinking_unsupported", "当前服务或模型不支持所选 Thinking 设置", "请前往【设置与检查】关闭 Thinking。"],
  ["rate_limited", "服务当前触发了速率限制", "请稍后重试，或检查服务配额。"],
  ["endpoint_unreachable", "无法连接 Base URL，请检查地址和网络", "如使用代理或本地模型服务，也请核对相应配置。"],
];
for (const [code, reason, guidance] of cases) {
  const backend = `${reason}\n诊断编号：demo；本地记录：demo.log`;
  for (const options of [{}, {confirmationOutcome:"not_confirmed"}, {confirmationOutcome:"unknown"}]) {
    const feedback = playerErrorFeedback({code, message:backend}, undefined, options);
    assert.ok(feedback.message.includes(backend), code);
    assert.equal(feedback.message.split(reason).length - 1, 1, code);
    assert.equal(feedback.message.split(guidance).length - 1, 1, code);
    const repeated = playerErrorFeedback({code, message:`${backend}\n${guidance}`}, undefined, options);
    assert.equal(repeated.message.split(guidance).length - 1, 1, code);
  }
  const fallback = playerErrorFeedback({code});
  assert.equal(fallback.message.split(guidance).length - 1, 1, code);
}
const unknownProvider = {code:"provider_error", message:"服务返回特殊错误\n诊断编号：demo"};
assert.equal(playerErrorFeedback(unknownProvider).message, unknownProvider.message);
'''
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script,
         (STATIC_DIR / "error_feedback.js").resolve().as_uri()],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_notice_is_visible_and_replay_deduplicates():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r'''
import assert from "node:assert/strict";
const state = {runtimeNotices: []};
const elements = Object.fromEntries(["runtimeNotices", "runtimeNoticeSummary", "runtimeNoticeList"].map(id => [id, {
  hidden:true, textContent:"", children:[], replaceChildren(...items) { this.children = items; },
}]));
const $ = id => elements[id];
const document = {createElement: () => ({textContent:""})};
const {createRuntimeNotices} = await import("__MODULE_URL__");
const {renderRuntimeNotices, handleRuntimeNotice} = createRuntimeNotices({state, $, document});
const event = {step:"runtime_notice", data:{notice_id:"op:check", created_at:"2026-10-21T00:00:00Z", message:"正文已生成，本次一致性检查未完成"}};
assert.equal(handleRuntimeNotice(event), true);
assert.equal(elements.runtimeNotices.hidden, false);
assert.ok(elements.runtimeNoticeSummary.textContent.includes("检查未完成"));
handleRuntimeNotice(event);
assert.equal(elements.runtimeNoticeList.children.length, 1);
state.runtimeNotices = JSON.parse(JSON.stringify(state.runtimeNotices));
renderRuntimeNotices();
assert.ok(elements.runtimeNoticeList.children[0].textContent.includes("检查未完成"));
assert.equal(handleRuntimeNotice({step:"narrative_checked", data:{}}), false);
'''.replace("__MODULE_URL__", (STATIC_DIR / "runtime_notices.js").resolve().as_uri())
    result = subprocess.run([node, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_resumed_confirmation_refresh_failure_keeps_success_fact():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r'''
import assert from "node:assert/strict";
const state = {activeSave:"save-1"};
const sessionStorage = {setItem() {}, removeItem() {}};
const CONFIRMATION_IN_FLIGHT_KEY = "confirmation";
const setMode = () => {}, renderPending = () => {}, renderControls = () => {};
const subscribe = async () => {}, handleConfirmationEvent = () => {};
const acknowledgeHandledOperation = async () => {}, acknowledgeFailedOperation = async () => {};
const refreshRecords = () => {}, refreshRecordsAfterTurn = () => {}, showToast = () => {};
const loadAll = async () => { throw new Error("refresh failed"); };
let result;
const showError = (error, fallback, options) => { result = {error, ...options}; };
const {createConfirmationActions} = await import("__MODULE_URL__");
const {resumeConfirmationOperation} = createConfirmationActions({
  state, sessionStorage, CONFIRMATION_IN_FLIGHT_KEY, setMode, renderPending, renderControls,
  subscribe, handleConfirmationEvent, acknowledgeHandledOperation, acknowledgeFailedOperation,
  refreshRecords, refreshRecordsAfterTurn, showToast, loadAll, showError, renderAll: () => {},
});
await resumeConfirmationOperation({operation_id:"op", metadata:{turn_id:"variant-1"}});
assert.equal(result.confirmationOutcome, "confirmed");
assert.equal(result.error.message, "refresh failed");
'''.replace("__MODULE_URL__", (STATIC_DIR / "confirmation.js").resolve().as_uri())
    result = subprocess.run([node, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_records_requests_carry_selected_view():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r'''
import assert from "node:assert/strict";
const {loadRecords, updateRecord} = await import(process.argv[1]);
globalThis.window = {setTimeout, clearTimeout};
globalThis.document = {querySelector: () => null};
const calls = [];
globalThis.fetch = async (path, options) => {
  calls.push({path, options});
  return {ok:true, json:async () => ({ok:true, data:{}})};
};
await loadRecords();
await loadRecords("advanced");
await updateRecord("npc", "npc-1", {motivation:"守城"}, "advanced");
assert.equal(calls[0].path, "/api/player/records?view=general");
assert.equal(calls[1].path, "/api/player/records?view=advanced");
assert.deepEqual(JSON.parse(calls[2].options.body), {
  kind:"npc", record_id:"npc-1", view:"advanced", payload:{motivation:"守城"},
});
'''
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script,
         (STATIC_DIR / "api.js").resolve().as_uri()], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
