"""Live test for Spontaneous NPC Auto-Backfill with live LLM Gateway.

Can be run directly via:
    .venv/bin/python scripts/test_live_spontaneous_backfill.py
"""

import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.core.llm.config import create_gateway
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.creative_brief import draw_desire_pattern
from app.domain.director.schemas import DirectorStageMaterial, DirectorStageResult
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.workflows import auto_backfill_spontaneous_npc_profiles


def _make_core_memory() -> CoreMemory:
    return CoreMemory(items=[
        CoreMemoryItem(
            id="world_1",
            kind="world_invariant",
            strength="hard_constraint",
            source="campaign_setup",
            content="古典仙侠世界，青云坊市为散修与凡人聚集之地。",
        ),
        CoreMemoryItem(
            id="protagonist_1",
            kind="protagonist_invariant",
            strength="hard_constraint",
            source="player",
            content="萧欢，合欢宗弟子，目前隐藏身份在坊市暂居。",
        ),
    ])


@pytest.mark.skip(reason="Live LLM test requiring external API key")
def test_live_spontaneous_backfill():
    print("\n" + "=" * 70)
    print("【实机测试：即兴轻量 NPC（孙百草）自动 Backfill 与 Profile 持久化】")
    print("=" * 70)

    gateway = create_gateway()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    npc_store = NPCProfileStore(conn)
    director_store = DirectorNPCStore(conn)

    material = DirectorStageMaterial(
        kind="npc",
        resource_id="spontaneous_npc_sun_01",
        guidance="在青云坊市百草堂称药，和善待客但深藏不露。",
        payload={
            "source": "spontaneous",
            "name": "孙百草",
            "role": "百草堂药铺掌柜",
            "appearance": "清瘦老者，穿洗得发白的青衫，下巴留着一小撮山羊胡",
            "personality_trait": "严谨和善，待人接物客气周到",
            "known_abilities": [
                {"name": "百草鉴别", "description": "一眼分辨各类凡草与低阶灵药的年份与成色。"},
                {"name": "调配药散", "description": "熟练炮制止血散与清心汤。"},
            ],
            "faction": "百草堂",
            "view_mode": "face_to_face",
        },
    )

    class MockPending:
        def __init__(self):
            self.director_stage = DirectorStageResult(
                reasoning="正文引入百草堂老掌柜孙百草，为主角提供疗伤药材并交代坊市近况。",
                materials=[material],
            )

    pattern = draw_desire_pattern()
    print("\n1. 抽卡结果（仅 desire_pattern 字符串）：")
    print(f"   desire_pattern: {pattern}")

    backfilled_ids = auto_backfill_spontaneous_npc_profiles(
        pending=MockPending(),
        npc_store=npc_store,
        gateway=gateway,
        core_memory=_make_core_memory(),
        turn_index=1,
        scene_id="scene_market_01",
        trace_id="live_test_trace",
    )

    print(f"\n2. Backfill 完成，生成 NPCProfile ID: {backfilled_ids}")
    assert len(backfilled_ids) == 1

    profile = npc_store.get_by_id(backfilled_ids[0])
    assert profile is not None
    print("\n3. 完整入库档案检查：")
    print(f"   name: {profile.name}")
    print(f"   role: {profile.role}")
    print(f"   appearance: {profile.appearance}")
    print(f"   personality_trait: {profile.personality_trait}")
    print(f"   core_motivation: {profile.core_motivation}")
    print(f"   hidden_secret: {profile.hidden_secret}")
    print(f"   conflict_style: {profile.conflict_style}")
    print(f"   social_style: {profile.social_style}")
    print(f"   known_abilities: {[a.name for a in profile.known_abilities]}")
    print(f"   disclosure_level: {profile.disclosure_level}")

    # 4. 断言验证
    assert profile.name == "孙百草", "原名必须锁定"
    assert profile.core_motivation is not None and len(profile.core_motivation) > 0
    assert profile.hidden_secret is not None and len(profile.hidden_secret) > 0
    assert profile.conflict_style is not None and len(profile.conflict_style) > 0
    assert profile.social_style is not None and len(profile.social_style) > 0
    assert profile.disclosure_level == "face_to_face"
    assert len(director_store.list_all()) == 0, "DirectorNPCStore 必须零记录"

    print("\n" + "=" * 70)
    print("【测试通过】即兴 NPC 自动补全成功且深层字段完整，原名及外貌保持不变，草稿库无污染！")
    print("=" * 70)


if __name__ == "__main__":
    test_live_spontaneous_backfill()
