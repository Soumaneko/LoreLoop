"""
Playground: 调整下方 SCENE_TEXT / SCENE_CONTEXT 后直接运行即可查看全流程输出.

用法:
    python playground.py
    python playground.py 2>&1 | tee docs/playground_output.md
"""

import json
import textwrap

from app.core.llm.config import LLMConfig, create_gateway
from app.domain.memory.schemas import Memory
from app.domain.memory.self_check import SelfCheckResult
from app.domain.memory.llm_check import LLMCheckResult
from app.workflows.extract_memory import run_extract_memory_workflow

# ══════════════════════════════════════════════════════════════════
#  修改下面的文本和上下文即可测试不同场景
# ══════════════════════════════════════════════════════════════════

SCENE_TEXT = (
    "你轻轻摆了摆手，那把红宝石手杖在指尖转了一个漂亮的弧度，然后稳稳地点在地面上。“报答？那太沉重了，米莉小姐。”你露出了那标志性的、含着三分笑意的神情，“对于一位绅士而言，让美丽的事物免遭破碎，本身就是一种奖赏。”你稍微靠近了一些，并没有触碰她，只是目光落在了她怀中那几瓶幸免于难的葡萄酒瓶颈上。那里系着简单的**淡紫色丝带**，用来装饰这些即将出售的样品。“不过，既然你坚持……”你伸出手，轻轻指了指那条丝带，“这瓶酒是你辛苦劳作的成果，应该去它该去的地方换取金币。但这根丝带……我想，它可以作为我今晚最好的纪念品。每当我看到它，就会想起北郡那甜美的葡萄香气。”米莉愣了一下，随即脸颊烫得像熟透的苹果。她慌乱又小心翼翼地解下那根丝带，双手递到了你的掌心。丝带上还带着微弱的葡萄酒香和少女的体温。当你将丝带优雅地缠绕在手杖柄上时，你转头看了一眼窗外。狮王之傲旅店外，夜色已经彻底笼罩了艾尔文森林，远处的树影在风中摇曳，像极了潜伏的野兽。“说起来，米莉小姐。”你的眉头微微皱起，语气中多了一份真切的关怀，“天已经完全黑了。我听说通往北郡的小径最近不太平，那些贪婪的狗头人最近甚至敢袭击落单的卫兵。你一个人……打算怎么回去？”这句话仿佛戳中了米莉的软肋。她刚才只顾着送货，完全忘了回程的恐惧。此时被你提起，她的脸色瞬间变得有些苍白。“我……我本来想在天黑前赶回去的，但是法雷老板太忙了，我等了很久……”她咬了咬嘴唇，不安地抓着裙角，“或许……或许我可以在马厩里凑合一晚？但我没带够住店的钱，而且明天一早葡萄园还有很多活……”她抬起头，眼神中带着一丝期冀，又有些不敢开口的犹豫。"
)

SCENE_CONTEXT = {
    "scene_id": "scene_002",
    "location": "狮王之傲旅店",
    "time_label": "第1日夜晚",
    "participants": ["主角", "米莉·奥斯沃斯"],
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
