"""
Playground: 调整下方 SCENE_TEXT / SCENE_CONTEXT 后直接运行即可查看全流程输出.

用法:
    python playground.py
    python playground.py 2>&1 | tee docs/playground_output.md
"""

import json
import textwrap

from app.core.llm.config import LLMConfig, create_gateway
from app.domain.memory.extractor import MemoryExtractor
from app.domain.memory.schemas import Memory, MemoryExtractResult
from app.domain.memory.self_check import MemorySelfCheck, SelfCheckResult
from app.domain.memory.llm_check import LLMCheck, LLMCheckResult
from app.domain.memory.store import MemoryStore

# ══════════════════════════════════════════════════════════════════
#  修改下面的文本和上下文即可测试不同场景
# ══════════════════════════════════════════════════════════════════

SCENE_TEXT = (
    "旅店里人声鼎沸，空气中混合着烤野猪肉的油脂味、矮人烟斗的辛辣味以及廉价麦酒发酵过度的酸味。对于习惯了暴风城贵族区香薰的你来说，这里的气味简直是对鼻腔的侮辱。你坐在角落的一张圆桌旁，手中虽然拿着那把镶嵌着红宝石的手杖，但那只是为了让你看起来不像个普通的流浪汉。你点了一杯旅店老板法雷的一般货色，仅仅抿了一口就皱起了眉头——这简直是马尿。就在这时，旅店厚重的橡木门被推开了。一阵带着森林清露气息的晚风吹散了些许浑浊的空气。走进来的不是满身臭汗的冒险者，而是一个看起来有些局促不安的年轻女子。她穿着并不华丽，甚至有些朴素——那是典型的北郡农场装束，洗得发白的亚麻长裙，腰间系着围裙。她的怀里紧紧抱着几瓶显然是精心包装过的葡萄酒，脸颊因为赶路（或者是羞涩）而泛着红晕。你认得她，或者说，任何在北郡修道院附近待过的人都应该见过她。 米莉·奥斯沃斯。北郡葡萄园的一朵不起眼的小花。她正试图挤过一群喧闹的矮人，走向吧台。 “法雷先生……”她的声音细若蚊呐，完全被周围的嘈杂声淹没了，“关于这一季的‘北郡红葡萄酒’，我想……”一个喝得醉醺醺的战士（看起来像是刚从狗头人矿洞回来）摇摇晃晃地撞了她一下。 “嘿！小妞！别挡道，我要续杯！”米莉惊呼一声，身体失去平衡，怀里的几瓶葡萄酒眼看就要摔在地上——那可能是她这一季收成的样品，如果碎了，她这一趟就白跑了，甚至可能还要赔上不少钱。"
)

SCENE_CONTEXT = {
    "scene_id": "scene_001",
    "location": "狮王之傲旅店",
    "time_label": "第1日",
    "participants": ["主角", "米莉·奥斯沃斯", "法雷", "醉醺醺的战士"],
    "viewpoint": "主角",
}

# ══════════════════════════════════════════════════════════════════
#  显示辅助
# ══════════════════════════════════════════════════════════════════

W = 72
BAR = "=" * W

TYPE_LABEL = {
    "agreement": "约定/暗号", "fact": "稳定事实", "trait": "性格/习惯",
    "relationship": "关系变化", "item": "道具/物品", "event": "重要事件",
}


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
            print(f"      source_span  : {m.source_text_span}")


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
            print(f"      source_span: {m.source_text_span}")


def _truncate(v) -> str:
    s = str(v)
    return s[:60] + "..." if len(s) > 60 else s


# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════

def main():
    if not LLMConfig.is_configured():
        print("OPENAI_API_KEY not set. 请在 .env 中配置 OPENAI_API_KEY.")
        return

    _hdr("Playground: Extract → SelfCheck → LLMCheck → Store")
    _show_input()

    gateway = create_gateway()

    # MODULE 1: LLM 提取
    _hdr("MODULE 1: MemoryExtractor → LLM 原始输出")
    print(f"\n  [MODULE 1] MemoryExtractor.extract() — 调用真实 LLM...")
    extractor = MemoryExtractor(gateway, enable_self_check=False)
    raw = extractor.extract(SCENE_TEXT, SCENE_CONTEXT)
    _show_raw_memories(raw.memories)

    # MODULE 2: 程序自检
    _hdr("MODULE 2: MemorySelfCheck → 程序自检")
    print(f"\n  [MODULE 2] MemorySelfCheck.check() — 运行中...")
    sc = MemorySelfCheck()
    cleaned, cr = sc.check(raw.memories, SCENE_TEXT, SCENE_CONTEXT)
    _show_check_result(cr)

    # MODULE 2.5: LLM 复查
    if cleaned:
        _hdr("MODULE 2.5: LLMCheck → LLM 质量复查")
        print(f"\n  [MODULE 2.5] LLMCheck.check() — 调用真实 LLM 复查...")
        llm_c = LLMCheck(gateway)
        checked, llm_cr = llm_c.check(
            cleaned, SCENE_TEXT, SCENE_CONTEXT,
            self_check_warnings=cr.warnings,
        )
        _show_llm_check_result(llm_cr)
        cleaned = checked
    else:
        _hdr("MODULE 2.5: LLMCheck → 跳过（无 memory）")

    # MODULE 3: 存储
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

    # 最终结果
    _hdr("FINAL RESULT")
    _show_final_memories(cleaned)


if __name__ == "__main__":
    main()
