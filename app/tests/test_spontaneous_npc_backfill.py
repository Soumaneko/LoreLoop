import sqlite3
from types import SimpleNamespace
from typing import Any

from app.domain.core_memory.schemas import CoreMemory
from app.domain.creative_brief import draw_desire_pattern
from app.domain.director.schemas import DirectorStageMaterial, DirectorStageResult
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.npc_profile import workflows as npc_workflows
from app.domain.npc_profile.director_promotion import npc_profile_from_spontaneous_material
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.workflows import auto_backfill_spontaneous_npc_profiles
from app.domain.scene_facts.schemas import SceneFact, SceneFactSet, SceneNpcMention


class _MockGateway:
    def __init__(self, response: dict[str, Any]):
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def run(self, *, task: str, input_data: dict[str, Any], output_schema: Any, trace_id: str | None = None, **kwargs):
        self.calls.append({
            "task": task,
            "input_data": input_data,
            "output_schema": output_schema,
            "trace_id": trace_id,
        })
        on_start = kwargs.get("on_attempt_start")
        validator = kwargs.get("output_validator")
        if on_start is not None:
            on_start(1)
        if task == "npc_batch_update":
            import json as _json
            chars = _json.loads(input_data.get("npc_batch_json") or "[]")
            updates = []
            for char in chars:
                ev_ids = []
                for ev in char.get("evidence") or []:
                    if isinstance(ev, dict) and "document_id" in ev:
                        ev_ids.append(ev["document_id"])
                if not ev_ids:
                    ev_ids = ["turn_1_p001"]
                updates.append({
                    "npc_id": char["npc_id"],
                    "scene_summary": {
                        "summary": "在百草堂为萧欢抓药并告知坊市近况，持续二十字以上有效摘要。",
                        "evidence_ids": ev_ids,
                    },
                })
            result = output_schema.model_validate({"updates": updates})
        else:
            result = output_schema.model_validate(self._response)
        if validator is not None:
            validator(result)
        return result


def _make_stores():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    npc_store = NPCProfileStore(conn)
    director_store = DirectorNPCStore(conn)
    return conn, npc_store, director_store


def test_draw_desire_pattern_returns_valid_string():
    pattern = draw_desire_pattern()
    assert isinstance(pattern, str)
    assert len(pattern) > 0


def test_confirmed_npc_turn_runs_business_steps_and_merges_ids(monkeypatch):
    process_result = SimpleNamespace(
        saved_ids=["npc_batch", "npc_shared"],
        new_npc_candidates=[],
        warnings=[],
    )

    def _backfill(**kwargs):
        return ["npc_backfill", "npc_shared"]

    def _batch(**kwargs):
        return process_result

    def _prune(**kwargs):
        return ["npc_pruned", "npc_batch"]

    monkeypatch.setattr(
        npc_workflows,
        "auto_backfill_spontaneous_npc_profiles",
        _backfill,
    )
    monkeypatch.setattr(npc_workflows, "apply_npc_batch_update", _batch)
    monkeypatch.setattr(
        npc_workflows,
        "prune_npc_transient_aliases_from_current_state",
        _prune,
    )

    result = npc_workflows.apply_confirmed_npc_turn(
        pending=object(),
        npc_store=object(),
        gateway=object(),
        core_memory=CoreMemory(items=[]),
        scene_facts=SceneFactSet(scene_focus="本轮场景。"),
        current_state=object(),
        user_input="前进。",
        scene_text="主角走进客栈。",
        turn_index=2,
        scene_id="scene_002",
        trace_id="trace-2",
    )

    assert result.process_result == process_result
    assert result.updated_npc_profile_ids == [
        "npc_backfill",
        "npc_shared",
        "npc_batch",
        "npc_pruned",
    ]


def test_auto_backfill_skips_when_npc_never_appeared_in_scene_facts():
    conn, npc_store, _ = _make_stores()

    material = DirectorStageMaterial(
        kind="npc",
        resource_id="spontaneous_npc_sun_01",
        guidance="在柜台后称药。",
        payload={
            "source": "spontaneous",
            "name": "孙百草",
            "role": "百草堂掌柜",
            "view_mode": "face_to_face",
        },
    )

    class MockPending:
        def __init__(self):
            self.director_stage = DirectorStageResult(
                reasoning="即兴引入孙百草",
                materials=[material],
            )

    gateway = _MockGateway({
        "core_motivation": "动机",
        "hidden_secret": "秘密",
        "conflict_style": "冲突",
        "social_style": "社交",
    })

    # scene_facts 中完全没有孙百草（只有主角）
    scene_facts = SceneFactSet(
        scene_focus="萧欢赶路",
        facts=[SceneFact(fact_id="f1", fact="萧欢在山道上行走。")],
        npc_mentions=[],
    )

    backfilled_ids = auto_backfill_spontaneous_npc_profiles(
        pending=MockPending(),
        npc_store=npc_store,
        gateway=gateway,
        core_memory=CoreMemory(items=[]),
        turn_index=1,
        scene_facts=scene_facts,
    )

    assert backfilled_ids == [], "正文中完全未出现时必须跳过 Backfill，避免幽灵 NPC"
    assert len(gateway.calls) == 0
    assert len(npc_store.list_all()) == 0


def test_auto_backfill_runs_when_npc_mentioned_only_in_scene_facts():
    conn, npc_store, _ = _make_stores()

    material = DirectorStageMaterial(
        kind="npc",
        resource_id="spontaneous_npc_sun_01",
        guidance="在柜台后称药。",
        payload={
            "source": "spontaneous",
            "name": "孙百草",
            "role": "百草堂掌柜",
            "view_mode": "face_to_face",
        },
    )

    class MockPending:
        def __init__(self):
            self.director_stage = DirectorStageResult(
                reasoning="即兴引入孙百草",
                materials=[material],
            )

    gateway = _MockGateway({
        "core_motivation": "收集天下灵药",
        "hidden_secret": "私藏残方",
        "conflict_style": "退让",
        "social_style": "谦和",
    })

    # scene_facts 中存在孙百草，但为 mentioned_only
    scene_facts = SceneFactSet(
        scene_focus="谈论孙百草",
        facts=[SceneFact(fact_id="f1", fact="店小二提到城西百草堂的孙百草医术高超。")],
        npc_mentions=[
            SceneNpcMention(
                primary_surface="孙百草",
                presence="mentioned_only",
                evidence_fact_ids=["f1"],
            )
        ],
    )

    backfilled_ids = auto_backfill_spontaneous_npc_profiles(
        pending=MockPending(),
        npc_store=npc_store,
        gateway=gateway,
        core_memory=CoreMemory(items=[]),
        turn_index=1,
        scene_facts=scene_facts,
    )

    assert len(backfilled_ids) == 1
    assert npc_store.get_by_name("孙百草") is not None


def test_npc_profile_from_spontaneous_material():
    material = DirectorStageMaterial(
        kind="npc",
        resource_id="spontaneous_npc_sun_01",
        guidance="在柜台后称药，和善待客。",
        payload={
            "source": "spontaneous",
            "name": "孙百草",
            "role": "百草堂药铺掌柜",
            "appearance": "清瘦老者，穿青衫",
            "personality_trait": "严谨和善",
            "known_abilities": [{"name": "辨识百草", "description": "能识别各类凡草灵药。"}],
            "faction": "百草堂",
            "view_mode": "face_to_face",
        },
    )

    profile = npc_profile_from_spontaneous_material(
        material,
        turn_index=2,
        scene_id="scene_001",
    )

    assert profile.name == "孙百草"
    assert profile.role == "百草堂药铺掌柜"
    assert profile.appearance == "清瘦老者，穿青衫"
    assert profile.personality_trait == "严谨和善"
    assert len(profile.known_abilities) == 1
    assert profile.known_abilities[0].name == "辨识百草"
    assert profile.faction == "百草堂"
    assert profile.disclosure_level == "face_to_face"
    assert profile.source_director_npc_id is None
    assert profile.created_turn == 2
    assert profile.introduced_turn == 2
    assert profile.source_scene_ids == ["scene_001"]


def test_auto_backfill_spontaneous_npc_profiles_workflow():
    conn, npc_store, director_store = _make_stores()

    material = DirectorStageMaterial(
        kind="npc",
        resource_id="spontaneous_npc_sun_01",
        guidance="在柜台后称药，和善待客。",
        payload={
            "source": "spontaneous",
            "name": "孙百草",
            "role": "百草堂药铺掌柜",
            "appearance": "清瘦老者，穿青衫",
            "personality_trait": "严谨和善",
            "known_abilities": [{"name": "辨识百草", "description": "能识别各类凡草灵药。"}],
            "faction": "百草堂",
            "view_mode": "face_to_face",
        },
    )

    class MockPending:
        def __init__(self):
            self.director_stage = DirectorStageResult(
                reasoning="即兴引入孙百草",
                materials=[material],
            )

    backfill_response = {
        "core_motivation": "收集天下灵草治愈胞妹隐疾",
        "hidden_secret": "私藏了一张上古洗髓残方",
        "conflict_style": "以理服人，极力避免武力冲突",
        "social_style": "谦和有礼但言谈审慎",
    }
    gateway = _MockGateway(backfill_response)

    backfilled_ids = auto_backfill_spontaneous_npc_profiles(
        pending=MockPending(),
        npc_store=npc_store,
        gateway=gateway,
        core_memory=CoreMemory(items=[]),
        turn_index=1,
        scene_id="scene_001",
        trace_id="test_trace",
    )

    assert len(backfilled_ids) == 1
    saved_id = backfilled_ids[0]

    # 1. 验证 NPCProfileStore 中已成功入库完整档案
    profile = npc_store.get_by_id(saved_id)
    assert profile is not None
    assert profile.name == "孙百草"
    assert profile.core_motivation == "收集天下灵草治愈胞妹隐疾"
    assert profile.hidden_secret == "私藏了一张上古洗髓残方"
    assert profile.conflict_style == "以理服人，极力避免武力冲突"
    assert profile.social_style == "谦和有礼但言谈审慎"
    assert profile.personality_trait == "严谨和善"
    assert profile.appearance == "清瘦老者，穿青衫"
    assert profile.disclosure_level == "face_to_face"

    # 2. 验证 DirectorNPCStore 没有任何草稿（不写 Draft）
    drafts = director_store.list_all()
    assert len(drafts) == 0

    # 3. 验证再次调用时防重入
    repeated_ids = auto_backfill_spontaneous_npc_profiles(
        pending=MockPending(),
        npc_store=npc_store,
        gateway=gateway,
        core_memory=CoreMemory(items=[]),
        turn_index=1,
        scene_id="scene_001",
        trace_id="test_trace_2",
    )
    assert repeated_ids == [saved_id]
    # 没有重复调用 gateway
    assert len(gateway.calls) == 1


def test_npc_profile_update_node_auto_backfills_spontaneous_npc(monkeypatch):
    from types import SimpleNamespace
    from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
    from app.domain.scene_facts import SceneFactSet
    from app.domain.world_setting.store import WorldSettingStore
    from app.graph import confirmation_nodes

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    npc_store = NPCProfileStore(conn)
    setting_store = WorldSettingStore(conn)
    director_store = DirectorNPCStore(conn)

    material = DirectorStageMaterial(
        kind="npc",
        resource_id="spontaneous_npc_sun_01",
        guidance="在柜台后为主角抓药。",
        payload={
            "source": "spontaneous",
            "name": "孙百草",
            "role": "药铺掌柜",
            "appearance": "清瘦老者",
            "personality_trait": "和善",
            "view_mode": "face_to_face",
        },
    )

    pending = SimpleNamespace(
        director_stage=DirectorStageResult(
            reasoning="即兴引入孙百草",
            materials=[material],
        ),
    )

    backfill_response = {
        "core_motivation": "收集天下灵药",
        "hidden_secret": "私藏残方",
        "conflict_style": "退让",
        "social_style": "谦和",
    }
    gateway = _MockGateway(backfill_response)

    monkeypatch.setattr(confirmation_nodes, "_gateway_for", lambda _state: gateway)

    state = {
        "pipeline_input": SimpleNamespace(turn_index=1, user_input="", pending=pending),
        "pending": pending,
        "trace_id": "trace-1",
        "cache": {"key": "正文"},
        "write_db": True,
        "scene_facts": SceneFactSet(
            scene_focus="萧欢抓药",
            facts=[SceneFact(fact_id="f1", fact="孙百草在百草堂柜台后抓药。")],
            npc_mentions=[
                SceneNpcMention(
                    primary_surface="孙百草",
                    presence="appeared",
                    evidence_fact_ids=["f1"],
                )
            ],
        ),
        "updated_state": CurrentState(
            scene=SceneState(
                scene_id="scene_001",
                location="百草堂",
                participants=["萧欢"],
            ),
            player=PlayerState(name="萧欢"),
        ),
        "effective_scene_text": "萧欢在百草堂抓药。",
        "core_memory": CoreMemory(items=[]),
        "existing_npc_profiles": [],
        "runtime_services": SimpleNamespace(
            gateway=lambda **kw: gateway,
            stores=SimpleNamespace(
                npc_profile=npc_store,
                world_setting=setting_store,
                director_npc=director_store,
            ),
        ),
    }

    result = confirmation_nodes.npc_profile_update_node(state)

    # 1. 验证即兴角色被自动 backfill 并记录入 updated_npc_profile_ids
    saved_profiles = npc_store.list_all()
    assert len(saved_profiles) == 1
    sun_profile = saved_profiles[0]
    assert sun_profile.name == "孙百草"
    assert sun_profile.core_motivation == "收集天下灵药"
    assert sun_profile.npc_id in result["updated_npc_profile_ids"]

    # 2. 验证草稿库零污染
    assert len(director_store.list_all()) == 0
