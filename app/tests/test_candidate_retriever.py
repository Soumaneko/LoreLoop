import pytest

from app.domain.action.schemas import (
    ActionParseResult,
    Anchor,
    AtomicAction,
)
from app.domain.memory.schemas import Memory, MemoryRecord
from app.domain.memory.store import MemoryStore
from app.domain.retrieval.candidate_retriever import CandidateRetriever
from app.domain.retrieval.config import RetrievalConfig


def _make_memory(**overrides) -> Memory:
    defaults = {
        "type": "fact",
        "subject": "测试记忆",
        "content": "这是一条测试记忆内容。",
        "entities": ["主角"],
        "location": "测试地点",
        "time_label": "第1日",
        "visibility": ["主角"],
        "tags": ["测试"],
        "certainty": 1.0,
        "salience": 0.5,
        "source_scene_id": "scene_test",
        "source_text_span": ["测试文本"],
    }
    defaults.update(overrides)
    return Memory(**defaults)


def _make_action(
    action_id: str,
    action_type: str,
    raw_text: str,
    **overrides,
) -> AtomicAction:
    defaults = {
        "action_id": action_id,
        "sequence": 1,
        "actor": "主角",
        "action_type": action_type,
        "raw_text": raw_text,
        "verb": raw_text[:4],
        "target": None,
        "method": None,
        "goal": None,
        "condition": None,
        "likely_needs_roll": False,
        "roll_hint": None,
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return AtomicAction(**defaults)


def _make_anchor(
    text: str,
    normalized: str | None = None,
    kind: str = "character",
    action_ids: list[str] | None = None,
    **overrides,
) -> Anchor:
    defaults = {
        "text": text,
        "normalized": normalized or text,
        "kind": kind,
        "source": "explicit",
        "action_ids": action_ids or ["A1"],
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return Anchor(**defaults)


def _make_parse_result(
    turn_intent: str = "测试意图",
    actions: list[AtomicAction] | None = None,
    anchors: list[Anchor] | None = None,
) -> ActionParseResult:
    return ActionParseResult(
        turn_intent=turn_intent,
        atomic_actions=actions or [],
        anchors=anchors or [],
    )


# ══════════════════════════════════════════════════════════════════
#  Test: 暗号召回 (Secret Signal Recall)
# ══════════════════════════════════════════════════════════════════

class TestSecretSignalRecall:
    def test_signal_memory_ranks_first(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="agreement",
                subject="沉默暗号",
                content="主角与伊莱娜约定左手轻敲两下表示保持沉默。",
                entities=["主角", "伊莱娜"],
                tags=["暗号", "约定", "沉默"],
                salience=0.8,
                certainty=0.9,
            ),
            _make_memory(
                type="trait",
                subject="伊莱娜性格",
                content="伊莱娜是一个冷静且敏锐的女性角色。",
                entities=["伊莱娜"],
                tags=["性格"],
                salience=0.5,
                certainty=0.7,
            ),
            _make_memory(
                type="fact",
                subject="码头环境",
                content="码头仓库区堆满了各种货物箱。",
                entities=["主角"],
                tags=["环境"],
                salience=0.3,
                certainty=1.0,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="signal_ally",
            raw_text="我用之前和伊莱娜约好的暗号提醒她别说话",
            verb="发信号",
            target="伊莱娜",
            method="暗号",
            goal="提醒她别说话",
        )

        anchors = [
            _make_anchor("伊莱娜", "伊莱娜", kind="character", action_ids=["A1"]),
            _make_anchor("暗号", "暗号", kind="concept", action_ids=["A1"]),
            _make_anchor("之前", "之前", kind="past_reference", action_ids=["A1"]),
            _make_anchor("别说话", "别说话", kind="action_keyword", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="用暗号提醒伊莱娜",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert len(result.candidates) >= 1
        assert result.candidates[0].memory_type == "agreement"
        assert result.candidates[0].subject == "沉默暗号"
        assert result.candidates[0].score > 0
        assert "entity_score" in result.candidates[0].score_breakdown
        assert "type_intent_score" in result.candidates[0].score_breakdown
        assert "rrf_score" in result.candidates[0].score_breakdown

        signal_score = result.candidates[0].score
        other_scores = [c.score for c in result.candidates if c.subject != "沉默暗号"]
        for s in other_scores:
            assert signal_score > s

    def test_signal_memory_scores_higher_than_entity_only(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="agreement",
                subject="沉默暗号",
                content="主角与伊莱娜约定左手轻敲两下表示保持沉默。",
                entities=["主角", "伊莱娜"],
                tags=["暗号", "约定", "沉默"],
                salience=0.8,
                certainty=0.9,
            ),
            _make_memory(
                type="trait",
                subject="伊莱娜左耳听力不佳",
                content="伊莱娜左耳听力不佳，需要大声说话才能听到。",
                entities=["伊莱娜"],
                tags=["trait", "弱点"],
                salience=0.5,
                certainty=0.8,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="signal_ally",
            raw_text="我用之前和伊莱娜约好的暗号提醒她别说话",
            verb="发信号",
            target="伊莱娜",
            method="暗号",
        )

        anchors = [
            _make_anchor("伊莱娜", "伊莱娜", kind="character", action_ids=["A1"]),
            _make_anchor("暗号", "暗号", kind="concept", action_ids=["A1"]),
            _make_anchor("之前", "之前", kind="past_reference", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="用暗号提醒伊莱娜",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert len(result.candidates) == 2
        assert result.candidates[0].subject == "沉默暗号"
        assert result.candidates[1].subject == "伊莱娜左耳听力不佳"
        assert result.candidates[0].score > result.candidates[1].score


# ══════════════════════════════════════════════════════════════════
#  Test: 门锁召回 (Door Lock Recall)
# ══════════════════════════════════════════════════════════════════

class TestDoorLockRecall:
    def test_lock_memory_ranks_high(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="item",
                subject="旧式仓库锁",
                content="主角曾在旧城区见过一种双层弹簧结构的旧式仓库锁。",
                entities=["主角", "旧城区"],
                tags=["门锁", "仓库", "旧城区"],
                salience=0.6,
                certainty=0.8,
            ),
            _make_memory(
                type="fact",
                subject="旧城区历史",
                content="旧城区在十年前经历了一场大火，大部分建筑被毁。",
                entities=["旧城区"],
                tags=["历史"],
                salience=0.4,
                certainty=0.9,
            ),
            _make_memory(
                type="trait",
                subject="主角习惯",
                content="主角习惯在进入新房间前检查门锁。",
                entities=["主角"],
                tags=["习惯"],
                salience=0.5,
                certainty=0.7,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="inspect_object",
            raw_text="我看看门锁是不是和旧城区见过的那种一样",
            verb="检查",
            target="门锁",
            method="观察",
            goal="确认门锁类型",
        )

        anchors = [
            _make_anchor("门锁", "门锁", kind="item", action_ids=["A1"]),
            _make_anchor("旧城区", "旧城区", kind="location", action_ids=["A1"]),
            _make_anchor("见过", "见过的", kind="past_reference", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="检查门锁",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert len(result.candidates) >= 1
        lock_memory = [c for c in result.candidates if c.subject == "旧式仓库锁"]
        assert len(lock_memory) == 1
        assert lock_memory[0].score > 0

        lock_index = next(i for i, c in enumerate(result.candidates) if c.subject == "旧式仓库锁")
        habit_index = next((i for i, c in enumerate(result.candidates) if c.subject == "主角习惯"), len(result.candidates))
        assert lock_index < habit_index


# ══════════════════════════════════════════════════════════════════
#  Test: 避免单实体污染 (Avoid Single-Entity Pollution)
# ══════════════════════════════════════════════════════════════════

class TestAvoidEntityPollution:
    def test_signal_ranks_above_other_traits(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="trait",
                subject="伊莱娜喜欢喝红茶",
                content="伊莱娜喜欢喝红茶，每天下午都会泡一杯。",
                entities=["伊莱娜"],
                tags=["喜好"],
                salience=0.3,
                certainty=1.0,
            ),
            _make_memory(
                type="trait",
                subject="伊莱娜左耳听力不佳",
                content="伊莱娜左耳听力不佳，需要从右边和她说话。",
                entities=["伊莱娜"],
                tags=["弱点", "身体"],
                salience=0.5,
                certainty=0.9,
            ),
            _make_memory(
                type="agreement",
                subject="沉默暗号约定",
                content="主角和伊莱娜约定沉默暗号，左手轻敲两下表示保持安静。",
                entities=["主角", "伊莱娜"],
                tags=["暗号", "约定"],
                salience=0.8,
                certainty=0.9,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="signal_ally",
            raw_text="我用和伊莱娜约好的暗号",
            verb="发信号",
            target="伊莱娜",
            method="暗号",
        )

        anchors = [
            _make_anchor("伊莱娜", "伊莱娜", kind="character", action_ids=["A1"]),
            _make_anchor("暗号", "暗号", kind="concept", action_ids=["A1"]),
            _make_anchor("约好的", "约定的", kind="past_reference", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="用暗号提醒伊莱娜",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        signal_score = next(c.score for c in result.candidates if c.subject == "沉默暗号约定")
        hearing_score = next(c.score for c in result.candidates if c.subject == "伊莱娜左耳听力不佳")
        tea_score = next(c.score for c in result.candidates if c.subject == "伊莱娜喜欢喝红茶")

        assert signal_score > hearing_score
        assert signal_score > tea_score


# ══════════════════════════════════════════════════════════════════
#  Test: past_reference 单 anchor 不应产生高分
# ══════════════════════════════════════════════════════════════════

class TestPastReferenceAlone:
    def test_past_reference_only_produces_low_scores(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="agreement",
                subject="沉默暗号约定",
                content="主角和伊莱娜约定沉默暗号，左手轻敲两下表示保持安静。",
                entities=["主角", "伊莱娜"],
                tags=["暗号", "约定"],
                salience=0.8,
                certainty=0.9,
            ),
            _make_memory(
                type="event",
                subject="旧城区大火",
                content="旧城区在十年前经历了一场大火。",
                entities=["旧城区"],
                tags=["历史", "火灾"],
                salience=0.4,
                certainty=0.9,
            ),
            _make_memory(
                type="fact",
                subject="码头的地形",
                content="码头仓库区有三个主要入口。",
                entities=["主角"],
                tags=["地形"],
                salience=0.3,
                certainty=1.0,
            ),
            _make_memory(
                type="trait",
                subject="伊莱娜喜欢红茶",
                content="伊莱娜喜欢喝红茶。",
                entities=["伊莱娜"],
                tags=["喜好"],
                salience=0.3,
                certainty=1.0,
            ),
            _make_memory(
                type="item",
                subject="鹰徽章",
                content="一枚烧焦的鹰徽章，边缘已经融化变形。",
                entities=["主角"],
                tags=["物品", "证据"],
                salience=0.5,
                certainty=0.8,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="other",
            raw_text="我照老办法行动",
            verb="行动",
            target=None,
            method="老办法",
        )

        anchors = [
            _make_anchor("老办法", "老办法", kind="past_reference", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="照老办法行动",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert result.total_returned <= 3
        for c in result.candidates:
            assert c.score < 10.0


# ══════════════════════════════════════════════════════════════════
#  Test: item 相关行动 (Item-Related Actions)
# ══════════════════════════════════════════════════════════════════

class TestItemAction:
    def test_eagle_badge_ranks_first(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="item",
                subject="烧焦的鹰徽章",
                content="一枚烧焦的鹰徽章，边缘已经融化变形，背面隐约可见一个家族纹章。",
                entities=["主角"],
                tags=["物品", "证据", "鹰徽章"],
                salience=0.7,
                certainty=0.9,
            ),
            _make_memory(
                type="fact",
                subject="鹰家族历史",
                content="鹰家族是旧城区的古老贵族之一。",
                entities=["鹰家族"],
                tags=["历史", "贵族"],
                salience=0.4,
                certainty=0.7,
            ),
            _make_memory(
                type="event",
                subject="找到徽章",
                content="主角在废墟中发现了一枚烧焦的徽章。",
                entities=["主角"],
                tags=["发现", "徽章"],
                salience=0.5,
                certainty=0.8,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="inspect_object",
            raw_text="我检查那枚烧焦的鹰徽章",
            verb="检查",
            target="鹰徽章",
            method="仔细观察",
            goal="确认徽章来源",
        )

        anchors = [
            _make_anchor("鹰徽章", "鹰徽章", kind="item", action_ids=["A1"]),
            _make_anchor("烧焦", "烧焦", kind="concept", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="检查鹰徽章",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert len(result.candidates) >= 1
        assert result.candidates[0].subject == "烧焦的鹰徽章"
        assert result.candidates[0].score > 0

        badge_score = result.candidates[0].score
        other_scores = [c.score for c in result.candidates if c.subject != "烧焦的鹰徽章"]
        for s in other_scores:
            assert badge_score > s


# ══════════════════════════════════════════════════════════════════
#  Test: condition 不能单独召回 (Condition Should Not Recall Alone)
# ══════════════════════════════════════════════════════════════════

class TestConditionNotRecalling:
    def test_condition_anchor_does_not_pull_many_memories(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="trait",
                subject="主角谨慎",
                content="主角是一个十分谨慎的人，总是留意周围的目光。",
                entities=["主角"],
                tags=["性格", "谨慎"],
                salience=0.5,
                certainty=0.8,
            ),
            _make_memory(
                type="fact",
                subject="码头仓库布局",
                content="码头仓库区有三个主要通道和两个紧急出口。",
                entities=["主角"],
                tags=["地形", "仓库"],
                salience=0.4,
                certainty=0.9,
            ),
            _make_memory(
                type="trait",
                subject="卫兵巡逻习惯",
                content="码头仓库区的卫兵每十五分钟巡逻一次。",
                entities=["卫兵"],
                tags=["巡逻", "规律"],
                salience=0.5,
                certainty=0.8,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="move",
            raw_text="如果没人注意，我就继续往前走",
            verb="继续走",
            target=None,
            condition="没人注意",
        )

        anchors = [
            _make_anchor("没人注意", "没人注意", kind="condition", action_ids=["A1"]),
            _make_anchor("继续往前走", "继续往前走", kind="action_keyword", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="继续前进",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert result.total_returned <= 1


# ══════════════════════════════════════════════════════════════════
#  Test: Score Breakdown
# ══════════════════════════════════════════════════════════════════

class TestScoreBreakdown:
    def test_breakdown_has_all_fields(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="agreement",
                subject="沉默暗号约定",
                content="主角和伊莱娜约定沉默暗号。",
                entities=["主角", "伊莱娜"],
                tags=["暗号", "约定"],
                salience=0.8,
                certainty=0.9,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="signal_ally",
            raw_text="我用之前和伊莱娜约好的暗号提醒她",
            verb="发信号",
        )

        anchors = [
            _make_anchor("伊莱娜", "伊莱娜", kind="character", action_ids=["A1"]),
            _make_anchor("暗号", "暗号", kind="concept", action_ids=["A1"]),
            _make_anchor("之前", "之前", kind="past_reference", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="提醒伊莱娜",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert len(result.candidates) == 1
        breakdown = result.candidates[0].score_breakdown

        required_fields = [
            "entity_score", "entity_rank",
            "subject_tag_score", "subject_tag_rank",
            "content_score", "content_rank",
            "type_intent_score", "type_intent_rank",
            "scene_context_score", "scene_context_rank",
            "rrf_score", "total_memories",
        ]
        for field in required_fields:
            assert field in breakdown, f"Missing field: {field}"

        assert result.candidates[0].score > 0

    def test_matched_anchors_tracked(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="agreement",
                subject="沉默暗号约定",
                content="主角和伊莱娜约定沉默暗号。",
                entities=["主角", "伊莱娜"],
                tags=["暗号"],
                salience=0.8,
                certainty=0.9,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="signal_ally",
            raw_text="我用暗号提醒伊莱娜",
            verb="发信号",
        )

        anchors = [
            _make_anchor("伊莱娜", "伊莱娜", kind="character", action_ids=["A1"]),
            _make_anchor("暗号", "暗号", kind="concept", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="提醒伊莱娜",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert len(result.candidates) == 1
        assert len(result.candidates[0].matched_anchors) >= 2
        assert "伊莱娜" in result.candidates[0].matched_anchors
        assert "暗号" in result.candidates[0].matched_anchors


# ══════════════════════════════════════════════════════════════════
#  Test: 空 store / 不匹配
# ══════════════════════════════════════════════════════════════════

class TestEmptyAndNoMatch:
    def test_empty_store_returns_empty(self):
        store = MemoryStore(":memory:")
        parse_result = _make_parse_result()

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert result.total_scanned == 0
        assert result.total_returned == 0
        assert len(result.candidates) == 0

    def test_no_matching_anchors_returns_empty(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="fact",
                subject="码头天气",
                content="码头今天的天气很好。",
                entities=["主角"],
                tags=["天气"],
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="move",
            raw_text="我向前走",
            verb="走",
        )

        anchors = [
            _make_anchor("向前走", "向前走", kind="action_keyword", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="移动",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert result.total_returned == 0


# ══════════════════════════════════════════════════════════════════
#  Test: action_proximity_boost
# ══════════════════════════════════════════════════════════════════

class TestActionProximityBoost:
    def test_multi_anchor_match_boosts_score(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_memory(
                type="agreement",
                subject="沉默暗号",
                content="主角与伊莱娜约定左手轻敲两下表示保持沉默。",
                entities=["主角", "伊莱娜"],
                tags=["暗号", "沉默", "约定"],
                salience=0.8,
                certainty=0.9,
            ),
            _make_memory(
                type="trait",
                subject="伊莱娜性格",
                content="伊莱娜性格冷静。",
                entities=["伊莱娜"],
                tags=["性格"],
                salience=0.5,
            ),
        ])

        action = _make_action(
            action_id="A1",
            action_type="signal_ally",
            raw_text="用暗号提醒伊莱娜",
            verb="发信号",
        )

        anchors = [
            _make_anchor("伊莱娜", "伊莱娜", kind="character", action_ids=["A1"]),
            _make_anchor("暗号", "暗号", kind="concept", action_ids=["A1"]),
            _make_anchor("沉默", "沉默", kind="action_keyword", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="提醒伊莱娜",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result)

        assert len(result.candidates) >= 1
        signal_candidate = [c for c in result.candidates if c.subject == "沉默暗号"]
        assert len(signal_candidate) == 1
        assert signal_candidate[0].score_breakdown["entity_score"] > 0
        assert signal_candidate[0].score_breakdown["subject_tag_score"] > 0


# ══════════════════════════════════════════════════════════════════
#  Test: top_k 限制
# ══════════════════════════════════════════════════════════════════

class TestTopK:
    def test_top_k_limits_results(self):
        store = MemoryStore(":memory:")
        for i in range(25):
            store.save_many([
                _make_memory(
                    type="fact",
                    subject=f"记忆{i}",
                    content=f"伊莱娜在第{i}天做了一件事。",
                    entities=["伊莱娜"],
                    tags=[f"tag{i}"],
                    salience=0.5,
                    certainty=0.8,
                ),
            ])

        action = _make_action(
            action_id="A1",
            action_type="observe",
            raw_text="我观察伊莱娜的行动",
            verb="观察",
            target="伊莱娜",
        )

        anchors = [
            _make_anchor("伊莱娜", "伊莱娜", kind="character", action_ids=["A1"]),
        ]

        parse_result = _make_parse_result(
            turn_intent="观察伊莱娜",
            actions=[action],
            anchors=anchors,
        )

        retriever = CandidateRetriever(store)
        result = retriever.retrieve(parse_result, top_k=10)

        assert result.total_scanned == 25
        assert result.total_returned <= 10
        assert len(result.candidates) <= 10
