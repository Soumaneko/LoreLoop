import pytest

import shutil
import subprocess

from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "player_static"


def test_player_frontend_javascript_is_syntactically_valid():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    for filename in (
        "api.js",
        "app.js",
        "client_diagnostics.js",
        "custom_select.js",
        "error_feedback.js",
        "prepaint.js",
        "popover_fallback.js",
        "reader_preferences.js",
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


def test_player_error_feedback_uses_explicit_pending_context():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "error_feedback.js").resolve().as_uri()
    script = f"""
const {{ playerErrorFeedback }} = await import("{module_url}");

const pending = playerErrorFeedback(
  new Error("boom"),
  undefined,
  {{ pendingPreserved: true }},
);
if (pending.title !== "操作未完成，待确认内容仍在") {{
  throw new Error("pending-preserved error must keep the pending title");
}}
if (pending.message !== "本次操作没有写入正式故事，待确认正文仍然保留在界面中。") {{
  throw new Error("pending-preserved error must keep the pending-safe description");
}}

const generic = playerErrorFeedback(new Error("boom"));
if (generic.title !== "这次回应没有完成") {{
  throw new Error("generic error must keep the neutral title");
}}

const unrelatedWithPending = playerErrorFeedback(
  new Error("boom"),
  undefined,
  {{ pendingPreserved: false }},
);
if (unrelatedWithPending.title !== "这次回应没有完成") {{
  throw new Error("unrelated errors must not borrow pending context");
}}

const validation = playerErrorFeedback({{
  code: "validation_error",
  message: "Operation not found",
}});
if (validation.title !== "这次操作没有完成") {{
  throw new Error("validation_error must use a neutral title");
}}
if (validation.message !== "Operation not found") {{
  throw new Error("validation_error must keep the server message");
}}

const connection = playerErrorFeedback({{
  code: "connection_failed",
  message: "无法连接本地服务。",
}});
if (connection.title !== "连接出现问题") {{
  throw new Error("connection_failed must use the connection title");
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


def test_player_saved_edits_are_projected_without_browser_storage():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "state.js").resolve().as_uri()
    script = f"""
const module = await import("{module_url}");
const variant = {{
  turn_id: "turn-1",
  scene_text: "原始正文",
  saved_scene_text: null,
}};
if (module.hasDraft(variant)) throw new Error("fresh variant must not be edited");
module.applySavedDraft(variant, {{
  scene_text: "原始正文",
  saved_scene_text: "玩家修改",
}});
if (!module.hasDraft(variant) || module.readDraft(variant) !== "玩家修改") {{
  throw new Error("server-projected edit was not selected");
}}
module.applySavedDraft(variant, {{
  scene_text: "原始正文",
  saved_scene_text: null,
}});
if (module.hasDraft(variant) || module.readDraft(variant) !== "原始正文") {{
  throw new Error("original text was not restored");
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


def test_one_click_confirm_save_updates_state_optimistically():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "reader_preferences.js").resolve().as_uri()
    script = f"""
globalThis.document = {{
  documentElement: {{
    style: {{ setProperty() {{}}, removeAttribute() {{}} }},
    dataset: {{}},
    removeAttribute() {{}},
  }},
  querySelector() {{ return null; }},
}};
globalThis.localStorage = {{ setItem() {{}}, getItem() {{ return null; }} }};

function stubElement() {{
  return {{ value: "16.5", checked: false, nextElementSibling: null }};
}}
const elements = {{
  fontSize: stubElement(),
  fontFamily: stubElement(),
  theme: stubElement(),
  tokenUsage: stubElement(),
  oneClickConfirm: stubElement(),
}};

const resolvers = [];
let requestCount = 0;
function request() {{
  return new Promise((resolve) => {{
    resolvers.push({{ resolve, index: requestCount++ }});
  }});
}}

const state = {{
  readerPreferences: {{
    font_size: "16.5", font_family: "serif", theme: "dark",
    show_token_usage: false, one_click_confirm: false,
  }},
}};

const {{ createReaderPreferencesController }} = await import("{module_url}");
const controller = createReaderPreferencesController({{
  elements, request, state,
  showToast() {{}}, showError() {{}},
}});

function echo(one_click_confirm) {{
  return {{
    font_size: "16.5", font_family: "serif", theme: "dark",
    show_token_usage: false, one_click_confirm,
  }};
}}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

elements.oneClickConfirm.checked = true;
const saveOn = controller.save({{ silent: true }});
if (state.readerPreferences.one_click_confirm !== true) {{
  throw new Error("save() must update state optimistically before the server responds");
}}

elements.oneClickConfirm.checked = false;
const saveOff = controller.save({{ silent: true }});
if (state.readerPreferences.one_click_confirm !== false) {{
  throw new Error("a newer optimistic save must win immediately");
}}

while (resolvers.length < 1) await tick();
resolvers[0].resolve(echo(true));
while (resolvers.length < 2) await tick();
if (state.readerPreferences.one_click_confirm !== false) {{
  throw new Error("stale echo from an older save must not clobber the newer value");
}}
resolvers[1].resolve(echo(false));
await saveOn;
await saveOff;
if (state.readerPreferences.one_click_confirm !== false) {{
  throw new Error("final saved state must reflect the latest toggle");
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


def test_pending_footer_mode_keeps_version_control_when_token_usage_is_missing():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "token_usage.js").resolve().as_uri()
    script = f"""
const {{ pendingFooterMode }} = await import("{module_url}");

function mode(overrides) {{
  return pendingFooterMode({{
    showToken: true,
    showVersions: true,
    usageData: {{ generation: null, confirmation: null }},
    ...overrides,
  }});
}}

// Opening variant 1/x: token usage never recorded but multiple versions exist.
if (mode({{}}) !== "versions") throw new Error("1/x must keep version control");
// Opening variant 2/x: real generation usage present.
if (mode({{ usageData: {{ generation: {{ input_tokens: 1 }}, confirmation: null }} }}) !== "token") {{
  throw new Error("2/x must render token footer");
}}
// Single variant without usage and token hidden: no footer at all.
if (pendingFooterMode({{ showToken: false, showVersions: false, usageData: null }}) !== "none") {{
  throw new Error("nothing to show must be none");
}}
// Token visible but no usage and single variant: nothing to render.
if (pendingFooterMode({{ showToken: true, showVersions: false, usageData: null }}) !== "none") {{
  throw new Error("no usage and single variant must be none");
}}
// Token hidden but multiple versions: version control only.
if (pendingFooterMode({{ showToken: false, showVersions: true, usageData: null }}) !== "versions") {{
  throw new Error("version control must render without token footer");
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


def test_legacy_browser_storage_keys_migrate_once():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    migration_url = (STATIC_DIR / "storage_migration.js").resolve().as_uri()
    prepaint_url = (STATIC_DIR / "prepaint.js").resolve().as_uri()
    script = f"""
class Storage {{
  constructor(entries = []) {{ this.values = new Map(entries); }}
  getItem(key) {{ return this.values.has(key) ? this.values.get(key) : null; }}
  setItem(key, value) {{ this.values.set(key, String(value)); }}
  removeItem(key) {{ this.values.delete(key); }}
}}
const {{ migrateStorageKey }} = await import("{migration_url}");
const session = new Storage([["tvg:player-shell", "legacy-shell"]]);
if (!migrateStorageKey(
  session,
  "tvg:player-shell",
  "loreloop:player-shell",
)) throw new Error("session migration failed");
if (session.getItem("loreloop:player-shell") !== "legacy-shell") {{
  throw new Error("legacy session value was not copied");
}}
if (session.getItem("tvg:player-shell") !== null) {{
  throw new Error("legacy session key was not removed");
}}
const newer = new Storage([
  ["tvg:generation-in-flight", "legacy"],
  ["loreloop:generation-in-flight", "current"],
]);
migrateStorageKey(
  newer,
  "tvg:generation-in-flight",
  "loreloop:generation-in-flight",
);
if (newer.getItem("loreloop:generation-in-flight") !== "current") {{
  throw new Error("current session value was overwritten");
}}

globalThis.localStorage = new Storage([[
  "tvg:reader-preferences",
  JSON.stringify({{
    font_size: "18",
    font_family: "kaiti",
    theme: "sepia",
    theme_color: "#e6ddcc",
  }}),
]]);
const root = {{
  dataset: {{}},
  style: {{ setProperty(name, value) {{ this[name] = value; }} }},
}};
const themeMeta = {{ content: "#11110e" }};
globalThis.document = {{
  documentElement: root,
  querySelector() {{ return themeMeta; }},
}};
await import("{prepaint_url}");
if (localStorage.getItem("tvg:reader-preferences") !== null) {{
  throw new Error("legacy reader key was not removed");
}}
if (localStorage.getItem("loreloop:reader-preferences") === null) {{
  throw new Error("reader preferences were not migrated");
}}
if (root.dataset.theme !== "sepia" || root.dataset.fontFamily !== "kaiti") {{
  throw new Error("migrated reader preferences were not applied");
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


def test_player_sse_parser_handles_split_crlf_and_multiline_frames():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "api.js").resolve().as_uri()
    script = f"""
const {{ parseSSEFrames }} = await import("{module_url}");
const first = parseSSEFrames(
  'data: {{"ok":true,"step":"one"}}\\r\\n\\r\\ndata: {{"ok":true',
);
if (first.frames.length !== 1 || first.frames[0].step !== "one") {{
  throw new Error("complete CRLF frame was not parsed");
}}
const second = parseSSEFrames(
  first.remainder + ',"step":"two"}}\\n\\n',
);
if (second.frames.length !== 1 || second.frames[0].step !== "two") {{
  throw new Error("split frame was not reassembled");
}}
const multiline = parseSSEFrames(
  'data: {{"ok":true,\\ndata: "step":"three"}}\\n\\n',
);
if (multiline.frames[0].step !== "three") {{
  throw new Error("multiline data frame was not parsed");
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


def test_player_api_sends_desktop_session_token_when_present():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "api.js").resolve().as_uri()
    script = f"""
globalThis.window = globalThis;
globalThis.document = {{
  querySelector: () => ({{ getAttribute: () => "desktop-token" }}),
}};
let captured = null;
globalThis.fetch = async (path, options) => {{
  captured = {{ path, options }};
  return {{
    ok: true,
    status: 200,
    json: async () => ({{ ok: true, data: {{ value: 1 }} }}),
  }};
}};
const {{ cancelOperation, request }} = await import("{module_url}");
await request("/api/player/state");
if (captured.options.headers["X-LoreLoop-Token"] !== "desktop-token") {{
  throw new Error("desktop token header was not attached");
}}
await cancelOperation("operation-1");
if (captured.path !== "/api/player/operations/operation-1/cancel") {{
  throw new Error("cancel operation used the wrong endpoint");
}}
if (captured.options.headers["X-LoreLoop-Token"] !== "desktop-token") {{
  throw new Error("cancel operation did not inherit the desktop token");
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


def test_player_api_omits_empty_desktop_token_in_web_mode():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "api.js").resolve().as_uri()
    script = f"""
globalThis.document = {{
  querySelector: () => ({{ getAttribute: () => "" }}),
}};
const {{ desktopHeaders }} = await import("{module_url}");
const headers = desktopHeaders({{ Accept: "application/json" }});
if (headers.Accept !== "application/json") {{
  throw new Error("existing web headers were not preserved");
}}
if ("X-LoreLoop-Token" in headers) {{
  throw new Error("web mode must not send an empty desktop token");
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


def test_setting_info_popover_shows_switches_and_closes_with_native_and_fallback():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "setting_info.js").resolve().as_uri()
    script = f"""
globalThis.window = {{ innerWidth: 1280, innerHeight: 720 }};
const docListeners = {{}};
globalThis.document = {{
  addEventListener(type, handler) {{ docListeners[type] = handler; }},
}};

function stubElement(rect) {{
  const attrs = {{}};
  const listeners = {{}};
  return {{
    textContent: "",
    style: {{}},
    attrs,
    listeners,
    addEventListener(name, handler) {{ listeners[name] = handler; }},
    setAttribute(name, value) {{ attrs[name] = value; }},
    removeAttribute(name) {{ delete attrs[name]; }},
    hasAttribute(name) {{ return name in attrs; }},
    contains(other) {{ return other === this; }},
    getBoundingClientRect() {{
      return rect || {{ top: 0, bottom: 24, left: 10, width: 20, height: 20 }};
    }},
  }};
}}

const {{ bindSettingInfoButtons, bindSettingInfoDismiss }} = await import("{module_url}");

function makeNativeDialog() {{
  const calls = [];
  const dialog = stubElement({{ top: 0, bottom: 0, left: 0, width: 120, height: 60 }});
  dialog.showPopover = () => calls.push("show");
  dialog.hidePopover = () => calls.push("hide");
  return {{ dialog, calls }};
}}

const titleEl = stubElement();
const contentEl = stubElement();
const buttons = {{ canon: stubElement(), usage: stubElement(), oneClick: stubElement() }};
const specs = {{
  canon: {{ title: "Canon 后校验", body: "关闭后可能产生与已确定事实冲突的内容。" }},
  usage: {{ title: "Token 用量显示", body: "关闭后不影响生成质量或实际成本。" }},
  oneClick: {{ title: "一键确认", body: "正文生成后自动确认。" }},
}};

const native = makeNativeDialog();
const controller = bindSettingInfoButtons({{
  dialog: native.dialog, titleEl, contentEl, buttons, specs,
}});
bindSettingInfoDismiss(native.dialog, () => controller.hide());

// 1) clicking an info button shows its content and opens the popover
buttons.canon.onclick();
if (titleEl.textContent !== "Canon 后校验") throw new Error("title was not set");
if (contentEl.textContent !== specs.canon.body) throw new Error("body was not shown");
if (!native.calls.includes("show")) throw new Error("native showPopover was not called");
if (!native.dialog.style.top) throw new Error("popover was not positioned");

// 2) switching between buttons updates content and re-opens
buttons.oneClick.onclick();
if (contentEl.textContent !== specs.oneClick.body) throw new Error("switch did not update content");
buttons.canon.onclick();
if (contentEl.textContent !== specs.canon.body) throw new Error("re-open did not restore previous content");
if (native.calls.filter((c) => c === "show").length !== 3) throw new Error("re-show was not triggered per click");

// 3) close behavior via backdrop click and explicit hide
native.dialog.listeners.click({{ target: native.dialog }});
if (!native.calls.includes("hide")) throw new Error("backdrop click did not hide");
controller.hide();
if (native.calls.filter((c) => c === "hide").length < 2) throw new Error("hide did not call hidePopover");

// 4) fallback without Popover API uses [open] and closes by removing it
const fallbackDialog = stubElement({{ top: 0, bottom: 0, left: 0, width: 120, height: 60 }});
const fbTitle = stubElement();
const fbContent = stubElement();
const fbButtons = {{ one: stubElement() }};
const fbController = bindSettingInfoButtons({{
  dialog: fallbackDialog, titleEl: fbTitle, contentEl: fbContent,
  buttons: fbButtons, specs: {{ one: specs.oneClick }},
}});
bindSettingInfoDismiss(fallbackDialog, () => fbController.hide());
fbButtons.one.onclick();
if (fallbackDialog.attrs.open !== "") throw new Error("fallback must set [open] to show");
if (fbTitle.textContent !== "一键确认") throw new Error("fallback content was not set");
fbController.hide();
if ("open" in fallbackDialog.attrs) throw new Error("fallback hide must remove [open]");

// 5) fallback closes on outside click and on Escape
fbButtons.one.onclick();
if (!fallbackDialog.hasAttribute("open")) throw new Error("fallback did not reopen");
docListeners.click({{ target: {{ outside: true }} }});
if (fallbackDialog.hasAttribute("open")) throw new Error("fallback outside click did not close");
fbButtons.one.onclick();
docListeners.keydown({{ key: "Escape" }});
if (fallbackDialog.hasAttribute("open")) throw new Error("fallback Escape did not close");

// 6) regression: the opening click must not be seen as an outside click
const openEvent = {{ target: fbButtons.one, stopped: false, stopPropagation() {{ this.stopped = true; }} }};
fbButtons.one.onclick(openEvent);
if (!openEvent.stopped) throw new Error("info button click must stop propagation");
if (!fallbackDialog.hasAttribute("open")) throw new Error("opening click must keep the popover open");
const nativeOpenEvent = {{ target: buttons.canon, stopped: false, stopPropagation() {{ this.stopped = true; }} }};
buttons.canon.onclick(nativeOpenEvent);
if (!nativeOpenEvent.stopped) throw new Error("native info button click must stop propagation");
if (titleEl.textContent !== "Canon 后校验") throw new Error("native button content was not set");
const hideCountBefore = native.calls.filter((c) => c === "hide").length;
docListeners.click(nativeOpenEvent);
const hideCountAfter = native.calls.filter((c) => c === "hide").length;
if (hideCountAfter !== hideCountBefore) throw new Error("native outside click while closed must not fire");

// 7) plain outside click still closes the fallback after an explicit hide/reopen cycle
fbController.hide();
fbButtons.one.onclick();
if (!fallbackDialog.hasAttribute("open")) throw new Error("fallback did not reopen for outside close");
docListeners.click({{ target: {{ outside: true }} }});
if (fallbackDialog.hasAttribute("open")) throw new Error("outside click must close the reopened fallback");
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_token_usage_dialog_opens_natively_and_via_open_fallback():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "token_usage.js").resolve().as_uri()
    script = f"""
globalThis.window = {{ innerWidth: 1280, innerHeight: 720 }};
function stubEl(rect) {{
  const attrs = {{}};
  const children = [];
  return {{
    textContent: "",
    className: "",
    style: {{}},
    children,
    attrs,
    setAttribute(name, value) {{ attrs[name] = value; }},
    removeAttribute(name) {{ delete attrs[name]; }},
    hasAttribute(name) {{ return name in attrs; }},
    replaceChildren() {{ children.length = 0; }},
    appendChild(child) {{ children.push(child); }},
    append(...items) {{ children.push(...items); }},
    getBoundingClientRect() {{
      return rect || {{ top: 0, bottom: 0, left: 0, width: 120, height: 60 }};
    }},
  }};
}}
globalThis.document = {{ createElement() {{ return stubEl(); }} }};

const {{ openTokenUsagePipelineDialog }} = await import("{module_url}");

const usage = {{
  generation: {{ calls: [{{ task: "generation", input_tokens: 10, output_tokens: 5 }}] }},
  confirmation: {{ calls: [] }},
}};

// native Popover API: showPopover is called, [open] is never set, content renders
const nativeDialog = stubEl();
const nativeCalls = [];
nativeDialog.showPopover = () => nativeCalls.push("show");
const nativeContent = stubEl();
openTokenUsagePipelineDialog({{
  dialog: nativeDialog, contentEl: nativeContent,
  usageData: usage, pipeline: "generation", anchorEl: stubEl(),
}});
if (!nativeCalls.includes("show")) throw new Error("native showPopover was not called");
if (nativeDialog.hasAttribute("open")) throw new Error("native mode must not set [open]");
if (nativeContent.children.length === 0) throw new Error("usage rows were not rendered");

// fallback without Popover API: [open] is set, empty-calls branch does not throw
const fallbackDialog = stubEl();
const fallbackContent = stubEl();
openTokenUsagePipelineDialog({{
  dialog: fallbackDialog, contentEl: fallbackContent,
  usageData: usage, pipeline: "confirmation", anchorEl: null,
}});
if (fallbackDialog.attrs.open !== "") throw new Error("fallback must set [open] to show");
fallbackDialog.removeAttribute("open");
if (fallbackDialog.hasAttribute("open")) throw new Error("fallback close must remove [open]");
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
