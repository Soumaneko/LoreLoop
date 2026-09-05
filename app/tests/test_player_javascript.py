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
        "model_selection.js",
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
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
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
