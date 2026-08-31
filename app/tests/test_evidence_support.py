from app.runtime.stores import open_confirmed_history_store
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




class TestRelationAdmissionMerge:
    def _admission(self, **overrides):
        from app.domain.context_facts.relation_admission import RelationAdmission

        return RelationAdmission(**overrides)







@contextmanager
def _scene_store(scene_text, user_input=""):
    with open_confirmed_history_store() as store:
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
