"""End-to-End Multi-Round Integration Test for Spontaneous NPC Lifecycle.

Tests the full cross-round lifecycle:
1. Round 1 Generation: Director introduces a spontaneous NPC ('孙百草') via introduce_npc(spontaneous=...).
2. Round 1 Scene Facts: Scene Fact Extractor grounds alias in prose ('老掌柜') to true name ('孙百草') via staged NPC prior.
3. Round 1 Confirmation: Auto-backfill generates deep profile fields (core_motivation, hidden_secret, etc.),
   persists directly to NPCProfileStore, and batch updater seamlessly resolves him without duplicate candidate creation.
4. Round 2 Consumption: The character is loaded into resources.npcs, available for scheduling, state updates,
   and alias recognition in subsequent turns.
"""

from types import SimpleNamespace
import sqlite3
from typing import Any

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.director.generation import _introduce_npc
from app.domain.director.schemas import DirectorStageMaterial, DirectorStageResult
from app.domain.director.skill_specs import (
    IntroduceNPCArguments,
    KnownAbilitySpec,
    SpontaneousNPCSpec,
)
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.npc_profile.schemas import KnownAbility, NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.scene_facts.schemas import (
    AliasRelation,
    SceneFact,
    SceneFactSet,
    SceneNpcMention,
)
from app.domain.world_setting.store import WorldSettingStore
from app.graph import confirmation_nodes


class _LifecycleMockGateway:
    def __init__(self):
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
        if task == "npc_profile_backfill":
            result = output_schema.model_validate({
                "core_motivation": "经营百草堂并暗中追寻救治胞妹的古方",
                "hidden_secret": "私藏有一张上古残缺丹方",
                "conflict_style": "以理服人，极力避免暴力",
                "social_style": "和善谦逊，言谈滴水不漏",
            })
        elif task == "npc_batch_update":
            import json
            chars = json.loads(input_data.get("npc_batch_json") or "[]")
            updates = [
                {
                    "npc_id": char["npc_id"],
                    "scene_summary": {
                        "summary": (
                            "在百草堂为萧欢抓药并告知坊市近况，"
                            "双方完成了一次正面交谈。"
                        ),
                        "evidence_ids": [
                            evidence["document_id"]
                            for evidence in char.get("evidence") or []
                            if isinstance(evidence, dict)
                            and evidence.get("document_id")
                        ],
                    },
                }
                for char in chars
            ]
            result = output_schema.model_validate({"updates": updates})
        else:
            result = output_schema.model_validate({})
        if validator is not None:
            validator(result)
        return result


def test_spontaneous_npc_full_multiround_lifecycle(monkeypatch):
    # -----------------------------------------------------------------------
    # Setup Stores
    # -----------------------------------------------------------------------
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    npc_store = NPCProfileStore(conn)
    director_store = DirectorNPCStore(conn)
    setting_store = WorldSettingStore(conn)

    gateway = _LifecycleMockGateway()

    stores_map = {
        "npc_profile": npc_store,
        "director_npc": director_store,
        "world_setting": setting_store,
    }

    def _mock_store_factory(_state, name):
        return stores_map[name]

    monkeypatch.setattr(confirmation_nodes, "_store_factory", _mock_store_factory)
    monkeypatch.setattr(confirmation_nodes, "_gateway_for", lambda _state: gateway)

    core_memory = CoreMemory(items=[
        CoreMemoryItem(
            id="world_1",
            kind="world_invariant",
            strength="hard_constraint",
            source="campaign_setup",
            content="古典仙侠世界，青云坊市为散修与凡人聚集之地。",
        ),
        CoreMemoryItem(
            id="player_1",
            kind="protagonist_invariant",
            strength="hard_constraint",
            source="player",
            content="萧欢，合欢宗弟子，隐藏身份暂居青云坊市。",
        ),
    ])

    # =======================================================================
    # Round 1 - Phase 1: Generation Stage (即兴引入孙百草)
    # =======================================================================
    intro_args = IntroduceNPCArguments(
        spontaneous=SpontaneousNPCSpec(
            name="孙百草",
            role="百草堂药铺掌柜",
            appearance="清瘦老者，穿青衫",
            personality_trait="严谨和善",
            known_abilities=[KnownAbilitySpec(name="辨识百草", description="能识别各类凡草灵药。")],
        ),
        view_mode="face_to_face",
    )
    material = _introduce_npc(
        director_npc_store=director_store,
        args=intro_args,
        guidance="在柜台后称药，为主角抓药并交代坊市近况。",
    )

    assert material.kind == "npc"
    assert material.payload["name"] == "孙百草"
    assert material.payload["source"] == "spontaneous"
    assert material.payload["view_mode"] == "face_to_face"

    pending = SimpleNamespace(
        director_stage=DirectorStageResult(
            reasoning="引入药铺掌柜孙百草",
            materials=[material],
        )
    )

    # =======================================================================
    # Round 1 - Phase 2: Scene Facts (正文称呼“老掌柜”，先验锚定真名“孙百草”)
    # =======================================================================
    round1_scene_facts = SceneFactSet(
        scene_focus="萧欢在百草堂抓药",
        facts=[
            SceneFact(fact_id="f1", fact="萧欢踏入百草堂买药。"),
            SceneFact(fact_id="f2", fact="老掌柜在柜台后称药并与萧欢交谈。"),
        ],
        npc_mentions=[
            SceneNpcMention(
                primary_surface="孙百草",
                presence="appeared",
                alias_relations=[
                    AliasRelation(
                        surface="老掌柜",
                        basis="temporary_address",
                        evidence_fact_ids=["f2"],
                    )
                ],
                evidence_fact_ids=["f2"],
            )
        ],
    )

    # =======================================================================
    # Round 1 - Phase 3: Confirmation Graph (Auto-backfill & Batch Update)
    # =======================================================================
    state_r1 = {
        "pipeline_input": SimpleNamespace(turn_index=1, user_input="去药铺买药", pending=pending),
        "pending": pending,
        "trace_id": "trace-r1",
        "cache": {"key": "r1"},
        "write_db": True,
        "scene_facts": round1_scene_facts,
        "updated_state": CurrentState(
            scene=SceneState(
                scene_id="scene_r1",
                location="百草堂",
                participants=["萧欢", "孙百草"],
            ),
            player=PlayerState(name="萧欢"),
        ),
        "effective_scene_text": "萧欢踏入百草堂。老掌柜在柜台后慢条斯理地称药，抬头微笑示意。",
        "core_memory": core_memory,
        "existing_npc_profiles": [],
        "runtime_services": SimpleNamespace(gateway=lambda **kw: gateway),
    }

    result_r1 = confirmation_nodes.npc_profile_update_node(state_r1)

    # 1. 验证孙百草成功创建为完整正式 NPCProfile
    profiles = npc_store.list_all()
    assert len(profiles) == 1
    sun_profile = profiles[0]
    assert sun_profile.name == "孙百草"
    assert sun_profile.role == "百草堂药铺掌柜"
    assert sun_profile.appearance == "清瘦老者，穿青衫"
    assert sun_profile.personality_trait == "严谨和善"
    assert sun_profile.core_motivation == "经营百草堂并暗中追寻救治胞妹的古方"
    assert sun_profile.hidden_secret == "私藏有一张上古残缺丹方"
    assert sun_profile.conflict_style == "以理服人，极力避免暴力"
    assert sun_profile.social_style == "和善谦逊，言谈滴水不漏"
    assert sun_profile.disclosure_level == "face_to_face"
    assert sun_profile.introduced_turn == 1

    # 2. 验证角色被批次事实更新器正确命中为 resolved 角色并增加命中计数
    assert sun_profile.name_hit_count == 1

    # 3. 验证本轮未产生未解析的新 NPC 候选
    assert result_r1["events"][0]["data"]["new_npc_candidates"] == []

    # 4. 验证草稿库零污染
    assert len(director_store.list_all()) == 0

    # =======================================================================
    # Round 2: Consumption in subsequent turn
    # =======================================================================
    # 下一轮从数据库读取既有 NPC 资源
    r2_existing_npcs = npc_store.list_all()
    assert len(r2_existing_npcs) == 1
    assert r2_existing_npcs[0].name == "孙百草"

    # 模拟 Round 2 再次提到“老掌柜”
    round2_scene_facts = SceneFactSet(
        scene_focus="萧欢再次回访百草堂",
        facts=[SceneFact(fact_id="f2_1", fact="萧欢再次来到百草堂，向孙百草打听坊市消息。")],
        npc_mentions=[
            SceneNpcMention(
                primary_surface="孙百草",
                presence="appeared",
                evidence_fact_ids=["f2_1"],
            )
        ],
    )

    state_r2 = {
        "pipeline_input": SimpleNamespace(turn_index=2, user_input="打听消息", pending=SimpleNamespace()),
        "pending": SimpleNamespace(),
        "trace_id": "trace-r2",
        "cache": {"key": "r2"},
        "write_db": True,
        "scene_facts": round2_scene_facts,
        "updated_state": CurrentState(
            scene=SceneState(
                scene_id="scene_r2",
                location="百草堂",
                participants=["萧欢", "孙百草"],
            ),
            player=PlayerState(name="萧欢"),
        ),
        "effective_scene_text": "萧欢向孙百草拱手问好。",
        "core_memory": core_memory,
        "existing_npc_profiles": r2_existing_npcs,
        "runtime_services": SimpleNamespace(gateway=lambda **kw: gateway),
    }

    result_r2 = confirmation_nodes.npc_profile_update_node(state_r2)

    # 验证 Round 2 孙百草档案依然稳定存在且只有 1 个，命中计数递增，不会重复创建
    assert len(npc_store.list_all()) == 1
    updated_sun = npc_store.get_by_id(sun_profile.npc_id)
    assert updated_sun is not None
    assert updated_sun.name_hit_count == 2
    assert updated_sun.name == "孙百草"
    assert updated_sun.last_known_location == "百草堂"
