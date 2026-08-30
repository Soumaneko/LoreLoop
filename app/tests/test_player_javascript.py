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
