"""
Playground: 调整下方 SCENE_TEXT / SCENE_CONTEXT 后直接运行即可查看全流程输出.

用法:
    python playground.py
    python playground.py 2>&1 | tee docs/playground_output.md
"""

import json
import textwrap

from app.core.llm.config import LLMConfig, create_gateway
from app.domain.recall_memory.schemas import Memory
from app.domain.recall_memory.self_check import SelfCheckResult
from app.domain.recall_memory.llm_check import LLMCheckResult
from app.workflows.extract_memory import run_extract_memory_workflow

# ══════════════════════════════════════════════════════════════════
#  修改下面的文本和上下文即可测试不同场景
# ══════════════════════════════════════════════════════════════════

SCENE_TEXT = (
    "面对这突如其来的遭遇战，你没有丝毫慌乱。你很清楚，作为一个穿着丝绒外套的法师，让敌人近身就是最大的失态。“莎拉，左边那个交给你，别让血溅到米莉裙子上。”你冷静地下达指令，同时手中的红宝石手杖在空中划出一道优雅的弧线。 奥术的光辉闪过，那只威胁最大的地卜师瞬间变成了一只滑稽的秃毛羊，它原本用来吟唱咒语的嘴现在正忙着咀嚼地上的青草。紧接着，你左手向下一压，一道寒气激射而出，那只举着镐头冲过来的狗头人瞬间被冻得瑟瑟发抖，连迈步都困难。“哼，还不赖。” 莎拉·月刃冷笑一声，身形如鬼魅般冲出。她并没有去管那只被你戏耍的羊，也没有理会那个被冻成冰棍的倒霉蛋，而是直接扑向了最后那个试图逃跑的狗头人。 两道寒光闪过，那只狗头人甚至没来得及尖叫就倒在了地上。此时，你手中的火球术也吟唱完毕。你并没有把它丢向已经被莎拉解决的目标，而是精准地砸在了那个被寒冰箭减速的狗头人身上。 **“轰！”** 火焰与冰霜的碰撞产生了剧烈的蒸汽。战斗结束得比预想中还要快。米莉一直紧紧抓着你的衣角，直到此刻才敢睁开眼睛。她看到的是你依然整洁的背影，以及那只正在这血腥战场中央、显得格格不入的秃毛绵羊。“它……它变成羊了？”米莉惊讶地捂住了嘴，随即忍不住“噗嗤”一声笑了出来，恐惧感荡然无存，“瓦伦丁先生，这太神奇了！”莎拉甩了甩匕首上的血迹，走回来深深看了你一眼：“控制优先于杀戮。我见过很多只会乱丢火球把场面搞得一团糟的人类法师，你不一样。你的脑子比你的脸好用。”解决了路上的小插曲，你们很快就看到了北郡修道院那标志性的白色高塔。 修道院的守卫看到你们（尤其是看到一个拿着手杖的贵族法师和一个暗夜精灵）有些惊讶，但米莉立刻上前解释了情况。此时，夜色已深，修道院的钟声悠悠响起。米莉站在修道院的门口，有些依依不舍。她怀里的葡萄酒完好无损，但她似乎更在意别的东西。 “瓦伦丁先生，还有莎拉姐姐……谢谢你们。”她红着脸，目光始终停留在你身上，“如果……如果以后您累了，或者只是想喝一杯新酿的葡萄酒，北郡的大门永远为您敞开。”"
)

SCENE_CONTEXT = {
    "scene_id": "scene_007",
    "location": "回音山路口",
    "time_label": "第1日夜晚",
    "participants": ["主角", "米莉·奥斯沃斯", "莎拉·月刃"],
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

DB_PATH = "data/memories.db"


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
    print(f"\n  [MODULE 1] run_extract_memory_workflow() — 调用真实 LLM...")

    wfr = run_extract_memory_workflow(SCENE_TEXT, SCENE_CONTEXT, gateway, DB_PATH)

    _show_raw_memories(wfr.raw_memories)

    # MODULE 2: 程序自检
    _hdr("MODULE 2: MemorySelfCheck → 程序自检")
    if wfr.self_check_result:
        _show_check_result(wfr.self_check_result)
    else:
        print(f"\n  [MODULE 2] 跳过（无 memory）")

    # MODULE 2.5: LLM 复查
    _hdr("MODULE 2.5: LLMCheck → LLM 质量复查")
    if wfr.llm_check_result:
        _show_llm_check_result(wfr.llm_check_result)
    else:
        print(f"\n  [MODULE 2.5] 跳过（无 memory）")

    # MODULE 3: 存储
    _hdr("MODULE 3: MemoryStore → 入库 / 读取 / 去重")
    print(f"\n  [MODULE 3] 数据库: {DB_PATH}")
    print(f"  [MODULE 3] 存入前: {wfr.store_count_before} 条")
    print(f"  [MODULE 3] 本次写入: {len(wfr.saved_ids)} 条")
    for rid in wfr.saved_ids:
        print(f"    {rid}")
    duplicate_count = max(0, len(wfr.final_memories) - len(wfr.saved_ids))
    if duplicate_count > 0:
        print(f"\n  [MODULE 3] 重复写入忽略: {duplicate_count} 条 (去重生效)")
    print(f"\n  [MODULE 3] 存入后总数: {wfr.store_total} 条")

    # 最终结果
    _hdr("FINAL RESULT")
    _show_final_memories(wfr.final_memories)


if __name__ == "__main__":
    main()
