import pytest
from collections import defaultdict
from contextlib import contextmanager
from types import SimpleNamespace

from app.domain.confirmed_history import ConfirmedHistoryStore, history_document_text_hash
from app.domain.continuity.schemas import ContinuityHistoryLink
from app.domain.context_facts.evidence_support import (
    ZERO_SUPPORT,
    classify_link_support,
    link_window_text,
    support_score,
)
from app.domain.context_facts.relation_admission import (
    continuity_link_to_evidence,
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


class TestAdmissionAccumulator:
    def _link(self, intent_ids: list[str], *, basis_complete: bool = True):
        return continuity_link_to_evidence(ContinuityHistoryLink(
            continuity_id="item",
            continuity_status="active",
            operation="create",
            evidence_id="p1",
            evidence_hash=None,
            triggering_intent_ids=intent_ids,
            match_reasons=["exact_entity_match"],
            evidence_position=0,
            basis_complete=basis_complete,
        ))

    def _support(self, *, quality: float, support_class: str, degraded: bool):
        return SimpleNamespace(
            quality=quality,
            support_class=support_class,
            degraded=degraded,
        )

    def test_quality_tie_breaks_by_class_rank_not_input_order(self):
        from app.domain.context_facts.relation_admission import (
            _AdmissionAccumulator,
        )

        link = self._link(["i1"])

        def absorb(order):
            accumulator = _AdmissionAccumulator()
            for cls in order:
                accumulator.absorb(
                    link,
                    self._support(
                        quality=0.10,
                        support_class=cls,
                        degraded=True,
                    ),
                    0,
                )
            return accumulator.admission().evidence_class

        # Same inputs, both orders: CLASS_RANK must win, not insertion order.
        assert absorb(["incomplete", "strong"]) == "strong"
        assert absorb(["strong", "incomplete"]) == "strong"

    def test_degraded_is_and_across_serving_links(self):
        from app.domain.context_facts.relation_admission import (
            _AdmissionAccumulator,
        )

        link = self._link(["i1"])
        accumulator = _AdmissionAccumulator()
        accumulator.absorb(
            link,
            self._support(quality=0.9, support_class="strong", degraded=False),
            0,
        )
        accumulator.absorb(
            link,
            self._support(quality=0.9, support_class="weak", degraded=True),
            1,
        )
        assert accumulator.admission().degraded_for("i1") is False

    def test_first_order_is_per_intent(self):
        from app.domain.context_facts.relation_admission import (
            _AdmissionAccumulator,
        )

        accumulator = _AdmissionAccumulator()
        accumulator.absorb(
            self._link(["b"]),
            self._support(quality=0.5, support_class="strong", degraded=False),
            0,
        )
        accumulator.absorb(
            self._link(["a"]),
            self._support(quality=0.5, support_class="strong", degraded=False),
            10,
        )
        admission = accumulator.admission()
        assert admission.intent_first_order == {"b": 0, "a": 10}


class TestRelationAdmission:
    def test_strong_on_one_intent_does_not_lift_another(self):
        from app.domain.context_facts.relation_admission import RelationAdmission

        admission = RelationAdmission(
            intent_quality={"a": 0.9, "b": 0.1},
            intent_class={"a": "strong", "b": "weak"},
            intent_degraded={"a": False, "b": True},
        )
        assert admission.evidence_quality == 0.9
        assert admission.evidence_class == "strong"
        assert admission.degraded_for("a") is False
        assert admission.degraded_for("b") is True

    def test_document_class_tie_breaks_by_class_rank_across_intents(self):
        from app.domain.context_facts.relation_admission import RelationAdmission

        def build(order):
            return RelationAdmission(
                intent_quality={intent_id: 1.0 for intent_id, _ in order},
                intent_class={intent_id: cls for intent_id, cls in order},
                intent_degraded={
                    intent_id: cls != "strong"
                    for intent_id, cls in order
                },
            )

        order_a_first = [("a", "incomplete"), ("b", "strong")]
        order_b_first = [("b", "strong"), ("a", "incomplete")]

        assert build(order_a_first).evidence_class == "strong"
        assert build(order_b_first).evidence_class == "strong"

    def test_signal_rank_tiers(self):
        from app.domain.context_facts.relation_admission import RelationAdmission

        def admission(reasons):
            return RelationAdmission(
                match_reasons=frozenset(reasons),
                intent_match_reasons={"i": frozenset(reasons)},
            )

        assert admission({"two_entity_match"}).signal_rank_for("i") == 0
        assert admission({"exact_entity_match"}).signal_rank_for("i") == 2
        assert admission({"location_match"}).signal_rank_for("i") == 4
        assert admission(set()).signal_rank_for("i") == 6


class TestRelationAdmissionMerge:
    def _admission(self, **overrides):
        from app.domain.context_facts.relation_admission import RelationAdmission

        return RelationAdmission(**overrides)

    def test_strong_plus_weak_keeps_strong_class(self):
        from app.domain.context_facts.relation_admission import RelationAdmission

        strong = self._admission(
            intent_quality={"intent": 0.8},
            intent_class={"intent": "strong"},
            intent_degraded={"intent": False},
        )
        weak = self._admission(
            intent_quality={"intent": 0.1},
            intent_class={"intent": "weak"},
            intent_degraded={"intent": True},
        )
        merged = RelationAdmission.merge([strong, weak])
        assert merged.intent_quality == {"intent": 0.8}
        assert merged.intent_class == {"intent": "strong"}
        assert merged.degraded_for("intent") is False

    def test_direct_only_member_does_not_undegrade(self):
        from app.domain.context_facts.relation_admission import RelationAdmission

        relation = self._admission(
            intent_quality={"intent": 0.0},
            intent_class={"intent": "zero"},
            intent_degraded={"intent": True},
        )
        merged = RelationAdmission.merge([relation, None])
        assert merged.degraded_for("intent") is True

    def test_distinct_intents_merge_independently(self):
        from app.domain.context_facts.relation_admission import RelationAdmission

        strong_a = self._admission(
            intent_quality={"a": 0.9, "b": 0.0},
            intent_class={"a": "strong", "b": "zero"},
            intent_degraded={"a": False, "b": True},
        )
        weak_b = self._admission(
            intent_quality={"b": 0.1},
            intent_class={"b": "weak"},
            intent_degraded={"b": True},
        )
        merged = RelationAdmission.merge([strong_a, weak_b])
        assert merged.degraded_for("a") is False
        assert merged.degraded_for("b") is True
        assert merged.evidence_class == "strong"


class TestDegradedRescue:
    def test_owner_without_direct_coverage_can_rescue(self):
        from app.domain.context_facts.relation_admission import (
            degraded_rescue_allowed,
        )

        assert degraded_rescue_allowed("intent_b", {"intent_a"})

    def test_owner_with_direct_coverage_is_blocked(self):
        from app.domain.context_facts.relation_admission import (
            degraded_rescue_allowed,
        )

        assert not degraded_rescue_allowed("intent_a", {"intent_a"})

    def test_covered_other_intent_does_not_block_owner(self):
        from app.domain.context_facts.relation_admission import (
            degraded_rescue_allowed,
        )

        assert degraded_rescue_allowed("intent_b", {"intent_a"})
        assert not degraded_rescue_allowed("intent_a", {"intent_a"})


class TestPartitionServed:
    def test_strong_and_degraded_relation_split_independently(self):
        from app.domain.context_facts.relation_admission import partition_served

        normal, degraded = partition_served(
            direct_intents=set(),
            relation_intents={"a", "b"},
            degraded_relation_intents={"b"},
        )
        assert normal == {"a"}
        assert degraded == {"b"}

    def test_directly_served_degraded_intent_counts_as_normal(self):
        from app.domain.context_facts.relation_admission import partition_served

        normal, degraded = partition_served(
            direct_intents={"x"},
            relation_intents={"x"},
            degraded_relation_intents={"x"},
        )
        assert normal == {"x"}
        assert degraded == set()

    def test_direct_and_relation_mix(self):
        from app.domain.context_facts.relation_admission import partition_served

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
        from app.domain.context_facts.relation_admission import RelationAdmission

        incomplete = RelationAdmission(
            intent_quality={"intent": 0.10},
            intent_class={"intent": "incomplete"},
            intent_degraded={"intent": True},
        )
        weak = RelationAdmission(
            intent_quality={"intent": 0.10},
            intent_class={"intent": "weak"},
            intent_degraded={"intent": True},
        )
        # incomplete first, same quality: class rank must win over order.
        assert RelationAdmission.merge(
            [incomplete, weak]
        ).intent_class == {"intent": "weak"}
        assert RelationAdmission.merge(
            [weak, incomplete]
        ).intent_class == {"intent": "weak"}


@contextmanager
def _scene_store(scene_text, user_input=""):
    with ConfirmedHistoryStore() as store:
        store.upsert_turn(
            scene_id="scene_001",
            turn_index=1,
            user_input=user_input,
            scene_text=scene_text,
        )
        documents = store.list_documents()
        documents_by_id = {document.document_id: document for document in documents}
        by_turn: dict[int, list] = defaultdict(list)
        for document in documents:
            by_turn[document.turn_index].append(document)
        yield documents_by_id, by_turn


class TestJudgeRelationLinks:
    def _link(
        self,
        evidence_id,
        *,
        intents=("i1",),
        basis=None,
        basis_complete=True,
        evidence_hash=None,
    ):
        return continuity_link_to_evidence(ContinuityHistoryLink(
            continuity_id="item",
            continuity_status="active",
            operation="create",
            evidence_id=evidence_id,
            evidence_hash=evidence_hash,
            triggering_intent_ids=list(intents),
            match_reasons=["exact_entity_match"],
            evidence_position=0,
            evidence_basis_primary=basis,
            basis_complete=basis_complete,
        ))

    @staticmethod
    def _paragraph(documents_by_id):
        return next(
            document
            for document in documents_by_id.values()
            if document.source_type == "scene_paragraph"
        )

    def test_stale_unknown_document(self):
        from app.domain.context_facts.relation_admission import (
            judge_relation_links,
        )

        with _scene_store("阿九确认三日后在青岩镇以铜牌暗记接头。") as (
            documents_by_id,
            by_turn,
        ):
            admissions, diagnostics = judge_relation_links(
                [self._link("missing")],
                documents_by_id=documents_by_id,
                by_turn=by_turn,
            )
        assert admissions == {}
        assert diagnostics.valid_links == 0
        assert diagnostics.stale_links == 1

    def test_stale_wrong_source_type(self):
        from app.domain.context_facts.relation_admission import (
            judge_relation_links,
        )

        with _scene_store(
            "阿九确认三日后在青岩镇以铜牌暗记接头。",
            user_input="问艾琳关于黑塔的事。",
        ) as (documents_by_id, by_turn):
            input_id = next(
                document_id
                for document_id in documents_by_id
                if "input" in document_id
            )
            admissions, diagnostics = judge_relation_links(
                [self._link(input_id)],
                documents_by_id=documents_by_id,
                by_turn=by_turn,
            )
        assert admissions == {}
        assert diagnostics.stale_links == 1

    def test_stale_hash_mismatch(self):
        from app.domain.context_facts.relation_admission import (
            judge_relation_links,
        )

        with _scene_store("阿九确认三日后在青岩镇以铜牌暗记接头。") as (
            documents_by_id,
            by_turn,
        ):
            paragraph = self._paragraph(documents_by_id)
            admissions, diagnostics = judge_relation_links(
                [self._link(paragraph.document_id, evidence_hash="f" * 64)],
                documents_by_id=documents_by_id,
                by_turn=by_turn,
            )
        assert admissions == {}
        assert diagnostics.stale_links == 1

    def test_valid_strong_link_admitted(self):
        from app.domain.context_facts.relation_admission import (
            judge_relation_links,
        )

        with _scene_store("阿九确认三日后在青岩镇以铜牌暗记接头。") as (
            documents_by_id,
            by_turn,
        ):
            paragraph = self._paragraph(documents_by_id)
            admissions, diagnostics = judge_relation_links(
                [self._link(
                    paragraph.document_id,
                    basis="确认三日后在青岩镇以铜牌暗记接头。",
                    evidence_hash=history_document_text_hash(paragraph),
                )],
                documents_by_id=documents_by_id,
                by_turn=by_turn,
            )
        assert diagnostics.valid_links == 1
        assert diagnostics.invalid_links == 0
        assert diagnostics.degraded_links == 0
        admission = admissions[paragraph.document_id]
        assert admission.intent_class["i1"] == "strong"
        assert admission.degraded_for("i1") is False

    def test_complete_zero_suppressed(self):
        from app.domain.context_facts.relation_admission import (
            judge_relation_links,
        )

        with _scene_store("阿九确认三日后在青岩镇以铜牌暗记接头。") as (
            documents_by_id,
            by_turn,
        ):
            paragraph = self._paragraph(documents_by_id)
            admissions, diagnostics = judge_relation_links(
                [self._link(paragraph.document_id, basis="对岸灯火在薄雾里明灭。")],
                documents_by_id=documents_by_id,
                by_turn=by_turn,
            )
        assert diagnostics.valid_links == 1
        assert diagnostics.invalid_links == 1
        assert paragraph.document_id not in admissions

    def test_incomplete_zero_fails_open(self):
        from app.domain.context_facts.relation_admission import (
            judge_relation_links,
        )

        with _scene_store("阿九确认三日后在青岩镇以铜牌暗记接头。") as (
            documents_by_id,
            by_turn,
        ):
            paragraph = self._paragraph(documents_by_id)
            admissions, diagnostics = judge_relation_links(
                [self._link(
                    paragraph.document_id,
                    basis="对岸灯火在薄雾里明灭。",
                    basis_complete=False,
                )],
                documents_by_id=documents_by_id,
                by_turn=by_turn,
            )
        assert diagnostics.valid_links == 1
        assert diagnostics.invalid_links == 0
        assert diagnostics.degraded_links == 1
        admission = admissions[paragraph.document_id]
        assert admission.intent_class["i1"] == "incomplete"
        assert admission.degraded_for("i1") is True

    def test_degraded_is_and_across_links(self):
        from app.domain.context_facts.relation_admission import (
            judge_relation_links,
        )

        with _scene_store("阿九确认三日后在青岩镇以铜牌暗记接头。") as (
            documents_by_id,
            by_turn,
        ):
            paragraph = self._paragraph(documents_by_id)
            strong = self._link(
                paragraph.document_id,
                basis="确认三日后在青岩镇以铜牌暗记接头。",
            )
            incomplete = self._link(
                paragraph.document_id,
                basis="对岸灯火在薄雾里明灭。",
                basis_complete=False,
            )
            admissions, diagnostics = judge_relation_links(
                [strong, incomplete],
                documents_by_id=documents_by_id,
                by_turn=by_turn,
            )
        admission = admissions[paragraph.document_id]
        assert admission.intent_class["i1"] == "strong"
        assert admission.degraded_for("i1") is False
        assert diagnostics.degraded_links == 1

    def test_first_order_is_per_intent(self):
        from app.domain.context_facts.relation_admission import (
            judge_relation_links,
        )

        with _scene_store("阿九确认三日后在青岩镇以铜牌暗记接头。") as (
            documents_by_id,
            by_turn,
        ):
            paragraph = self._paragraph(documents_by_id)
            link_b = self._link(
                paragraph.document_id,
                intents=["b"],
                basis="确认三日后在青岩镇以铜牌暗记接头。",
            )
            link_a = self._link(
                paragraph.document_id,
                intents=["a"],
                basis="确认三日后在青岩镇以铜牌暗记接头。",
            )
            admissions, _ = judge_relation_links(
                [link_b, link_a],
                documents_by_id=documents_by_id,
                by_turn=by_turn,
            )
        assert admissions[paragraph.document_id].intent_first_order == {
            "b": 0,
            "a": 1,
        }

    def test_merge_keeps_per_intent_first_order(self):
        from app.domain.context_facts.relation_admission import RelationAdmission

        first = RelationAdmission(
            intent_quality={"a": 0.5, "b": 0.5},
            intent_class={"a": "strong", "b": "strong"},
            intent_degraded={"a": False, "b": False},
            intent_first_order={"a": 10, "b": 0},
        )
        second = RelationAdmission(
            intent_quality={"a": 0.5},
            intent_class={"a": "strong"},
            intent_degraded={"a": False},
            intent_first_order={"a": 5},
        )
        merged = RelationAdmission.merge([first, second])
        assert merged.intent_first_order == {"a": 5, "b": 0}
