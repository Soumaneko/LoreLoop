import io
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from app import debug_server
from app.debug_server import DebugHandler
from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    PlayerState,
    ResourcesState,
    SceneState,
    TimedCondition,
    WorldClock,
)
from app.domain.entity.schemas import EntityProfile
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.turn_resolution import PlayerTurnResolution
from app.runtime.player_state import (
    load_player_history,
    load_player_state,
    player_saves_payload,
)
from app.runtime.operations import OperationManager
from app.runtime.session import get_campaign_runtime
from app.runtime.settings import (
    load_settings,
    save_dice_display,
    save_reader_preferences,
)


STATIC_DIR = Path(__file__).parents[1] / "player_static"


class _DisconnectedWriter:
    def write(self, _payload):
        raise BrokenPipeError

    def flush(self):
        pass


def _action_preview() -> ActionPreviewResult:
    return ActionPreviewResult(previews=[
        {"level": "追击施压", "label": "追上去", "good_result": "追上目标。", "risk": "可能暴露。"},
        {"level": "静待时机", "label": "留在原地", "good_result": "保持安全。", "risk": "可能错失机会。"},
        {"level": "痕迹追查", "label": "检查痕迹", "good_result": "发现线索。", "risk": "会消耗时间。"},
        {"level": "声东击西", "label": "制造声响", "good_result": "引开注意。", "risk": "可能惊动更多人。"},
    ])


def _player_resolution() -> PlayerTurnResolution:
    return PlayerTurnResolution(actions=[{
        "action_text": "追问木偶师",
        "resolution_source": "roll",
        "outcome_band": "partial_success",
        "dice": "2d20kh",
        "raw_values": [7, 11],
        "modifiers": [{
            "label": "对方戒备",
            "reason": "木偶师对直接追问有所防备。",
            "value": -1,
        }],
        "modifier_total": -1,
        "final_value": 10,
        "dice_sides": 20,
    }])


def test_player_frontend_javascript_is_syntactically_valid():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    for filename in ("api.js", "state.js", "app.js"):
        result = subprocess.run(
            [node, "--check", str(STATIC_DIR / filename)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_player_drafts_can_restore_original_across_reload_and_save_switch():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (STATIC_DIR / "state.js").resolve().as_uri()
    script = f"""
const values = new Map();
globalThis.localStorage = {{
  getItem: (key) => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
}};
const first = await import("{module_url}?first");
const variant = {{ turn_id: "turn-1", scene_text: "原始正文" }};
first.state.activeSave = "save-a";
if (first.hasDraft(variant)) throw new Error("fresh variant must not have a draft");
first.writeDraft(variant, "玩家修改");
if (!first.hasDraft(variant) || first.readDraft(variant) !== "玩家修改") {{
  throw new Error("saved draft was not retained");
}}
first.state.activeSave = "save-b";
if (first.readDraft(variant) !== "原始正文") throw new Error("draft leaked across saves");
const reloaded = await import("{module_url}?reloaded");
reloaded.state.activeSave = "save-a";
if (!reloaded.hasDraft(variant) || reloaded.readDraft(variant) !== "玩家修改") {{
  throw new Error("draft did not survive module reload");
}}
reloaded.clearDraft(variant);
if (reloaded.hasDraft(variant) || reloaded.readDraft(variant) !== "原始正文") {{
  throw new Error("original text was not restored");
}}
reloaded.writeDraft(variant, "原始正文");
if (reloaded.hasDraft(variant)) throw new Error("original text must not create a draft");
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_dice_display_setting_is_per_save_and_normalized():
    assert save_dice_display({"dice_display": "full"}) == {
        "dice_display": "full"
    }
    assert load_settings()["dice_display"] == "full"
    assert save_dice_display({"dice_display": "invalid"}) == {
        "dice_display": "outcome"
    }


def test_reader_preferences_setting_is_per_save_and_normalized():
    assert save_reader_preferences({
        "font_size": "18",
        "font_family": "kaiti",
        "theme": "sepia",
    }) == {
        "font_size": "18",
        "font_family": "kaiti",
        "theme": "sepia",
    }
    settings = load_settings()
    assert settings["font_size"] == "18"
    assert settings["font_family"] == "kaiti"
    assert settings["theme"] == "sepia"
    assert save_reader_preferences({"font_size": "invalid", "font_family": "bad", "theme": "none"}) == {
        "font_size": "16.5",
        "font_family": "serif",
        "theme": "dark",
    }


def test_player_themes_use_semantic_tokens_without_component_overrides():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert styles.count('html[data-theme="') == 2
    assert "ULTIMATE THEME OVERRIDES" not in styles
    assert 'html[data-theme="light"] .' not in styles
    assert 'html[data-theme="sepia"] .' not in styles
    assert "select { color-scheme: dark; }" not in styles
    assert styles.count("/* Rewrite Dropdown Menu Component */") == 1
    for token in (
        "--surface-raised:",
        "--editor-bg:",
        "--positive-text:",
        "--warning-text:",
        "--negative-text:",
        "--highlight-player-text:",
        "--highlight-npc-text:",
        "--highlight-entity-text:",
        "--backdrop:",
    ):
        assert styles.count(token) == 3

    assert 'const READER_PREFERENCES_CACHE_KEY = "tvg:reader-preferences"' in source
    assert "readerPreferencesSaveQueue" in source
    assert "themeColorMeta.content = themeColor" in source
    assert "applyReaderPreferences(state.playerState || {})" in source
    assert 'localStorage.getItem("tvg:reader-preferences")' in html
    assert 'meta name="theme-color" content="#11110e"' in html


def test_player_frontend_uses_only_player_api_and_hides_debug_controls():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "/api/player/state" in source
    assert 'await request("/api/player/model-config")' in source
    assert 'request("/api/player/model-config/test"' in source
    assert 'request("/api/player/model-config"' in source
    assert 'const saves = await request("/api/player/saves")' in source
    assert 'await request("/api/player/state")' in source
    assert "Promise.all([" not in source
    assert "/api/player/history?before_turn=" in source
    assert 'id="turnNavigator"' in html
    assert 'id="turnNavigatorList"' in html
    assert 'aria-label="回合导航"' in html
    assert "function renderTurnNavigator()" in source
    assert "function jumpToHistoryTurn(turnIndex)" in source
    assert "function scheduleNavigationActiveUpdate()" in source
    assert "HISTORY_NAVIGATION_LOAD_SIZE = 20" in source
    assert 'button.setAttribute("aria-current", "step")' in source
    assert 'if ("onscrollend" in window)' in source
    assert "if (!wasSelected)" in source
    assert "return insertedCount;" in source
    assert ".turn-navigator-list::before" in styles
    assert ".turn-nav-item.active .turn-nav-dot" in styles
    assert "/api/player/generate" in source
    assert "/api/player/${endpoint}" in source
    assert 'retryTurn("rephrase")' in source
    assert 'retryTurn("reroll")' in source
    assert "/api/player/feedback-rewrite" in source
    assert 'id="feedbackRewriteDialog"' in html
    assert '"根据意见修改"' in source
    assert '"重新措辞"' in source
    assert '"重新判定"' in source
    assert "/api/player/confirm" in source
    assert "/api/player/operations/recoverable" in source
    assert "/api/player/operations/${encodeURIComponent(operationId)}/ack" in (
        STATIC_DIR / "api.js"
    ).read_text(encoding="utf-8")
    assert "/api/player/operations/${encodeURIComponent(operation.operation_id)}/events" in source
    assert "request_id: newRequestId()" in source
    assert "function resumeConfirmationOperation(" in source
    assert "acknowledgeOperation" in source
    assert 'from "./api.js"' in source
    assert "/api/player/init/story-direction" in source
    assert 'operation.recovery_kind === "initialization"' in source
    assert "for (const operation of recoverableOperations)" in source
    assert "function resumeInitializationOperation(" in source
    assert "story_direction_done" in source
    assert "/api/state" not in source
    assert "/api/traces" not in source
    assert "/api/maintenance-jobs" not in source
    assert 'href="/debug"' not in html
    assert 'id="welcome" class="welcome-card" hidden' in html
    assert "maxCharsInput" not in html
    assert "writeDbToggle" not in html
    assert "loadEarlierHistory" in source
    assert "addedHeight" in source
    assert "appendHighlightedText" in source
    assert "openCodexProfile" in source
    assert "preview.good_result" in source
    assert "renderPlayerResolution" in source
    assert 'if (mode === "outcome")' in source
    assert '"resolution-outcome resolution-outcome-line"' in source
    assert "`${item.label} ${valueText}`" in source
    assert "`${item.label} ${valueText}：${item.reason}`" not in source
    assert "`[${values.join(\", \")}] → ${keptValue}`" in source
    assert "`最终值 ${finalValue}/${sides}　${outcome}`" in source
    assert "修正明细未记录" in source
    assert "confirmDiscardUnsavedPendingEdit" in source
    assert "切换存档会丢失尚未保存的修改" in source
    assert "restoreOriginalScene" in source
    assert '"恢复原文"' in source
    assert "已恢复该版本的原始正文" in source
    assert "discard_pending" not in source
    assert "stopActiveGeneration" in source
    assert "operation_cancelled" in source
    assert "已停止生成" in source
    assert 'id="stopGenerationBtn"' in html
    assert "/cancel" in (STATIC_DIR / "api.js").read_text(encoding="utf-8")
    assert "/api/player/dice-display" in source
    assert 'id="sceneSummary"' in html
    assert 'id="codexDialog"' in html
    assert 'id="diceDisplaySelect"' in html
    assert "innerHTML" not in source
    assert "appendStreamingText" in source
    assert 'event.step === "done"' in (
        STATIC_DIR / "api.js"
    ).read_text(encoding="utf-8")
    assert "narrative_check_started" in source
    assert "canon_query_started" in source
    assert "narrative_revision_started" in source
    assert "event.data?.revision" in source
    assert 'loadAll({ scroll: "instant" })' in source
    assert ".composer-shell:focus-within" in styles
    assert ".story-highlight-npc" in styles
    assert "inventory-quantity" in source
    assert ".resource-group-inventory .resource-items" in styles
    assert "width: min(1040px" in styles
    assert "backdrop-filter" not in styles
    assert ".init-modal [hidden]" in styles
    assert "select option, select optgroup" in styles
    assert "confirmation-status" in source
    assert "activePlayerAction" in source
    assert "showActivePlayerAction(userInput)" in source
    assert 'const PLAYER_SHELL_CACHE_KEY = "tvg:player-shell"' in source
    assert "function restorePlayerShell()" in source
    assert 'sessionStorage.setItem(CONFIRMATION_IN_FLIGHT_KEY, "1")' in source
    assert "restorePlayerShell();" in source
    assert 'confirm.textContent = confirming ? "正在确认……"' in source
    assert "_handle_player_json_stream" not in (
        Path(__file__).parents[1] / "debug_server.py"
    ).read_text(encoding="utf-8")
    assert "playerState.action_preview?.previews" in source
    assert 'els.initOriginalFields.querySelectorAll("input, textarea, select")' in source
    assert 'els.initFanficFields.querySelectorAll("input, textarea, select")' in source
    for field in (
        "initFanfic",
        'name="work_name"',
        'name="birth_location"',
        'name="story_era"',
        'name="world_name"',
        'name="world_style"',
        'name="ability_system"',
        'name="custom_ability_system"',
        'name="protagonist_age"',
        'name="protagonist_background"',
        'name="narrative_tone"',
        'name="story_expectation"',
        "ability-name",
        "ability-description",
        "extraCoreMemoryList",
        "storyDirectionContent",
        "storyDirectionCurrency",
    ):
        assert field in html
    assert "story_direction" in source
    assert "initConfigSnapshot" in source
    assert 'id="manageSavesBtn"' in html
    assert 'id="modelSettingsBtn"' in html
    assert 'id="modelConfigDialog"' in html
    assert 'id="modelApiKey" type="password"' in html
    assert "modelApiKey.value = config" not in source
    assert 'els.modelApiKey.value = ""' in source
    assert 'setMode("needs_model")' in source
    assert "enterModelSetup(modelConfig)" in source
    assert 'id="saveManagerDialog"' in html
    assert "save.last_played_at" in source
    assert "save.turn_count" in source
    assert "save.protagonist_name" in source
    assert "save.location" in source
    assert 'option.value = save.save_id' in source
    assert '/api/player/saves/rename' in source
    assert '/api/player/saves/copy' in source
    assert '/api/player/saves/delete' in source
    api_source = (STATIC_DIR / "api.js").read_text(encoding="utf-8")
    assert '/api/player/saves/export?save_id=' in api_source
    assert '/api/player/saves/import?name=' in api_source
    assert '"Content-Type": "application/zip"' in api_source


@pytest.mark.parametrize(
    "operation_type",
    [
        "generation",
        "confirmation",
        "initialization_story_direction",
        "initialization_opening",
    ],
)
def test_pipeline_operation_continues_after_subscriber_disconnect(
    monkeypatch,
    operation_type,
):
    manager = OperationManager("disconnect-test", max_workers=1)
    handler = object.__new__(DebugHandler)
    handler.send_response = lambda _status: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None
    handler.wfile = _DisconnectedWriter()
    handler.path = "/api/player/generate"
    release = threading.Event()
    completed = []

    def generation_events():
        yield {"step": "state_loaded", "data": {}}
        assert release.wait(timeout=2)
        completed.append("continued")
        yield {"step": "scene_written", "data": {}}
        completed.append("finished")

    monkeypatch.setattr(
        debug_server,
        "get_campaign_runtime",
        lambda: SimpleNamespace(operations=manager),
    )
    try:
        operation_id = manager.submit_stream(operation_type, generation_events)
        handler._handle_operation_event_stream(
            operation_id,
            urlparse(handler.path),
            player=True,
            resumed=False,
        )
        release.set()

        assert manager.wait(operation_id, timeout=2).status == "succeeded"
        assert completed == ["continued", "finished"]
    finally:
        release.set()
        manager.shutdown()


def test_cancelled_player_operation_ends_sse_without_failure_payload(monkeypatch):
    manager = OperationManager("cancel-stream-test", max_workers=1)
    handler = object.__new__(DebugHandler)
    handler.send_response = lambda _status: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None
    handler.wfile = io.BytesIO()
    handler.path = "/api/player/generate"
    waiting = threading.Event()
    release = threading.Event()

    def generation_events():
        yield {"step": "state_loaded", "data": {}}
        waiting.set()
        assert release.wait(timeout=2)
        yield {"step": "must_not_be_emitted", "data": {}}

    monkeypatch.setattr(
        debug_server,
        "get_campaign_runtime",
        lambda: SimpleNamespace(operations=manager),
    )
    try:
        operation_id = manager.submit_stream("generation", generation_events)
        assert waiting.wait(timeout=2)
        subscriber = threading.Thread(
            target=handler._handle_operation_event_stream,
            args=(operation_id, urlparse(handler.path)),
            kwargs={"player": True, "resumed": False},
        )
        subscriber.start()
        manager.cancel(operation_id)
        release.set()
        subscriber.join(timeout=2)

        payload = handler.wfile.getvalue().decode("utf-8")
        assert '"step": "operation_cancelled"' in payload
        assert '"step": "done"' in payload
        assert '"ok": false' not in payload
    finally:
        release.set()
        manager.shutdown()


def test_player_state_is_allow_listed_and_restores_pending_content():
    runtime = get_campaign_runtime()
    runtime.stores.core_memory().save(CoreMemory(items=[
        CoreMemoryItem(id="secret", category="director", content="不可泄露")
    ]))
    with runtime.stores.current_state() as store:
        store.save(CurrentState(
            scene=SceneState(
                scene_id="scene_7",
                location="老橡树镇",
                participants=[
                    "莱尔",
                    {
                        "name": "埃利奥",
                        "npc_conditions": [TimedCondition(
                            condition="暗中恐惧",
                            expires_after_phases=-1,
                        )],
                        "attitude_to_player": "表面友好，实际警惕",
                    },
                ],
                scene_condition=[TimedCondition(
                    condition="房间正在燃烧",
                    expires_after_phases=2,
                )],
            ),
            world_clock=WorldClock(day=4, phase="晚上"),
            player=PlayerState(name="莱尔", condition=["疲惫"]),
            resources=ResourcesState(money=17, money_unit="金币"),
        ))
    with runtime.stores.short_term() as store:
        store.add_turn(
            "scene_7",
            6,
            "询问木偶的事。",
            "莱尔在图书馆找到了木偶师埃利奥。",
            "继续追问吗？",
            action_preview=_action_preview(),
            player_resolution=_player_resolution(),
        )
    with runtime.stores.entity() as store:
        store.upsert(EntityProfile(
            entity_type="location",
            name="灰烬图书馆",
            aliases=["图书馆"],
            summary="藏有大量旧书的公开地点。",
        ))
    with runtime.stores.npc_profile() as store:
        store.upsert(NPCProfile(
            name="埃利奥",
            aliases=["木偶师"],
            role="流浪木偶师",
            public_summary="一名公开身份不明的木偶师。",
            hidden_secret="不应投影的 NPC 秘密",
        ))
    runtime.pending_turns.add(SimpleNamespace(
        turn_id="turn-v1",
        group_id="group-1",
        version_index=1,
        turn_index=7,
        user_input="继续追问。",
        difficulty="普通",
        scene_result=SimpleNamespace(
            scene_text="埃利奥终于开口。",
            closing_prompt="你准备如何回应？",
        ),
    ))

    payload = load_player_state()

    assert set(payload) == {
        "save", "needs_init", "difficulty", "campaign", "history",
        "history_navigation", "history_pagination", "highlights", "pending",
        "history_controls", "action_preview", "dice_display",
        "font_size", "font_family", "theme",
    }
    assert payload["campaign"]["participants"] == ["莱尔", "埃利奥"]
    assert payload["campaign"]["scene_conditions"] == ["房间正在燃烧"]
    assert "attitude_to_player" not in str(payload)
    assert "暗中恐惧" not in str(payload)
    assert "不可泄露" not in str(payload)
    assert "藏有大量旧书的公开地点。" in str(payload)
    assert "流浪木偶师" in str(payload)
    assert "不应投影的 NPC 秘密" not in str(payload)
    assert {item["surface"] for item in payload["highlights"]} == {
        "莱尔", "图书馆", "木偶师", "埃利奥",
    }
    assert {item["kind"] for item in payload["highlights"]} == {
        "player", "npc", "entity",
    }
    assert payload["pending"][0]["variants"][0]["closing_prompt"] == "你准备如何回应？"
    assert payload["action_preview"] == _action_preview().model_dump(mode="json")
    assert payload["history"][0]["player_resolution"] == _player_resolution().model_dump(mode="json")
    assert payload["history_navigation"] == [{
        "turn_index": 6,
        "user_input_excerpt": "询问木偶的事。",
    }]
    assert payload["dice_display"] == "outcome"


def test_player_history_loads_five_turn_pages_oldest_to_newest():
    runtime = get_campaign_runtime()
    with runtime.stores.short_term() as store:
        for turn_index in range(1, 13):
            store.add_turn(
                f"scene-{turn_index}",
                turn_index,
                f"行动 {turn_index}",
                f"正文 {turn_index}",
            )

    initial = load_player_state()
    assert [turn["turn_index"] for turn in initial["history"]] == [8, 9, 10, 11, 12]
    assert [item["turn_index"] for item in initial["history_navigation"]] == list(range(1, 13))
    assert initial["history_pagination"]["has_more"] is True

    previous = load_player_history(before_turn_index=8, limit=5)
    assert [turn["turn_index"] for turn in previous["history"]] == [3, 4, 5, 6, 7]
    assert previous["pagination"]["has_more"] is True

    oldest = load_player_history(before_turn_index=3, limit=5)
    assert [turn["turn_index"] for turn in oldest["history"]] == [1, 2]
    assert oldest["pagination"]["has_more"] is False


def test_player_history_navigation_normalizes_and_truncates_user_input():
    runtime = get_campaign_runtime()
    with runtime.stores.short_term() as store:
        store.add_turn("opening", 0, "", "开场正文")
        store.add_turn(
            "scene-1",
            1,
            "  这是一个包含换行\n和   多余空白、并且长度明显超过导航摘要上限的玩家行动输入  ",
            "第一回合正文",
        )

    navigation = load_player_state()["history_navigation"]

    assert navigation == [
        {"turn_index": 0, "user_input_excerpt": ""},
        {
            "turn_index": 1,
            "user_input_excerpt": "这是一个包含换行 和 多余空白、并且长度明显超过导航摘要上限的…",
        },
    ]
    assert len(navigation[1]["user_input_excerpt"]) == 32


def test_player_history_navigation_preserves_sparse_turn_indexes():
    runtime = get_campaign_runtime()
    with runtime.stores.short_term() as store:
        store.add_turn("scene-2", 2, "第二回合行动", "第二回合正文")
        store.add_turn("scene-7", 7, "第七回合行动", "第七回合正文")

    assert load_player_state()["history_navigation"] == [
        {"turn_index": 2, "user_input_excerpt": "第二回合行动"},
        {"turn_index": 7, "user_input_excerpt": "第七回合行动"},
    ]


def test_player_history_navigation_is_empty_without_confirmed_turns():
    assert load_player_state()["history_navigation"] == []


def test_player_event_projection_removes_internal_generation_data():
    event = DebugHandler._player_event({
        "step": "scene_written",
        "data": {
            "turn_id": "turn-1",
            "group_id": "group-1",
            "version_index": 2,
            "turn_index": 7,
            "trace_id": "private-trace",
            "context": {"director_event_consumptions": ["secret"]},
            "scene_result": {
                "scene_text": "公开正文",
                "closing_prompt": "接下来怎么办？",
                "next_action_directions": ["private"],
            },
        },
    })

    assert event == {
        "step": "scene_written",
        "data": {
            "turn_id": "turn-1",
            "group_id": "group-1",
            "version_index": 2,
            "turn_index": 7,
            "scene_text": "公开正文",
            "closing_prompt": "接下来怎么办？",
        },
    }
    assert "private" not in str(event)


def test_player_event_projection_preserves_revision_stream_marker():
    event = DebugHandler._player_event({
        "step": "scene_stream_started",
        "data": {
            "attempt": 2,
            "revision": True,
            "trace_id": "private-trace",
        },
    })

    assert event == {
        "step": "scene_stream_started",
        "data": {
            "attempt": 2,
            "version_index": None,
            "revision": True,
        },
    }


def test_player_story_direction_event_exposes_only_editable_result():
    event = DebugHandler._player_event({
        "step": "story_direction_done",
        "data": {
            "config": {"protagonist_name": "莱尔"},
            "story_direction": {"content": "向北探索", "currency_unit": "金币"},
            "core_memory": {"items": [{"content": "internal"}]},
            "trace_id": "private-trace",
        },
    })

    assert event == {
        "step": "story_direction_done",
        "data": {
            "config": {"protagonist_name": "莱尔"},
            "story_direction": {"content": "向北探索", "currency_unit": "金币"},
        },
    }


def test_player_operation_projection_is_allow_listed():
    operation = DebugHandler._player_operation({
        "operation_id": "operation-1",
        "operation_type": "generation",
        "status": "running",
        "current_step": "action_parsed",
        "submitted_at": "now",
        "started_at": "now",
        "result": {"private": "result"},
        "error_message": "private error",
        "metadata": {
            "user_input": "调查钟楼",
            "turn_id": "turn-1",
            "trace_id": "private-trace",
        },
    })

    assert operation == {
        "operation_id": "operation-1",
        "operation_type": "generation",
        "status": "running",
        "current_step": "action_parsed",
        "submitted_at": "now",
        "started_at": "now",
        "recovery_kind": "generation",
        "metadata": {
            "user_input": "调查钟楼",
            "turn_id": "turn-1",
        },
    }
    assert "private" not in str(operation)


def test_completed_player_operation_is_returned_until_acknowledged(monkeypatch):
    captured = {}

    def list_recoverable(**kwargs):
        captured.update(kwargs)
        return [
            {
                "operation_id": "operation-newer",
                "operation_type": "initialization_opening",
                "status": "running",
                "current_step": "initialization_started",
                "submitted_at": "later",
                "started_at": "later",
                "metadata": {"stage": "opening"},
            },
            {
                "operation_id": "operation-finished",
                "operation_type": "initialization_story_direction",
                "status": "succeeded",
                "current_step": "story_direction_done",
                "submitted_at": "earlier",
                "started_at": "earlier",
                "metadata": {"stage": "story_direction"},
            },
        ]

    monkeypatch.setattr(debug_server, "list_operations", list_recoverable)
    handler = object.__new__(DebugHandler)

    result = handler._load_recoverable_player_operations()

    assert captured == {"recoverable_only": True, "limit": None}
    assert [operation["operation_id"] for operation in result["operations"]] == [
        "operation-finished",
        "operation-newer",
    ]
    assert result["operations"][0]["recovery_kind"] == "initialization"


def test_operation_ack_path_parser_rejects_ambiguous_paths():
    assert DebugHandler._operation_id_from_action_path(
        "/api/player/operations/operation-1/ack",
        player=True,
        action="ack",
    ) == "operation-1"
    assert DebugHandler._operation_id_from_action_path(
        "/api/player/operations/operation-1/cancel",
        player=True,
        action="cancel",
    ) == "operation-1"
    with pytest.raises(ValueError, match="Invalid"):
        DebugHandler._operation_id_from_action_path(
            "/api/player/operations/operation-1/extra/ack",
            player=True,
            action="ack",
        )


def test_player_ack_removes_completed_operation_from_recovery():
    runtime = get_campaign_runtime()

    def stream():
        yield {"step": "story_direction_done", "data": {}}

    operation_id = runtime.operations.submit_stream(
        "initialization_story_direction",
        stream,
        requires_ack=True,
    )
    runtime.operations.wait(operation_id, timeout=2)
    handler = object.__new__(DebugHandler)

    result = handler._acknowledge_player_operation(
        f"/api/player/operations/{operation_id}/ack"
    )

    assert result == {"operation_id": operation_id, "acknowledged": True}
    assert operation_id not in {
        operation.operation_id
        for operation in runtime.operations.list(recoverable_only=True)
    }


def test_player_save_payload_removes_server_root():
    payload = player_saves_payload({
        "active": "8e4e1da7-00a0-4a63-83c7-d6726694077a",
        "root": "/private/server/path",
        "saves": [{
            "save_id": "8e4e1da7-00a0-4a63-83c7-d6726694077a",
            "name": "莱尔",
            "active": True,
            "initialized": True,
            "last_played_at": "now",
            "turn_count": 3,
            "protagonist_name": "莱尔",
            "location": "北门",
            "path": "/private/server/path/莱尔",
        }],
    })

    assert "root" not in payload
    assert "path" not in payload["saves"][0]
