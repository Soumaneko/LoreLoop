"""Live single-turn tests for Generation Director spontaneous NPC introduction."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.core.llm.config import create_gateway
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.director.generation import run_generation_director_agent
from app.domain.director_npc.schemas import DirectorNPCDraft, KnownAbility
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork


@pytest.mark.skip(reason="Live LLM test requiring external API key")
def test_scenario_1_spontaneous_creation(tmp_path: Path):
    """Scenario 1: drafts 为空，玩家前往药铺买药，导演应即兴捏出药铺掌柜/医师。"""
    print("\n" + "=" * 70)
    print("【场景一：即兴创建轻量 NPC 测试（Drafts 为空）】")
    print("=" * 70)

    gateway = create_gateway()
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path / "s1"))

    user_input = "推门走进路边的‘百草堂’药铺，想找掌柜买几味安神灵药。"
    current_state = CurrentState(
        scene=SceneState(
            scene_id="scene_12",
            location="百草堂药铺门前",
            participants=["萧欢"],
        ),
        player=PlayerState(name="萧欢"),
    )
    core_memory = CoreMemory(items=[
        CoreMemoryItem(
            id="world_1",
            kind="world_invariant",
            strength="hard_constraint",
            source="campaign_setup",
            content="古典奇幻修仙世界，陷空山下散修与市井聚集。",
        ),
        CoreMemoryItem(
            id="protagonist_1",
            kind="protagonist_invariant",
            strength="hard_constraint",
            source="player",
            content="萧欢，远古宗门合欢宗残脉传人。",
        ),
    ])

    stage, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=core_memory,
        current_state=current_state,
        user_input=user_input,
        context_facts=["山脚下有一家名为百草堂的陈旧药铺。"],
        recent_summaries=[],
        turn_index=12,
        trace_id="live-test-spontaneous-s1",
        npc_profiles=[],
    )

    print(f"\n[导演决策 Reasoning]:\n{decision.reasoning}\n")
    print(f"[下发请求 Requests 数量]: {len(decision.requests)}")
    for i, req in enumerate(decision.requests, 1):
        print(f"  Request {i}: skill={req.skill}")
        print(f"  Arguments: {json.dumps(req.arguments, ensure_ascii=False)}")
        print(f"  Guidance: {req.guidance}")

    print(f"\n[Stage Materials 产物]: {len(stage.materials)}")
    for mat in stage.materials:
        print(f"  Material ID: {mat.resource_id}, Kind: {mat.kind}")
        print(f"  Payload: {json.dumps(mat.payload, ensure_ascii=False, indent=2)}")

    # 验证是否成功调用了 introduce_npc 且为 spontaneous
    intro_reqs = [r for r in decision.requests if r.skill == "introduce_npc"]
    assert len(intro_reqs) > 0, "场景一应触发 introduce_npc 技能"
    req = intro_reqs[0]
    assert "spontaneous" in req.arguments, "drafts 为空时应调用 spontaneous 即兴建卡"
    spontaneous_data = req.arguments["spontaneous"]
    print(f"\n>> 场景一验证通过！成功即兴生成角色：{spontaneous_data.get('name')}（{spontaneous_data.get('role')}）")


@pytest.mark.skip(reason="Live LLM test requiring external API key")
def test_scenario_2_draft_first_priority(tmp_path: Path):
    """Scenario 2: drafts 中已有准备好的灵材商人周行之，玩家寻找商人，导演应优先起用草稿。"""
    print("\n" + "=" * 70)
    print("【场景二：Draft-First 存货优先测试（Drafts 中已有匹配角色）】")
    print("=" * 70)

    gateway = create_gateway()
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path / "s2"))

    # 在 stores.director_npc 中预先存入草稿
    draft_id = "director_npc_zhou_01"
    with stores.director_npc as d_store:
        d_store.save(DirectorNPCDraft(
            draft_id=draft_id,
            status="prepared",
            name="周行之",
            role="暗市商会灵材商人",
            appearance="三十来岁，穿灰褐短打，腰悬青铜小秤与制式储物袋",
            personality_trait="精明市侩，眼光毒辣，极善察言观色",
            known_abilities=[KnownAbility(name="灵材辨识", description="精通各类矿石灵草鉴定")],
            core_motivation="为暗市商会开辟陷空山秘密走私通道并谋取私利",
            hidden_secret="随身暗袋中藏有暗市商会密令与真伪账本",
            conflict_style="遇事先赔笑周旋，势头不对立刻舍财脱身",
            social_style="热情市侩但戒备深沉",
            created_turn=10,
        ))

    user_input = "前往山脚林边，寻找傍晚那帮自称倒卖灵材的商人搭话。"
    current_state = CurrentState(
        scene=SceneState(
            scene_id="scene_13",
            location="陷空山山脚林间",
            participants=["萧欢"],
        ),
        player=PlayerState(name="萧欢"),
    )
    core_memory = CoreMemory(items=[
        CoreMemoryItem(
            id="world_1",
            kind="world_invariant",
            strength="hard_constraint",
            source="campaign_setup",
            content="古典奇幻修仙世界。",
        ),
    ])

    stage, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=core_memory,
        current_state=current_state,
        user_input=user_input,
        context_facts=["山脚下有暗市商会的商人周行之正在暗中打探灵脉。"],
        recent_summaries=[],
        turn_index=13,
        trace_id="live-test-draft-first-s2",
        npc_profiles=[],
    )

    print(f"\n[导演决策 Reasoning]:\n{decision.reasoning}\n")
    print(f"[下发请求 Requests 数量]: {len(decision.requests)}")
    for i, req in enumerate(decision.requests, 1):
        print(f"  Request {i}: skill={req.skill}")
        print(f"  Arguments: {json.dumps(req.arguments, ensure_ascii=False)}")
        print(f"  Guidance: {req.guidance}")

    print(f"\n[Stage Materials 产物]: {len(stage.materials)}")
    for mat in stage.materials:
        print(f"  Material ID: {mat.resource_id}, Kind: {mat.kind}")
        print(f"  Payload: {json.dumps(mat.payload, ensure_ascii=False, indent=2)}")

    # 验证是否遵守 Draft-First 规则使用了 npc_id
    intro_reqs = [r for r in decision.requests if r.skill == "introduce_npc"]
    assert len(intro_reqs) > 0, "场景二应触发 introduce_npc 技能"
    req = intro_reqs[0]
    assert req.arguments.get("npc_id") == draft_id, f"场景二应优先起用草稿 {draft_id}"
    assert "spontaneous" not in req.arguments or req.arguments.get("spontaneous") is None, "有草稿时严禁使用 spontaneous"
    print(f"\n>> 场景二验证通过！成功遵守 Draft-First 规则起用草稿：{draft_id}（周行之）")


if __name__ == "__main__":
    with TemporaryDirectory() as temp_dir:
        test_scenario_1_spontaneous_creation(Path(temp_dir))
        test_scenario_2_draft_first_priority(Path(temp_dir))
    print("\n" + "=" * 70)
    print("全部单轮真实模型测试圆满成功！")
    print("=" * 70)
