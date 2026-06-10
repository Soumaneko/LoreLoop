"""
Action + Retrieval + Roll + Context Playground: 调整下方 USER_INPUT / SCENE_CONTEXT 后直接运行.

流程: ActionParser → ActionSelfCheck → CandidateRetriever → RollResolver → ContextAssembler

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
from app.domain.core_memory.schemas import CoreMemory, create_default_core_memory
from app.domain.current_state.store import CurrentStateStore, make_default_state
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.candidate_retriever import CandidateRetriever
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.retrieval.sub_retrievers import build_context
from app.domain.roll.resolver import RollResolver
from app.domain.roll.schemas import RollConfig, RollResultBatch
from app.domain.short_term_memory.schemas import ShortTermMemory
from app.workflows.context_assembler import PlotOutlineContext, assemble_context

# ══════════════════════════════════════════════════════════════════
#  修改下面的文本和上下文即可测试不同场景
# ══════════════════════════════════════════════════════════════════

USER_INPUT = (
    "品酒最忌讳心急，人也一样，潇洒的和米莉打个招呼，让她别忘了我把狗头人变成的那只羊，做出下次再来的约定吧。接着，和莎拉简单的聊一聊，这对明天的安排也有帮助。"
)

SCENE_CONTEXT = {
    "scene_id": "scene_010",
    "location": "北郡修道院",
    "time_label": "第1日深夜",
    "participants": ["主角", "米莉·奥斯沃斯", "莎拉·月刃"],
    "viewpoint": "主角",
}

DB_PATH = "data/memories.db"
STATE_DB_PATH = "data/current_state.db"
TOP_K = 20

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

MEMORY_TYPE_LABEL = {
    "agreement": "约定/暗号", "fact": "稳定事实", "trait": "性格/习惯",
    "relationship": "关系变化", "item": "道具/物品", "event": "重要事件",
}


def _show_candidates(candidates: list[MemoryCandidate], total_scanned: int) -> None:
    n = len(candidates)
    print(f"\n  [MODULE 3] 扫描 {total_scanned} 条记忆，命中 {n} 条候选:")
    if n == 0:
        print(f"  (empty — 没有匹配的记忆)")
        return

    retriever_labels = [
        ("entity_score", "entity_rank", "R1 entity"),
        ("subject_tag_score", "subject_tag_rank", "R2 subj+tag"),
        ("content_score", "content_rank", "R3 content"),
        ("type_intent_score", "type_intent_rank", "R4 type"),
        ("scene_context_score", "scene_context_rank", "R5 ctx"),
    ]

    for i, c in enumerate(candidates):
        label = MEMORY_TYPE_LABEL.get(c.memory_type, c.memory_type)
        content_preview = textwrap.shorten(c.content, width=80, placeholder="…")
        total_mem = int(c.score_breakdown.get("total_memories", total_scanned))
        print(f"\n  #{i + 1}  rrf={c.score:.4f}  [{c.memory_type}] {label}")
        print(f"      subject   : {c.subject}")
        print(f"      content   : {content_preview}")
        if c.entities:
            print(f"      entities  : {c.entities}")
        if c.tags:
            print(f"      tags      : {c.tags}")
        if c.location:
            print(f"      location  : {c.location}")
        if c.time_label:
            print(f"      time_label: {c.time_label}")
        print(f"      salience={c.salience}  certainty={c.certainty}")
        n_actions_hit = int(c.score_breakdown.get("num_actions_hit", 0))
        bonus = c.score_breakdown.get("multi_action_bonus", 0.0)
        max_rrf = c.score_breakdown.get("max_rrf", c.score)
        if n_actions_hit > 1:
            print(f"      actions hit: {n_actions_hit}  max_rrf={max_rrf:.4f}  bonus=+{bonus:.4f}")
        print(f"      matched_anchors : {c.matched_anchors}")
        print(f"      matched_actions : {c.matched_actions}")
        print(f"      scores & ranks (top-scored items / {total_mem} total):")
        for score_key, rank_key, label in retriever_labels:
            s = c.score_breakdown.get(score_key, 0.0)
            r = int(c.score_breakdown.get(rank_key, 0))
            if s != 0.0 or r != 0:
                print(f"        {label:>14s} : {s:>7.2f}  (rank {r}/{total_mem})")



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


def _show_rolls(batch: RollResultBatch, actions: list[AtomicAction], config: RollConfig) -> None:
    total = len(actions)
    needs_count = sum(1 for a in actions if a.likely_needs_roll)
    needs_map = {a.action_id: a.likely_needs_roll for a in actions}
    forced_count = sum(1 for r in batch.results if not needs_map.get(r.action_id, False))
    print(f"\n  [MODULE 4] Roll Pipeline — NdM Roll Resolver")
    print(f"  [MODULE 4] {total} total actions, {needs_count} need roll, min_rolls={config.min_rolls}")
    print(f"  [MODULE 4] dice_formula: {batch.dice_formula}  (dice_sides={batch.dice_sides})")
    if forced_count > 0:
        print(f"  [MODULE 4] {forced_count} action(s) forced to roll (补充至 min_rolls)")
    if batch.total_rolls == 0:
        print(f"  [MODULE 4] (no rolls needed)")
        return
    for r in batch.results:
        label = ACTION_TYPE_LABEL.get(r.action_type, r.action_type)
        hint = f"  hint={r.roll_hint}" if r.roll_hint else ""
        forced_mark = " [forced]" if not needs_map.get(r.action_id, True) else ""
        print(f"    [{r.action_id}] seq={r.sequence}  {r.actor}  [{r.action_type}] {label}{forced_mark}")
        print(f"      roll: {r.dice} = {r.value}{hint}")


def _show_context(ctx: PlotOutlineContext) -> None:
    cs = ctx.current_state
    actions = ctx.action_parse_result.atomic_actions
    rolls = ctx.roll_results
    cm = ctx.core_memory
    stm = ctx.short_term_memory
    rm = ctx.retrieved_memories

    print(f"\n  [MODULE 5] ContextAssembler → PlotOutlineContext 组装完成")
    print(f"  [MODULE 5] user_input: {textwrap.shorten(ctx.user_input, width=60, placeholder='…')}")
    print(f"  [MODULE 5]")
    print(f"  [MODULE 5] ── current_state ──")
    print(f"  [MODULE 5]   scene  : {cs.scene.scene_id}  [{cs.scene.location}] {cs.scene.time_label}")
    print(f"  [MODULE 5]            participants: {cs.scene.participants}")
    print(f"  [MODULE 5]   player : {cs.player.name}  pos={cs.player.position}  intent={cs.player.intent}")
    if cs.player.condition:
        print(f"  [MODULE 5]            condition: {cs.player.condition}")
    if cs.npcs:
        print(f"  [MODULE 5]   npcs   : {len(cs.npcs)} present")
    print(f"  [MODULE 5]   resources: money={cs.resources.money}  inventory={cs.resources.inventory}")
    print(f"  [MODULE 5]")
    print(f"  [MODULE 5] ── action_parse_result ──")
    print(f"  [MODULE 5]   turn_intent: {ctx.action_parse_result.turn_intent}")
    print(f"  [MODULE 5]   actions: {len(actions)}  anchors: {len(ctx.action_parse_result.anchors)}  ambiguities: {len(ctx.action_parse_result.ambiguities)}")
    print(f"  [MODULE 5]")
    print(f"  [MODULE 5] ── roll_results ──")
    print(f"  [MODULE 5]   formula: {rolls.dice_formula}  total: {rolls.total_rolls}")
    print(f"  [MODULE 5]")
    print(f"  [MODULE 5] ── core_memory ──")
    print(f"  [MODULE 5]   {len(cm.items)} items: {[(i.id, i.category) for i in cm.items]}")
    print(f"  [MODULE 5]")
    print(f"  [MODULE 5] ── short_term_memory ──")
    print(f"  [MODULE 5]   {len(stm.recent_scenes)} recent scenes (max {stm.max_items})")
    print(f"  [MODULE 5]")
    print(f"  [MODULE 5] ── retrieved_memories ──")
    print(f"  [MODULE 5]   candidates: {rm.total_returned} / scanned: {rm.total_scanned}")


# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════

def main():
    if not LLMConfig.is_configured():
        print("OPENAI_API_KEY not set. 请在 .env 中配置 OPENAI_API_KEY.")
        return

    _hdr("Action + Retrieval + Roll + Context Playground: Parse → SelfCheck → Retriever → Roll → Context")
    _show_input()

    gateway = create_gateway()

    # MODULE 1: LLM 解析
    _hdr("MODULE 1: ActionParser → LLM 原始输出")
    print(f"\n  [MODULE 1] ActionParser.parse() — 调用真实 LLM...")
    parser = ActionParser(gateway)
    raw = parser.parse(USER_INPUT, SCENE_CONTEXT)
    _show_turn_intent(raw.turn_intent)
    _show_actions(raw.atomic_actions)
    _show_anchors(raw.anchors)
    _show_ambiguities(raw.ambiguities)

    # MODULE 2: 程序自检
    _hdr("MODULE 2: ActionSelfCheck → 程序自检")
    print(f"\n  [MODULE 2] ActionSelfCheck.check() — 运行中...")
    sc = ActionSelfCheck()
    cleaned = sc.check(raw, USER_INPUT, SCENE_CONTEXT)

    _show_turn_intent(cleaned.turn_intent)
    _show_actions(cleaned.atomic_actions)
    _show_anchors(cleaned.anchors)
    _show_ambiguities(cleaned.ambiguities)

    # MODULE 3: 记忆候选召回
    retrieval_result: CandidateRetrievalResult | None = None
    _hdr("MODULE 3: CandidateRetriever → 记忆候选召回")
    print(f"\n  [MODULE 3] 从 {DB_PATH} 加载记忆库...")
    with MemoryStore(DB_PATH) as store:
        total = store.count()
        print(f"  [MODULE 3] 记忆库共有 {total} 条记忆")
        if total == 0:
            print(f"  [MODULE 3] 记忆库为空，跳过召回")
        else:
            retriever = CandidateRetriever(store)
            retrieval_result = retriever.retrieve(cleaned, top_k=TOP_K)
            _show_candidates(retrieval_result.candidates, retrieval_result.total_scanned)

            ctx = build_context(store.list_all(), cleaned.anchors, cleaned.atomic_actions)
            if ctx.anchors:
                print(f"\n  ── Anchor IDF weights ──")
                for i, a in enumerate(ctx.anchors):
                    w = ctx.anchor_weights[i]
                    print(f"    [{a.kind}] \"{a.text}\"  norm=\"{ctx.norm_anchors[i]}\"  weight={w:.3f}")

    # MODULE 4: Roll Pipeline
    _hdr("MODULE 4: RollResolver → NdM 骰点")
    roll_config = RollConfig(seed=42)
    resolver = RollResolver(roll_config)
    roll_batch = resolver.resolve(cleaned)
    _show_rolls(roll_batch, cleaned.atomic_actions, roll_config)

    # MODULE 5: Context Assembler
    _hdr("MODULE 5: ContextAssembler → 组装全流程上下文")
    with CurrentStateStore(STATE_DB_PATH) as state_store:
        current_state = state_store.load_or_default()
    if retrieval_result is None:
        retrieval_result = CandidateRetrievalResult(candidates=[], total_scanned=0, total_returned=0)
    core_memory = create_default_core_memory()
    short_term_memory = ShortTermMemory(recent_scenes=[], max_items=5)

    assembled = assemble_context(
        user_input=USER_INPUT,
        current_state=current_state,
        action_parse_result=cleaned,
        roll_results=roll_batch,
        core_memory=core_memory,
        short_term_memory=short_term_memory,
        retrieved_memories=retrieval_result,
    )
    _show_context(assembled)

    # 最终结果
    _hdr("FINAL RESULT")
    _show_final_result(cleaned)


if __name__ == "__main__":
    main()
