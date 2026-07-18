import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.debug_server import DebugHandler
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
from app.runtime.player_state import (
    load_player_history,
    load_player_state,
    player_saves_payload,
)
from app.runtime.session import get_campaign_runtime


STATIC_DIR = Path(__file__).parents[1] / "player_static"


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


def test_player_frontend_uses_only_player_api_and_hides_debug_controls():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "/api/player/state" in source
    assert "/api/player/history?before_turn=" in source
    assert "/api/player/generate" in source
    assert "/api/player/confirm" in source
    assert "/api/state" not in source
    assert "/api/traces" not in source
    assert "/api/maintenance-jobs" not in source
    assert 'href="/debug"' not in html
    assert "maxCharsInput" not in html
    assert "writeDbToggle" not in html
    assert "loadEarlierHistory" in source
    assert "addedHeight" in source
    assert "appendHighlightedText" in source
    assert "innerHTML" not in source
    assert "appendStreamingText" in source
    assert 'event.step === "done"' in (
        STATIC_DIR / "api.js"
    ).read_text(encoding="utf-8")
    assert "narrative_check_started" in source
    assert 'loadAll({ scroll: "instant" })' in source
    assert ".composer-shell:focus-within" in styles
    assert ".story-highlight-npc" in styles
    assert "inventory-quantity" in source
    assert ".resource-group-inventory .resource-items" in styles
    assert "width: min(1040px" in styles


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
        )
    with runtime.stores.entity() as store:
        store.upsert(EntityProfile(
            entity_type="location",
            name="灰烬图书馆",
            aliases=["图书馆"],
            summary="不应投影的实体摘要",
        ))
    with runtime.stores.npc_profile() as store:
        store.upsert(NPCProfile(
            name="埃利奥",
            aliases=["木偶师"],
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
        "history_pagination", "highlights", "pending", "history_controls",
    }
    assert payload["campaign"]["participants"] == ["莱尔", "埃利奥"]
    assert payload["campaign"]["scene_conditions"] == ["房间正在燃烧"]
    assert "attitude_to_player" not in str(payload)
    assert "暗中恐惧" not in str(payload)
    assert "不可泄露" not in str(payload)
    assert "不应投影的实体摘要" not in str(payload)
    assert "不应投影的 NPC 秘密" not in str(payload)
    assert {item["surface"] for item in payload["highlights"]} == {
        "莱尔", "图书馆", "木偶师", "埃利奥",
    }
    assert {item["kind"] for item in payload["highlights"]} == {
        "player", "npc", "entity",
    }
    assert payload["pending"][0]["variants"][0]["closing_prompt"] == "你准备如何回应？"


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
    assert initial["history_pagination"]["has_more"] is True

    previous = load_player_history(before_turn_index=8, limit=5)
    assert [turn["turn_index"] for turn in previous["history"]] == [3, 4, 5, 6, 7]
    assert previous["pagination"]["has_more"] is True

    oldest = load_player_history(before_turn_index=3, limit=5)
    assert [turn["turn_index"] for turn in oldest["history"]] == [1, 2]
    assert oldest["pagination"]["has_more"] is False


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


def test_player_save_payload_removes_server_root():
    payload = player_saves_payload({
        "active": "莱尔",
        "root": "/private/server/path",
        "saves": [{
            "name": "莱尔",
            "active": True,
            "initialized": True,
            "modified_at": "now",
            "path": "/private/server/path/莱尔",
        }],
    })

    assert "root" not in payload
    assert "path" not in payload["saves"][0]
