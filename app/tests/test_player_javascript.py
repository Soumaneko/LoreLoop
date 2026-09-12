import pytest

import shutil
import subprocess

from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "player_static"


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
    for filename in (
        "api.js",
        "app.js",
        "campaign_setup.js",
        "client_diagnostics.js",
        "custom_select.js",
        "error_feedback.js",
        "model_combobox.js",
        "model_configs.js",
        "model_selection.js",
        "prepaint.js",
        "popover_fallback.js",
        "progress_labels.js",
        "reader_preferences.js",
        "records_policy.js",
        "setting_info.js",
        "state.js",
        "storage_migration.js",
    ):
        source = (STATIC_DIR / filename).read_text(encoding="utf-8")
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
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
const src = fs.readFileSync(process.argv[1], "utf8");

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

const ctx = { document: doc, Event: globalThis.Event };
vm.createContext(ctx);
vm.runInContext(src.replace(/export /g, ""), ctx);

ctx.enhanceCustomSelect(select);
const wrapper = select.nextElementSibling;
assert.ok(wrapper.classList.contains("codex-custom-select-wrapper"));
const menu = wrapper.querySelector(".codex-custom-select-menu");
const search = wrapper.querySelector(".codex-custom-select-search");
assert.ok(search, "searchable select renders a search box");

// Opening the menu exposes options and focuses the search box.
ctx.openCustomSelect(select);
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
  if (select.value === "__search__") { ctx.openCustomSelect(select); reopened = true; }
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
        [node, "--input-type=module", "--eval", script, str(STATIC_DIR / "custom_select.js")],
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
if (!preserved.message.includes(backend) || !preserved.message.includes("没有写入正式故事")) {{
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

    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    start = source.index("function startRecordsPoll()")
    body = source[start:source.index("\n}", start)]
    assert body.index("isRecordsPollingPaused") < body.index("stopRecordsPoll")


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
