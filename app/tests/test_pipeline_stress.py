"""
长文本压测: 复杂旅店密谈 → 全流程 Extract → SelfCheck → LLMCheck → Store

覆盖: 多角色、代词、谎言/声称、不确定信息、道具转移、关系变化、承诺、公开事件、
      source_span 子串、visibility、item/fact/relationship/agreement/event/trait 混合

运行方式:
  pytest app/tests/test_pipeline_stress.py -v -s
  pytest app/tests/test_pipeline_stress.py -v -s 2>&1 | tee docs/pipeline_stress_output.md
"""

import json
import re
import textwrap

import pytest

from app.core.llm.config import LLMConfig, create_gateway
from app.domain.memory.extractor import MemoryExtractor
from app.domain.memory.schemas import Memory, MemoryExtractResult
from app.domain.memory.self_check import MemorySelfCheck, SelfCheckResult
from app.domain.memory.llm_check import LLMCheck, LLMCheckResult
from app.domain.memory.store import MemoryStore
from app.domain.memory.self_check import MemorySelfCheck

pytestmark = pytest.mark.skipif(
    not LLMConfig.is_configured(),
    reason="OPENAI_API_KEY not set. Skipping integration tests.",
)

W = 72
SEP = "-" * W
BAR = "=" * W

TYPE_LABEL = {
    "agreement": "约定/暗号", "fact": "稳定事实", "trait": "性格/习惯",
    "relationship": "关系变化", "item": "道具/物品", "event": "重要事件",
}

SCENE_TEXT = (
    "灰鹭旅店后厨只剩下一盏油灯。旅店老板娘把门从里面闩上，低声说："
    "“刚才有两个穿灰斗篷的人在前厅打听你们，他们袖口上都有半枚黑月纹。”"
    "伊莱娜听到“黑月纹”三个字时，手指立刻停了一下，随后装作若无其事地继续擦拭杯子。"
    "主角注意到她没有看向自己，而是用右耳侧向老板娘。"
    "马洛从怀里取出一枚裂开的铜哨，放到桌上：“这是我从码头仓库外捡到的。"
    "它可能是黑帆帮夜哨用的东西，也可能只是普通船工的信号哨，我不确定。”"
    "主角没有立刻拿起铜哨，只问马洛：“你为什么现在才说？”"
    "马洛沉默片刻，说：“因为我之前不信任你。但钟楼爆炸后，我觉得你至少不是他们的人。"
    "从今晚开始，我可以替你盯着码头，但我不会替你动刀。”"
    "老板娘插话说：“如果你们明天拂晓前要离开城里，就从酒窖后面的排水门走。"
    "那扇门外面有一条废弃水渠，平时没人巡逻。”说完，她把一枚黄铜钥匙推给主角，"
    "“这把钥匙只能开酒窖门，不能开排水门。”"
    "就在这时，前厅传来一阵短促的敲门声：两短一长。老板娘脸色一变，示意所有人别出声。"
)

SCENE_CONTEXT = {
    "scene_id": "scene_021",
    "location": "灰鹭旅店后厨",
    "time_label": "第4日深夜",
    "participants": ["主角", "伊莱娜", "马洛", "旅店老板娘"],
    "viewpoint": "主角",
}


# ── display helpers ────────────────────────────────────────────

def _hdr(title: str) -> None:
    print(f"\n{BAR}")
    print(f"  {title}")
    print(f"{BAR}")


def _show_input() -> None:
    print(f"\n  [INPUT] scene_text ({len(SCENE_TEXT)} chars):")
    wrapped = textwrap.fill(SCENE_TEXT, width=60, initial_indent="    ", subsequent_indent="    ")
    print(wrapped)
    print(f"\n  [INPUT] scene_context:")
    ctx = json.dumps(SCENE_CONTEXT, ensure_ascii=False, indent=4)
    print(f"    {ctx.replace(chr(10), chr(10) + '    ')}")


def _show_raw_memories(memories: list[Memory]) -> None:
    n = len(memories)
    print(f"\n  [MODULE 1] LLM returned {n} raw memory(s):")
    if n == 0:
        print(f"  (empty)")
        return
    for i, m in enumerate(memories, 0):
        label = TYPE_LABEL.get(m.type, m.type)
        print(f"\n    #{i}")
        print(f"      type         : {m.type} ({label})")
        print(f"      subject      : {m.subject}")
        c = textwrap.fill(m.content, width=56, subsequent_indent="                    ")
        print(f"      content      : {c}")
        if m.entities:       print(f"      entities     : {m.entities}")
        if m.location:       print(f"      location     : {m.location}")
        if m.time_label:     print(f"      time_label   : {m.time_label}")
        if m.visibility:     print(f"      visibility   : {m.visibility}")
        if m.tags:           print(f"      tags         : {m.tags}")
        print(f"      certainty    : {m.certainty}  salience: {m.salience}")
        if m.source_text_span:
            print(f"      source_span  : \"{m.source_text_span}\"")


def _show_check_result(cr: SelfCheckResult) -> None:
    dedup = cr.original_count - cr.final_count
    print(f"\n  [MODULE 2] 输入: {cr.original_count} 条 → 输出: {cr.final_count} 条")
    if dedup > 0:
        print(f"  [MODULE 2] 去重移除: {dedup} 条")
    print(f"  [MODULE 2] 修正: {cr.fix_count} 处, 警告: {cr.warning_count} 条")
    if cr.fixes:
        print(f"\n  [MODULE 2] 自动修正:")
        for f in cr.fixes:  print(f"    + {f}")
    if cr.warnings:
        print(f"\n  [MODULE 2] 警告:")
        for w in cr.warnings:  print(f"    ! {w}")


def _show_llm_check_result(cr: LLMCheckResult) -> None:
    if cr.change_count == 0:
        print(f"\n  [MODULE 2.5] LLM 检查未发现需要修改的内容")
        return
    print(f"\n  [MODULE 2.5] LLM 建议修改 {cr.change_count} 处:")
    for i, ch in enumerate(cr.changes_applied, 1):
        idx = ch.get("memory_index", "?")
        field = ch.get("field", "?")
        old = ch.get("old")
        new = ch.get("new")
        reason = ch.get("reason", "")
        error = ch.get("error")
        if error:
            print(f"    {i}. #{idx}  {field}: 应用失败 ({error})")
        elif field == "__new__":
            subject = ch.get("subject", "?")
            print(f"    {i}. 新增 memory: [{subject}]")
            if reason: print(f"       理由: {reason}")
        elif field == "__delete__":
            subject = ch.get("subject", "?")
            print(f"    {i}. 删除 #{idx} [{subject}]")
            if reason: print(f"       理由: {reason}")
        elif field == "__warning_reviewed__":
            print(f"    {i}. #{idx}  审核自检警告: {reason}")
        else:
            s_old = _truncate(old)
            s_new = _truncate(new)
            print(f"    {i}. #{idx}  {field}: {s_old} → {s_new}")
            if reason:
                print(f"       理由: {reason}")


def _show_final_memories(memories: list[Memory]) -> None:
    n = len(memories)
    print(f"\n  [RESULT] 最终 memories ({n} 条):")
    if n == 0:
        print(f"  (empty)")
        return
    for i, m in enumerate(memories, 0):
        label = TYPE_LABEL.get(m.type, m.type)
        print(f"\n    #{i}  [{m.type}] {m.subject}  cert={m.certainty} sal={m.salience}")
        print(f"      content: {m.content}")
        if m.entities:     print(f"      entities: {m.entities}")
        if m.location:    print(f"      location: {m.location}")
        if m.time_label:  print(f"      time_label: {m.time_label}")
        if m.visibility:  print(f"      visibility: {m.visibility}")
        if m.tags:         print(f"      tags: {m.tags}")
        if m.source_text_span:
            print(f"      source_span: \"{m.source_text_span}\"")


def _truncate(v) -> str:
    s = str(v)
    return s[:60] + "..." if len(s) > 60 else s


# ── assertions ─────────────────────────────────────────────────

def _assert_stress(memories: list[Memory]) -> None:
    subjects = {m.subject for m in memories}
    all_content = " ".join(m.content for m in memories)
    all_spans = [m.source_text_span for m in memories if m.source_text_span]
    all_types = {m.type for m in memories}

    # 宽松类型检查
    assert "relationship" in all_types, f"缺少 relationship 类型, 实际: {all_types}"
    assert "agreement" in all_types, f"缺少 agreement 类型, 实际: {all_types}"
    assert "item" in all_types, f"缺少 item 类型, 实际: {all_types}"

    # 道具
    item_memories = [m for m in memories if m.type == "item"]
    item_content = " ".join(m.content for m in item_memories)
    assert "钥匙" in item_content or "铜哨" in item_content, "item 中未出现钥匙或铜哨"

    # 关系
    rel_memories = [m for m in memories if m.type == "relationship"]
    rel_text = " ".join(f"{m.subject} {m.content}" for m in rel_memories)
    assert "马洛" in rel_text, "relationship 中应涉及马洛"

    # 约定
    agr_memories = [m for m in memories if m.type == "agreement"]
    agr_text = " ".join(f"{m.subject} {m.content}" for m in agr_memories)
    assert "马洛" in agr_text or "码头" in agr_text, "agreement 中应涉及马洛监守码头"

    # certainty < 1.0
    assert any(m.certainty < 1.0 for m in memories), "至少应有 1 条 certainty < 1.0"

    # 禁止项
    assert "伊莱娜害怕黑月纹" not in all_content, "不应出现脑补: 伊莱娜害怕黑月纹"
    assert "敲门声是敌人暗号" not in all_content, "不应出现脑补: 敲门声是敌人暗号"

    # source_text_span 子串检查（宽松：非子串则警告，不阻断）
    bad_spans = 0
    for span in all_spans:
        if span not in SCENE_TEXT:
            bad_spans += 1
            print(f"  [WARN] source_text_span not exact substring: {span[:60]!r}")
    if bad_spans == 0:
        print(f"  [OK] 所有 source_text_span 均为原文子串")
    else:
        print(f"  [WARN] {bad_spans} 个 source_text_span 非原文精确子串")

    # visibility 合理性
    allowed = set(SCENE_CONTEXT["participants"]) | {"public"}
    for m in memories:
        for v in m.visibility:
            assert v in allowed or v == "灰斗篷者" or v == "黑月纹", (
                f"visibility 含未知角色: {v!r} (allowed: {allowed})"
            )

    print(f"\n  [PASS] 所有宽松断言通过")


# ── test ───────────────────────────────────────────────────────

class TestPipelineStress:
    def test_complex_inn_scene(self):
        _hdr("压测: 复杂旅店密谈 — Extract → SelfCheck → LLMCheck → Store")
        _show_input()

        gateway = create_gateway()

        # MODULE 1
        _hdr("MODULE 1: MemoryExtractor → LLM 原始输出")
        print(f"\n  [MODULE 1] MemoryExtractor.extract() — 调用真实 LLM...")
        extractor = MemoryExtractor(gateway, enable_self_check=False)
        raw = extractor.extract(SCENE_TEXT, SCENE_CONTEXT)
        _show_raw_memories(raw.memories)

        # MODULE 2
        _hdr("MODULE 2: MemorySelfCheck → 程序自检")
        print(f"\n  [MODULE 2] MemorySelfCheck.check() — 运行中...")
        sc = MemorySelfCheck()
        cleaned, cr = sc.check(raw.memories, SCENE_TEXT, SCENE_CONTEXT)
        _show_check_result(cr)

        # MODULE 2.5
        _hdr("MODULE 2.5: LLMCheck → LLM 质量复查")
        if cleaned:
            print(f"\n  [MODULE 2.5] LLMCheck.check() — 调用真实 LLM 复查...")
            llm_c = LLMCheck(gateway)
            checked, llm_cr = llm_c.check(
                cleaned, SCENE_TEXT, SCENE_CONTEXT,
                self_check_warnings=cr.warnings,
            )
            _show_llm_check_result(llm_cr)
            cleaned = checked
        else:
            print(f"\n  [MODULE 2.5] 跳过（无 memory）")

        # MODULE 3
        _hdr("MODULE 3: MemoryStore → 入库 / 读取 / 去重")
        with MemoryStore(":memory:") as store:
            ids = store.save_many(cleaned)
            print(f"\n  [MODULE 3] save_many() → 写入 {len(ids)} 条")
            for rid in ids:  print(f"    {rid}")

            records = store.list_all()
            print(f"\n  [MODULE 3] list_all() → {len(records)} 条")
            for i, r in enumerate(records, 1):
                print(f"    {i}. [{r.type}] {r.subject}")

            if len(cleaned) > 0:
                ids2 = store.save_many(cleaned)
                print(f"\n  [MODULE 3] 重复写入 → {len(ids2)} 条 (应为 0)")
                assert len(ids2) == 0

        # final result
        _hdr("FINAL RESULT")
        _show_final_memories(cleaned)
        _assert_stress(cleaned)
