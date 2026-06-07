"""
Action Playground: 调整下方 USER_INPUT / SCENE_CONTEXT 后直接运行即可查看解析结果.

用法:
    python playground_action.py
    python playground_action.py 2>&1 | tee docs/playground_action_output.md
"""

import json
import textwrap

from app.core.llm.config import LLMConfig, create_gateway
from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult, AtomicAction, Anchor
from app.domain.action.self_check import ActionSelfCheck

# ══════════════════════════════════════════════════════════════════
#  修改下面的文本和上下文即可测试不同场景
# ══════════════════════════════════════════════════════════════════

USER_INPUT = (
    "我站在街角踌躇，突然想到背包里还有一颗藏尸者制造的防腐过的美女头颅。这玩意可不能随意带到公爵府或暴风要塞，还是先想办法处理掉吧。我决定先去猪和哨声旅店寻找肖尔，汇报一下我维护了夜色镇的治安，帮助了守备官，这也许能让他对我看法改观。并且询问他是否能找到能辨认头颅的人。如果没有，我将去已宰的羔羊找黑暗缚灵者们处理掉头颅，换取一些东西。"
)

SCENE_CONTEXT = {
    "scene_id": "scene_010",
    "location": "狮王之傲旅店",
    "time_label": "第1日",
    "participants": ["主角", "米莉·奥斯沃斯", "醉醺醺的战士"],
    "viewpoint": "主角",
}

# ══════════════════════════════════════════════════════════════════
#  显示辅助
# ══════════════════════════════════════════════════════════════════

W = 72
BAR = "=" * W

ACTION_TYPE_LABEL = {
    "move": "移动", "observe": "观察", "inspect_object": "检查物品",
    "interact_object": "使用物品", "speak": "对话", "signal_ally": "发信号",
    "instruct_ally": "指挥同伴", "hide_or_stealth": "潜行/伪装",
    "attack": "攻击", "defend": "防御", "cast_or_use_power": "施法/异能",
    "wait": "等待", "plan": "制定计划", "other": "其他",
}

ANCHOR_KIND_LABEL = {
    "character": "角色", "location": "地点", "item": "物品",
    "organization": "组织", "concept": "概念", "action_keyword": "动作关键词",
    "past_reference": "旧事引用", "temporal_reference": "时间词",
    "condition": "条件词", "unknown": "未分类",
}


def _hdr(title: str) -> None:
    print(f"\n{BAR}")
    print(f"  {title}")
    print(f"{BAR}")


def _show_input() -> None:
    print(f"\n  [INPUT] user_input:")
    wrapped = textwrap.fill(USER_INPUT, width=60, initial_indent="    ", subsequent_indent="    ")
    print(wrapped)
    print(f"\n  [INPUT] scene_context:")
    ctx = json.dumps(SCENE_CONTEXT, ensure_ascii=False, indent=4)
    print(f"    {ctx.replace(chr(10), chr(10) + '    ')}")


def _show_actions(actions: list[AtomicAction]) -> None:
    n = len(actions)
    print(f"\n  [MODULE 1] LLM returned {n} atomic action(s):")
    if n == 0:
        print(f"  (empty)")
        return
    for a in actions:
        label = ACTION_TYPE_LABEL.get(a.action_type, a.action_type)
        print(f"\n    [{a.action_id}] seq={a.sequence}  [{a.action_type}] {a.verb}")
        print(f"      actor       : {a.actor}")
        print(f"      raw_text    : \"{a.raw_text}\"")
        if a.target:     print(f"      target      : {a.target}")
        if a.method:     print(f"      method      : {a.method}")
        if a.goal:       print(f"      goal        : {a.goal}")
        if a.condition:  print(f"      condition   : {a.condition}")
        print(f"      needs_roll  : {a.likely_needs_roll}", end="")
        if a.roll_hint:  print(f"  hint={a.roll_hint}", end="")
        print()
        print(f"      confidence  : {a.confidence}")


def _show_anchors(anchors: list[Anchor]) -> None:
    n = len(anchors)
    print(f"\n  [MODULE 1] {n} anchor(s):")
    if n == 0:
        print(f"  (empty)")
        return
    for a in anchors:
        kind_label = ANCHOR_KIND_LABEL.get(a.kind, a.kind)
        print(f"\n    [{a.kind}] {a.text}  →  {a.normalized}")
        print(f"      source      : {a.source}")
        print(f"      action_ids  : {a.action_ids}")
        print(f"      confidence  : {a.confidence}")


def _show_turn_intent(intent: str) -> None:
    print(f"\n  [INTENT] {intent}")


def _show_ambiguities(ambiguities: list[str]) -> None:
    if not ambiguities:
        return
    print(f"\n  [AMBIGUITIES]")
    for a in ambiguities:
        print(f"    ? {a}")


def _show_final_result(result: ActionParseResult) -> None:
    n = len(result.atomic_actions)
    print(f"\n  [RESULT] 最终 {n} actions, {len(result.anchors)} anchors")
    _show_turn_intent(result.turn_intent)
    _show_ambiguities(result.ambiguities)

    print(f"\n  ── Actions ──")
    if n == 0:
        print(f"  (empty)")
    else:
        for a in result.atomic_actions:
            label = ACTION_TYPE_LABEL.get(a.action_type, a.action_type)
            print(f"    [{a.action_id}] seq={a.sequence}  [{a.action_type}] {a.actor} {a.verb} → {a.target or '-'}  roll={a.likely_needs_roll}")

    print(f"\n  ── Anchors ──")
    if not result.anchors:
        print(f"  (empty)")
    else:
        for a in result.anchors:
            kind_label = ANCHOR_KIND_LABEL.get(a.kind, a.kind)
            print(f"    [{kind_label}] {a.text}  →  {a.normalized}  (actions: {a.action_ids})")


# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════

def main():
    if not LLMConfig.is_configured():
        print("OPENAI_API_KEY not set. 请在 .env 中配置 OPENAI_API_KEY.")
        return

    _hdr("Action Playground: Parse → SelfCheck")
    _show_input()

    gateway = create_gateway()

    # MODULE 1: LLM 解析
    _hdr("MODULE 1: ActionParser → LLM 原始输出")
    print(f"\n  [MODULE 1] ActionParser.parse() — 调用真实 LLM...")
    parser = ActionParser(gateway)
    result = parser.parse(USER_INPUT, SCENE_CONTEXT)
    _show_turn_intent(result.turn_intent)
    _show_actions(result.atomic_actions)
    _show_anchors(result.anchors)
    _show_ambiguities(result.ambiguities)

    # MODULE 2: 程序自检
    _hdr("MODULE 2: ActionSelfCheck → 程序自检")
    print(f"\n  [MODULE 2] ActionSelfCheck.check() — 运行中...")
    sc = ActionSelfCheck()
    cleaned = sc.check(result, USER_INPUT, SCENE_CONTEXT)

    _show_turn_intent(cleaned.turn_intent)
    _show_actions(cleaned.atomic_actions)
    _show_anchors(cleaned.anchors)
    _show_ambiguities(cleaned.ambiguities)

    # 最终结果
    _hdr("FINAL RESULT")
    _show_final_result(cleaned)


if __name__ == "__main__":
    main()
