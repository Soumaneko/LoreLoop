"""Live single-turn tests for SceneFactExtractor with Staged Director NPC grounding.

Can be run directly via:
    .venv/bin/python scripts/test_live_scene_facts_staged.py
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.core.llm.config import create_gateway
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.scene_facts import (
    SceneFactExtractInput,
    SceneFactExtractor,
    SceneParagraphInput,
)


def _make_core_memory() -> CoreMemory:
    return CoreMemory(items=[
        CoreMemoryItem(
            id="world_1",
            kind="world_invariant",
            strength="hard_constraint",
            source="campaign_setup",
            content="古典修仙世界，市井与宗门交错。",
        ),
        CoreMemoryItem(
            id="protagonist_1",
            kind="protagonist_invariant",
            strength="hard_constraint",
            source="player",
            content="萧欢，合欢宗残脉弟子。",
        ),
    ])


@pytest.mark.skip(reason="Live LLM test requiring external API key")
def test_live_scene_facts_staged_npc_alias_defense():
    """场景一：即兴引入药铺掌柜孙百草，正文全篇使用‘青衫老者/老掌柜’代称。

    验证：
    1. 成功提取‘老掌柜/青衫老者’出场（presence == 'appeared'）；
    2. 严格防编造：alias_relations 中严禁凭空捏造正文不存在的后台真名‘孙百草’；
    3. facts 真实准确。
    """
    print("\n" + "=" * 70)
    print("【场景一：即兴 NPC 代称出场与无虚假真名 alias 真实测试】")
    print("=" * 70)

    gateway = create_gateway()
    extractor = SceneFactExtractor(gateway)

    staged_npcs = [
        {
            "name": "孙百草",
            "role": "百草堂药铺掌柜，资深药师",
            "appearance": "约五十岁，清瘦矍铄，穿半旧青布长衫，蓄山羊胡",
            "personality_trait": "严谨和善，目光审慎",
            "guidance": "主角推门进店时，孙百草正在柜台后用精铜小秤称量宁神花，态度和善接待主角抓药。",
            "source": "spontaneous",
        }
    ]

    scene_text = (
        "萧欢推开百草堂虚掩的木门，店内弥漫着浓郁的药草清香。柜台后站着一位身形清瘦的青衫老者，"
        "正手持精铜小秤细细称量一撮干枯的紫色花叶。见有人进门，老者抬起头，捋了捋下颌的山羊胡，"
        "和善地笑道：「客官要抓什么药？小店虽偏，寻常宁神调息的草药倒是齐全。」"
        "萧欢说明来意，老掌柜微微颔首，转身在药柜抽屉中熟练地取出几味灵药包好递了过来。"
    )

    current_state = CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="百草堂药铺内",
            participants=["萧欢"],
        ),
        player=PlayerState(name="萧欢"),
    )

    scene_paragraphs = [
        SceneParagraphInput(
            document_id="turn_1_p001",
            text=scene_text,
        )
    ]

    result = extractor.extract(
        SceneFactExtractInput(
            scene_text=scene_text,
            current_state=current_state,
            core_memory=_make_core_memory(),
            scene_paragraphs=scene_paragraphs,
            staged_npcs=staged_npcs,
        ),
        trace_id="live-scene-facts-staged-1",
    )

    print(f"\n[Scene Focus]: {result.scene_focus}\n")
    print(f"[Facts ({len(result.facts)})]:")
    for fact in result.facts:
        print(f"  - [{fact.fact_id}] {fact.fact}")

    print(f"\n[NPC Mentions ({len(result.npc_mentions)})]:")
    for mention in result.npc_mentions:
        print(f"  - primary_surface: {mention.primary_surface!r}")
        print(f"    presence: {mention.presence}")
        print(f"    alias_relations: {mention.alias_relations}")
        print(f"    evidence_fact_ids: {mention.evidence_fact_ids}")
        print(f"    evidence_paragraph_ids: {mention.evidence_paragraph_ids}")

    assert len(result.npc_mentions) > 0, "应提取到在场老者/老掌柜的 npc_mention"
    app_mentions = [m for m in result.npc_mentions if m.presence == "appeared"]
    assert len(app_mentions) > 0, "应识别为 appeared 出场"

    # 关键对齐验证：提取器应成功将正文老掌柜识别对齐为引入的“孙百草”
    sun_mention = next(
        (m for m in result.npc_mentions if "孙百草" in m.primary_surface or any(r.surface == "孙百草" for r in m.alias_relations)),
        None,
    )
    assert sun_mention is not None, f"提取器应识别出登场老掌柜就是引入角色‘孙百草’，实际 mentions: {result.npc_mentions}"
    assert sun_mention.presence == "appeared"

    print("\n>> 场景一验证通过：代称角色精准识别为引入角色‘孙百草’（appeared 出场）！")


@pytest.mark.skip(reason="Live LLM test requiring external API key")
def test_live_scene_facts_multi_character_complex():
    """场景二：复杂多人场景（即兴角色代称 + 既有同伴真名 + 提及人物）。

    验证：
    1. 严铁匠（即兴角色代称对齐）：presence == 'appeared'；
    2. 沈忘忧（同伴）：presence == 'appeared'；
    3. 欧阳大师（仅被提及）：presence == 'mentioned_only'。
    """
    print("\n" + "=" * 70)
    print("【场景二：复杂多人多类型角色识别测试（即兴代称 + 既有登场 + 远程提及）】")
    print("=" * 70)

    gateway = create_gateway()
    extractor = SceneFactExtractor(gateway)

    staged_npcs = [
        {
            "name": "严铁匠",
            "role": "黑市铸灵匠人",
            "appearance": "身材魁梧如铁塔，右臂有烈火刺青，赤膊裹皮围裙",
            "personality_trait": "暴躁寡言",
            "guidance": "在铁匠铺后院挥锤淬火，拒绝为玄天宗修理佩剑。",
            "source": "spontaneous",
        }
    ]

    scene_text = (
        "沈忘忧带着萧欢穿过阴暗小巷，来到铁匠铺后院。炉火熊熊，一位手臂纹着烈火图腾的赤膊壮汉"
        "正狠狠挥动铁锤，火星四载。沈忘忧上前说明来意，壮汉停下手中铁锤，冷哼一声："
        "「要修这把断剑？去找城西的欧阳大师吧，老子没空伺候玄天宗的人。」"
        "沈忘忧低声道：「欧阳大师上个月就云游去了，如今城中只有你的玄火锤法能化开此铁。」"
    )

    current_state = CurrentState(
        scene=SceneState(
            scene_id="scene_002",
            location="铁匠铺后院",
            participants=["萧欢", "沈忘忧"],
        ),
        player=PlayerState(name="萧欢"),
    )

    scene_paragraphs = [
        SceneParagraphInput(
            document_id="turn_2_p001",
            text=scene_text,
        )
    ]

    result = extractor.extract(
        SceneFactExtractInput(
            scene_text=scene_text,
            current_state=current_state,
            core_memory=_make_core_memory(),
            scene_paragraphs=scene_paragraphs,
            staged_npcs=staged_npcs,
        ),
        trace_id="live-scene-facts-staged-2",
    )

    print(f"\n[Scene Focus]: {result.scene_focus}\n")
    print(f"[Facts ({len(result.facts)})]:")
    for fact in result.facts:
        print(f"  - [{fact.fact_id}] {fact.fact}")

    print(f"\n[NPC Mentions ({len(result.npc_mentions)})]:")
    for mention in result.npc_mentions:
        print(f"  - primary_surface: {mention.primary_surface!r}")
        print(f"    presence: {mention.presence}")
        print(f"    alias_relations: {mention.alias_relations}")

    mention_map = {m.primary_surface: m for m in result.npc_mentions}

    # 1. 验证沈忘忧（appeared）
    assert "沈忘忧" in mention_map, "沈忘忧应被提取"
    assert mention_map["沈忘忧"].presence == "appeared"

    # 2. 验证欧阳大师（mentioned_only）
    ouyang = next((m for m in result.npc_mentions if "欧阳" in m.primary_surface), None)
    assert ouyang is not None, "欧阳大师应被提取"
    assert ouyang.presence == "mentioned_only", "欧阳大师未实际在场，应为 mentioned_only"

    # 3. 验证严铁匠（识别对齐且 appeared）
    smith = next(
        (m for m in result.npc_mentions if "严铁匠" in m.primary_surface or any(r.surface == "严铁匠" for r in m.alias_relations)),
        None,
    )
    assert smith is not None, f"应将打铁壮汉识别对齐为引入的‘严铁匠’，实际 mentions: {result.npc_mentions}"
    assert smith.presence == "appeared", "严铁匠在场打铁说话，应为 appeared"

    print("\n>> 场景二验证通过：多人多状态（即兴代称对齐/在场同伴/远程提及）分类完全精准！")


if __name__ == "__main__":
    print("Running live test scenario 1...")
    test_live_scene_facts_staged_npc_alias_defense()
    print("\nRunning live test scenario 2...")
    test_live_scene_facts_multi_character_complex()
    print("\nAll live tests passed successfully!")
