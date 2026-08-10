import pytest

from app.domain.confirmed_history import ConfirmedHistoryStore
from app.domain.continuity.schemas import ContinuityHistoryLink
from app.domain.context_facts.evidence_support import (
    ZERO_SUPPORT,
    classify_link_support,
    link_window_text,
    support_score,
)


def _support(basis: str | None, window: str):
    return classify_link_support(
        basis_primary=basis,
        basis_context=None,
        basis_complete=True,
        window_text=window,
    )


class TestSupportScore:
    def test_exact_overlap_is_strong(self):
        support = _support(
            "约定三日后在青岩镇以铜牌接头。",
            "阿九确认三日后在青岩镇按刻痕接头。",
        )
        assert support.support_class == "strong"
        assert support.quality > 0.3

    def test_shared_two_char_term_counts(self):
        support = _support(
            "约定三日后在青岩镇以铜牌暗记接头。",
            "那女人点头应下，约定以铜牌为凭。",
        )
        assert support.quality > ZERO_SUPPORT
        assert support.support_class in {"weak", "strong"}

    def test_unrelated_atmosphere_is_zero(self):
        support = _support(
            "阿九确认三日后在青岩镇按刻痕接头。",
            "对岸灯火在薄雾里明灭。",
        )
        assert support.support_class == "zero"
        assert support.quality <= ZERO_SUPPORT

    def test_zero_band_not_math_zero(self):
        assert support_score(None, "任意正文") == 0.0
        assert support_score("", "任意正文") == 0.0


class TestClassifyLinkSupport:
    def test_class_ordering_is_monotonic(self):
        strong = _support("确认接头暗记与时间。", "在旧港确认了接头暗记与三日后时间。")
        weak = _support(
            "约定三日后在青岩镇以铜牌暗记接头。",
            "那女人点头应下，约定以铜牌为凭。",
        )
        zero = _support("确认接头暗记与时间。", "对岸灯火在薄雾里明灭。")
        ranks = {"strong": 3, "weak": 2, "zero": 1, "incomplete": 0}
        assert ranks[strong.support_class] > ranks[weak.support_class]
        assert ranks[weak.support_class] > ranks[zero.support_class]
        assert strong.quality > weak.quality > zero.quality

    def test_incomplete_basis_fails_open(self):
        support = classify_link_support(
            basis_primary=None,
            basis_context=None,
            basis_complete=False,
            window_text="任意正文。",
        )
        assert support.support_class == "incomplete"
        assert support.degraded

    def test_degraded_flag(self):
        assert _support("确认接头暗记与时间。", "确认了接头暗记。").degraded is False
        assert _support("确认接头暗记与时间。", "对岸灯火明灭。").degraded is True

    def test_context_backoff_is_capped(self):
        support = classify_link_support(
            basis_primary="确认可疑但暂不追查。",
            basis_context="林舟处理边境探子。",
            basis_complete=True,
            window_text="现有迹象表明他们很可能是外围探子。",
        )
        assert support.context_support > 0
        assert support.quality <= support.context_support
        assert support.primary_support == 0

    def test_context_exact_primary_zero_is_never_strong(self):
        from app.domain.context_facts.evidence_support import WEAK_SUPPORT

        support = classify_link_support(
            basis_primary="确认可疑但暂不追查。",
            basis_context="林舟处理边境探子。",
            basis_complete=True,
            window_text="林舟处理边境探子。",
        )
        assert support.primary_support == 0
        assert support.context_support > WEAK_SUPPORT
        assert support.support_class == "weak"
        assert support.quality <= WEAK_SUPPORT

    def test_strong_requires_primary_support(self):
        from app.domain.context_facts.evidence_support import WEAK_SUPPORT

        support = classify_link_support(
            basis_primary="确认接头暗记与时间。",
            basis_context="青岩镇接头。",
            basis_complete=True,
            window_text="确认接头暗记与时间，约定三日后在青岩镇以铜牌接头。",
        )
        assert support.primary_support > WEAK_SUPPORT
        assert support.support_class == "strong"


class TestLinkWindowText:
    def test_excludes_node_sibling_evidence(self):
        with ConfirmedHistoryStore() as store:
            store.upsert_turn(
                scene_id="scene_001",
                turn_index=1,
                user_input="",
                scene_text=(
                    "阿九确认三日后在青岩镇按刻痕接头。\n\n"
                    "那女人点头应下，约定以铜牌为凭。\n\n"
                    "对岸灯火在薄雾里明灭。"
                ),
            )
            by_turn = {1: store.list_documents()}
            anchor = next(
                d for d in by_turn[1] if d.paragraph_index == 1
            )
            middle = next(
                d for d in by_turn[1] if d.paragraph_index == 2
            )
            node_ids = {d.document_id for d in by_turn[1]}

            anchor_window = link_window_text(anchor, by_turn[1], node_ids)
            assert anchor.text in anchor_window
            assert "对岸灯火" not in anchor_window
            assert "点头应下" not in anchor_window

            middle_window = link_window_text(middle, by_turn[1], node_ids)
            assert "阿九确认三日后" not in middle_window
            assert "对岸灯火" not in middle_window

    def test_keeps_non_sibling_adjacent_context(self):
        with ConfirmedHistoryStore() as store:
            store.upsert_turn(
                scene_id="scene_001",
                turn_index=1,
                user_input="",
                scene_text="他点头应下。\n\n约定三日后在青岩镇以铜牌接头。",
            )
            documents = store.list_documents()
            anchor = next(d for d in documents if d.paragraph_index == 1)
            node_ids = {anchor.document_id}
            window_text = link_window_text(anchor, documents, node_ids)
            assert "约定三日后在青岩镇" in window_text


class TestSuppressAttribution:
    def _link(self, *, basis_complete: bool = True):
        return ContinuityHistoryLink(
            continuity_id="item",
            continuity_status="active",
            operation="create",
            evidence_id="p1",
            evidence_hash=None,
            triggering_intent_ids=["i1"],
            match_reasons=["exact_entity_match"],
            evidence_position=0,
            basis_complete=basis_complete,
        )

    def test_complete_zero_is_suppressed_unconditionally(self):
        from app.domain.context_facts.retriever import _suppress_attribution

        zero = _support("确认接头暗记与时间。", "对岸灯火在薄雾里明灭。")
        assert _suppress_attribution(self._link(), zero)

    def test_complete_zero_is_suppressed_even_when_single_evidence(self):
        from app.domain.context_facts.retriever import _suppress_attribution

        zero = _support("确认接头暗记与时间。", "对岸灯火在薄雾里明灭。")
        link = self._link()
        assert _suppress_attribution(link, zero)

    def test_incomplete_zero_fails_open(self):
        from app.domain.context_facts.retriever import _suppress_attribution
        from app.domain.context_facts.evidence_support import (
            classify_link_support,
        )

        support = classify_link_support(
            basis_primary="确认接头暗记与时间。",
            basis_context=None,
            basis_complete=False,
            window_text="对岸灯火在薄雾里明灭。",
        )
        assert support.support_class == "incomplete"
        link = self._link(basis_complete=False)
        assert not _suppress_attribution(link, support)

    def test_weak_or_strong_never_suppressed(self):
        from app.domain.context_facts.retriever import _suppress_attribution

        weak = _support("确认接头暗记与时间。", "确认了接头。")
        strong = _support("确认接头暗记与时间。", "在旧港确认了接头暗记与三日后时间。")
        link = self._link()
        assert not _suppress_attribution(link, weak)
        assert not _suppress_attribution(link, strong)


class TestCandidatePerIntent:
    def test_strong_on_one_intent_does_not_lift_another(self):
        from app.domain.context_facts.retriever import _Candidate

        candidate = _Candidate(document=object(), score=0.0)
        candidate.relation_intent_quality = {"a": 0.9, "b": 0.1}
        candidate.relation_intent_class = {"a": "strong", "b": "weak"}
        candidate.relation_intent_degraded = {"a": False, "b": True}

        assert candidate.relation_evidence_quality == 0.9
        assert candidate.relation_evidence_class == "strong"
        assert candidate.relation_intent_degraded["a"] is False
        assert candidate.relation_intent_degraded["b"] is True

    def test_degraded_is_and_across_serving_links(self):
        from app.domain.context_facts.retriever import _Candidate

        candidate = _Candidate(document=object(), score=0.0)
        candidate.relation_intent_degraded = {"intent": True}
        candidate.relation_intent_degraded["intent"] = (
            candidate.relation_intent_degraded["intent"] and True
        )
        assert candidate.relation_intent_degraded["intent"] is True
        candidate.relation_intent_degraded["intent"] = (
            candidate.relation_intent_degraded["intent"] and False
        )
        assert candidate.relation_intent_degraded["intent"] is False

    def test_document_class_tie_breaks_by_class_rank_across_intents(self):
        from app.domain.context_facts.retriever import _Candidate

        def build(order):
            return _Candidate(
                document=object(),
                score=0.0,
                relation_intent_quality={
                    intent_id: 1.0 for intent_id, _ in order
                },
                relation_intent_class={
                    intent_id: cls for intent_id, cls in order
                },
                relation_intent_degraded={
                    intent_id: cls != "strong"
                    for intent_id, cls in order
                },
            )

        order_a_first = [("a", "incomplete"), ("b", "strong")]
        order_b_first = [("b", "strong"), ("a", "incomplete")]

        assert build(order_a_first).relation_evidence_class == "strong"
        assert build(order_b_first).relation_evidence_class == "strong"


class TestMergedRepresentative:
    def _member(self, **overrides):
        from app.domain.context_facts.retriever import _Candidate

        return _Candidate(document=object(), score=0.0, **overrides)

    def test_strong_plus_weak_keeps_strong_class(self):
        from app.domain.context_facts.retriever import _merged_representative

        strong = self._member(
            relation_intent_quality={"intent": 0.8},
            relation_intent_class={"intent": "strong"},
            relation_intent_degraded={"intent": False},
        )
        weak = self._member(
            relation_intent_quality={"intent": 0.1},
            relation_intent_class={"intent": "weak"},
            relation_intent_degraded={"intent": True},
        )
        merged = _merged_representative([strong, weak])
        assert merged.relation_intent_quality == {"intent": 0.8}
        assert merged.relation_intent_class == {"intent": "strong"}
        assert merged.relation_intent_degraded == {"intent": False}

    def test_direct_only_member_does_not_undegrade(self):
        from app.domain.context_facts.retriever import _merged_representative

        relation = self._member(
            relation_intent_quality={"intent": 0.0},
            relation_intent_class={"intent": "zero"},
            relation_intent_degraded={"intent": True},
        )
        direct_only = self._member(matched_intent_ids={"intent"})
        merged = _merged_representative([relation, direct_only])
        assert merged.relation_intent_degraded == {"intent": True}

    def test_distinct_intents_merge_independently(self):
        from app.domain.context_facts.retriever import _merged_representative

        strong_a = self._member(
            relation_intent_quality={"a": 0.9, "b": 0.0},
            relation_intent_class={"a": "strong", "b": "zero"},
            relation_intent_degraded={"a": False, "b": True},
        )
        weak_b = self._member(
            relation_intent_quality={"b": 0.1},
            relation_intent_class={"b": "weak"},
            relation_intent_degraded={"b": True},
        )
        merged = _merged_representative([strong_a, weak_b])
        assert merged.relation_intent_degraded == {"a": False, "b": True}
        assert merged.relation_evidence_class == "strong"


class TestDegradedRescue:
    def test_owner_without_direct_coverage_can_rescue(self):
        from app.domain.context_facts.diversity import degraded_rescue_allowed

        assert degraded_rescue_allowed("intent_b", {"intent_a"})

    def test_owner_with_direct_coverage_is_blocked(self):
        from app.domain.context_facts.diversity import degraded_rescue_allowed

        assert not degraded_rescue_allowed("intent_a", {"intent_a"})

    def test_covered_other_intent_does_not_block_owner(self):
        from app.domain.context_facts.diversity import degraded_rescue_allowed

        assert degraded_rescue_allowed("intent_b", {"intent_a"})
        assert not degraded_rescue_allowed("intent_a", {"intent_a"})


class TestPartitionServed:
    def test_strong_and_degraded_relation_split_independently(self):
        from app.domain.context_facts.diversity import partition_served

        normal, degraded = partition_served(
            direct_intents=set(),
            relation_intents={"a", "b"},
            degraded_relation_intents={"b"},
        )
        assert normal == {"a"}
        assert degraded == {"b"}

    def test_directly_served_degraded_intent_counts_as_normal(self):
        from app.domain.context_facts.diversity import partition_served

        normal, degraded = partition_served(
            direct_intents={"x"},
            relation_intents={"x"},
            degraded_relation_intents={"x"},
        )
        assert normal == {"x"}
        assert degraded == set()

    def test_direct_and_relation_mix(self):
        from app.domain.context_facts.diversity import partition_served

        normal, degraded = partition_served(
            direct_intents={"a"},
            relation_intents={"a", "b", "c"},
            degraded_relation_intents={"b", "c"},
        )
        assert normal == {"a"}
        assert degraded == {"b", "c"}
        assert normal | degraded == {"a", "b", "c"}


class TestMergeClassTieBreak:
    def test_tie_breaks_by_class_rank_not_member_order(self):
        from app.domain.context_facts.retriever import _merge_intent_class
        from app.domain.context_facts.retriever import _Candidate

        incomplete = _Candidate(
            document=object(),
            score=0.0,
            relation_intent_quality={"intent": 0.10},
            relation_intent_class={"intent": "incomplete"},
            relation_intent_degraded={"intent": True},
        )
        weak = _Candidate(
            document=object(),
            score=0.0,
            relation_intent_quality={"intent": 0.10},
            relation_intent_class={"intent": "weak"},
            relation_intent_degraded={"intent": True},
        )
        # incomplete first, same quality: class rank must win over order.
        assert _merge_intent_class([incomplete, weak]) == {"intent": "weak"}
        assert _merge_intent_class([weak, incomplete]) == {"intent": "weak"}
