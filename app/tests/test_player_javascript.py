import pytest
import shutil
import subprocess
from pathlib import Path


STATIC_DIR = Path(__file__).parents[1] / "player_static"


def _run_js(script, *arguments):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script, *arguments],
        capture_output=True, text=True, encoding="utf-8",
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
    _run_js(script)


def test_model_selection_row_switch_and_metadata_behaviors():
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
  m.modelOptions(prov).map((option) => option.value),
  ["gpt-4o", "kimi-k3"],
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
    _run_js(script)


def test_model_configs_list_and_add_candidates():
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
    _run_js(script)


def test_player_frontend_protocol_auth_and_saved_draft_behaviors():
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
    _run_js(script)


def test_records_polling_pauses_while_work_is_active():
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
    _run_js(script)

def test_campaign_setup_submit_validation_accepts_player_edits():
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
    _run_js(script)


def test_initialization_draft_helpers_preserve_unfinished_edits():
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
    _run_js(script)


def test_confirmation_failure_checks_commit_instead_of_pending_ui():
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
    _run_js(script)


def test_pending_feedback_preserves_backend_cause():
    script = r'''
import assert from "node:assert/strict";
const {playerErrorFeedback} = await import(process.argv[1]);
for (const code of ["internal_error", "operation_failed", "file_access_failed", "timeout", "validation_error", "auth_failed"]) {
  const error = {code, message: "真实原因：测试文件无法写入"};
  for (const options of [{pendingPreserved:true}, {confirmationOutcome:"confirmed"}, {confirmationOutcome:"unknown"}]) {
    const result = playerErrorFeedback(error, undefined, options);
    assert.ok(result.message.includes(error.message), code);
  }
}
'''
    _run_js(script, (STATIC_DIR / "error_feedback.js").resolve().as_uri())


def test_resumed_confirmation_refresh_failure_keeps_success_fact():
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
    _run_js(script)


def test_records_requests_carry_selected_view():
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
    _run_js(script, (STATIC_DIR / "api.js").resolve().as_uri())
