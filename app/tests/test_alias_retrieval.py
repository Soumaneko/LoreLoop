import pytest

from app.domain.action.schemas import ActionParseResult, Anchor, AtomicAction
from app.domain.recall_memory.schemas import Memory
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.alias_store import AliasStore
from app.domain.retrieval.candidate_retriever import CandidateRetriever


def _make_action(action_id="A1", action_type="observe", raw_text="测试", **overrides):
    defaults = {
        "action_id": action_id, "sequence": 1, "actor": "主角",
        "action_type": action_type, "raw_text": raw_text, "verb": raw_text[:4],
        "likely_needs_roll": False, "confidence": 0.9,
    }
    defaults.update(overrides)
    return AtomicAction(**defaults)


def _make_anchor(text, normalized=None, kind="character", **overrides):
    defaults = {
        "text": text, "normalized": normalized or text, "kind": kind,
        "source": "explicit", "confidence": 0.9,
    }
    defaults.update(overrides)
    return Anchor(**defaults)


def _make_mem(**overrides):
    defaults = {
        "memory_kind": "relationship_state", "evidence_mode": "inferred", "subject": "测试", "content": "测试内容",
        "entities": ["主角"], "confidence": 1.0, "salience": 0.5,
    }
    defaults.update(overrides)
    return Memory(**defaults)


class TestAliasStore:
    def test_expand_with_aliases(self):
        store = AliasStore(":memory:")
        store.add_alias("莎拉", "莎拉·月刃", "character", 1.0)
        store.add_alias("莎拉·月刃", "莎拉·月刃", "character", 1.0)
        store.add_alias("暗夜精灵女性", "莎拉·月刃", "character", 0.8)
        store.add_alias("暗夜精灵", "莎拉·月刃", "character", 0.8)

        result = store.expand("莎拉")
        terms = [t for t, _ in result]
        assert "莎拉" in terms
        assert "莎拉·月刃" in terms
        assert "暗夜精灵女性" in terms
        assert result[0][1] >= result[-1][1]  # sorted by confidence desc

    def test_expand_no_alias_returns_self(self):
        store = AliasStore(":memory:")
        result = store.expand("狗头人")
        assert result == [("狗头人", 1.0)]

    def test_resolve_returns_canonical(self):
        store = AliasStore(":memory:")
        store.add_alias("莎拉", "莎拉·月刃", "character", 1.0)
        assert store.resolve("莎拉") == "莎拉·月刃"
        assert store.resolve("狗头人") == "狗头人"


class TestAliasRetrieval:
    def test_retriever_closes_only_owned_alias_store(self, monkeypatch):
        class _FakeAliasStore:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        owned = _FakeAliasStore()
        monkeypatch.setattr(
            CandidateRetriever,
            "_load_default_alias_store",
            staticmethod(lambda: owned),
        )

        with CandidateRetriever(MemoryStore(":memory:")):
            pass

        assert owned.closed is True

        injected = _FakeAliasStore()
        with CandidateRetriever(
            MemoryStore(":memory:"),
            alias_store=injected,
        ):
            pass

        assert injected.closed is False

    def test_alias_entity_match_passes_gate(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_mem(
                memory_kind="relationship_state", evidence_mode="inferred", subject="暗夜精灵女性", content="暗夜精灵女性擅长近战",
                entities=["暗夜精灵女性"], tags=["战斗"], salience=0.5, confidence=0.8,
            ),
            _make_mem(
                memory_kind="open_thread", evidence_mode="observed", subject="无关记忆", content="今天天气很好",
                entities=["主角"], tags=["天气"], salience=0.3, confidence=1.0,
            ),
        ])

        alias_store = AliasStore(":memory:")
        alias_store.add_alias("莎拉", "莎拉·月刃", "character", 1.0)
        alias_store.add_alias("暗夜精灵女性", "莎拉·月刃", "character", 0.8)

        action = _make_action(raw_text="检查莎拉")
        anchors = [_make_anchor("莎拉", kind="character")]

        parse_result = ActionParseResult(
            turn_intent="测试", atomic_actions=[action], anchors=anchors
        )

        retriever = CandidateRetriever(store, alias_store=alias_store)
        result = retriever.retrieve(parse_result)

        assert len(result.candidates) >= 1
        assert result.candidates[0].subject == "暗夜精灵女性"

    def test_alias_confidence_penalizes_score(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_mem(
                memory_kind="relationship_state", evidence_mode="inferred", subject="莎拉·月刃技能", content="莎拉·月刃是近战专家",
                entities=["莎拉·月刃"], tags=["战斗"], salience=0.6, confidence=0.9,
            ),
            _make_mem(
                memory_kind="relationship_state", evidence_mode="inferred", subject="暗夜精灵女性", content="暗夜精灵女性擅长近战",
                entities=["暗夜精灵女性"], tags=["战斗"], salience=0.5, confidence=0.8,
            ),
        ])

        alias_store = AliasStore(":memory:")
        alias_store.add_alias("莎拉·月刃", "莎拉·月刃", "character", 1.0)
        alias_store.add_alias("暗夜精灵女性", "莎拉·月刃", "character", 0.8)

        action = _make_action(raw_text="检查莎拉")
        anchors = [_make_anchor("莎拉·月刃", kind="character")]

        parse_result = ActionParseResult(
            turn_intent="测试", atomic_actions=[action], anchors=anchors
        )

        retriever = CandidateRetriever(store, alias_store=alias_store)
        result = retriever.retrieve(parse_result)

        direct = [c for c in result.candidates if c.subject == "莎拉·月刃技能"]
        alias = [c for c in result.candidates if c.subject == "暗夜精灵女性"]
        assert len(direct) == 1
        assert len(alias) == 1
        assert direct[0].score > alias[0].score

    def test_no_alias_match_works_normally(self):
        store = MemoryStore(":memory:")
        store.save_many([
            _make_mem(
                memory_kind="relationship_state", evidence_mode="inferred", subject="狗头人特征", content="狗头人身材矮小",
                entities=["狗头人"], tags=["种族"], salience=0.5, confidence=0.9,
            ),
        ])

        alias_store = AliasStore(":memory:")
        action = _make_action(raw_text="观察狗头人")
        anchors = [_make_anchor("狗头人", kind="character")]

        parse_result = ActionParseResult(
            turn_intent="测试", atomic_actions=[action], anchors=anchors
        )

        retriever = CandidateRetriever(store, alias_store=alias_store)
        result = retriever.retrieve(parse_result)

        assert len(result.candidates) >= 1
        assert result.candidates[0].subject == "狗头人特征"
