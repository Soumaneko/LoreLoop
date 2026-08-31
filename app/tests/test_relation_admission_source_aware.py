from app.tests.factories.stores import open_confirmed_history_store
import pytest
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal

from app.domain.confirmed_history import (
    ConfirmedHistoryStore,
    history_document_text_hash,
)
from app.domain.continuity.schemas import (
    ContinuityHistoryLink,
    ContinuityOperationType,
)
from app.domain.context_facts.diversity import (
    AdmissionPreview,
    HistoryDiversityPolicy,
    SelectionProposal,
    select_diverse_candidates,
)
from app.domain.context_facts.relation_admission import (
    ContinuityAttribution,
    NPCAttribution,
    RelationAdmission,
    RelationEvidenceLink,
    continuity_link_to_evidence,
    judge_relation_links,
    npc_field_link_to_evidence,
)
from app.domain.context_facts.retriever import ContextHistoryRetriever
from app.domain.context_facts.schemas import (
    ContextSearchEntity,
    ContextSearchIntent,
    ContextSearchPlan,
)
from app.domain.npc_profile.field_history import NPCFieldHistoryName
from app.domain.npc_profile.history_bridge import NPCFieldHistoryLink


@dataclass
class _FakeCandidate:
    document: Any
    matched_intent_ids: set[str]
    intent_matched_entities: dict[str, set[str]] = field(default_factory=dict)
    relation_admission: RelationAdmission | None = None


def _npc_link(
    *,
    npc_id="npc-a",
    field_name: NPCFieldHistoryName = "role",
    log_id=7,
    turn_index=1,
    evidence_id="e1",
    evidence_hash=None,
    intents=("i1",),
    reasons=("npc_name_match",),
    intent_match_reasons=None,
    basis=None,
    basis_complete=True,
    position=0,
) -> NPCFieldHistoryLink:
    return NPCFieldHistoryLink(
        npc_id=npc_id,
        field_name=field_name,
        log_id=log_id,
        turn_index=turn_index,
        evidence_id=evidence_id,
        evidence_hash=evidence_hash,
        evidence_position=position,
        triggering_intent_ids=list(intents),
        match_reasons=list(reasons),
        intent_match_reasons=intent_match_reasons or {},
        evidence_basis_primary=basis,
        basis_complete=basis_complete,
    )


def _continuity_link(
    *,
    item_id="item",
    operation: ContinuityOperationType = "create",
    evidence_id="e1",
    evidence_hash=None,
    intents=("i1",),
    reasons=("exact_entity_match",),
    basis=None,
    basis_complete=True,
) -> ContinuityHistoryLink:
    return ContinuityHistoryLink(
        continuity_id=item_id,
        continuity_status="active",
        operation=operation,
        evidence_id=evidence_id,
        evidence_hash=evidence_hash,
        triggering_intent_ids=list(intents),
        match_reasons=list(reasons),
        evidence_position=0,
        evidence_basis_primary=basis,
        basis_complete=basis_complete,
    )


class TestAdapters:
    def test_continuity_adapter_normalizes_source_aware_fields(self):
        link = _continuity_link(
            item_id="item-1",
            operation="update",
            evidence_id="turn_1_p001",
            evidence_hash="h" * 64,
            basis="正文依据",
        )
        evidence = continuity_link_to_evidence(link)

        assert evidence.source == "continuity"
        assert evidence.node_key == "item-1:update"
        assert evidence.owner_key == "continuity:item-1"
        assert evidence.continuity == ContinuityAttribution(
            continuity_id="item-1",
            operation="update",
        )
        assert evidence.npc is None
        assert evidence.evidence_position == 0







    def _npc_evidence_link(
        self,
        *,
        intents=("a",),
        match_reasons=frozenset({"npc_name_match"}),
        intent_match_reasons=None,
    ):
        return RelationEvidenceLink(
            source="npc_profile",
            node_key="n",
            owner_key="o",
            evidence_id="e",
            evidence_hash=None,
            evidence_position=0,
            triggering_intent_ids=tuple(intents),
            match_reasons=frozenset(match_reasons),
            intent_match_reasons=intent_match_reasons or {},
            evidence_basis_primary=None,
            evidence_basis_context=None,
            basis_complete=True,
            npc=NPCAttribution(npc_id="npc-a", field_name="role", log_id=1),
        )








def _scene_context(scene_text, user_input=""):
    with open_confirmed_history_store() as store:
        store.upsert_turn(
            scene_id="scene_001",
            turn_index=1,
            user_input=user_input,
            scene_text=scene_text,
        )
        documents = store.list_documents()
        documents_by_id = {item.document_id: item for item in documents}
        by_turn: dict[int, list] = {1: documents}
        paragraph = next(
            item for item in documents if item.source_type == "scene_paragraph"
        )
        return documents_by_id, by_turn, paragraph


class TestJudgeSourceAware:
    def test_cross_source_shared_evidence_merges_provenance(self):
        text = "她接过铜印，正式成为北门巡检。"
        documents_by_id, by_turn, paragraph = _scene_context(text)
        continuity = continuity_link_to_evidence(_continuity_link(
            evidence_id=paragraph.document_id,
            evidence_hash=history_document_text_hash(paragraph),
            intents=("a",),
            basis=text,
        ))
        npc = npc_field_link_to_evidence(_npc_link(
            npc_id="npc-a",
            log_id=7,
            evidence_id=paragraph.document_id,
            evidence_hash=history_document_text_hash(paragraph),
            intents=("b",),
            basis="北门巡检",
        ))

        admissions, diagnostics = judge_relation_links(
            [continuity, npc],
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )

        admission = admissions[paragraph.document_id]
        assert admission.matched_continuity_ids == {"item"}
        assert admission.linked_operations == {"create"}
        assert admission.linked_npc_changes == {("npc-a", "role", 7)}
        assert admission.owner_keys == {
            "continuity:item",
            "npc_profile:npc-a",
        }
        assert admission.intent_owner_keys["a"] == {"continuity:item"}
        assert admission.intent_owner_keys["b"] == {"npc_profile:npc-a"}
        assert diagnostics.valid_links == 2
        assert diagnostics.npc_valid_links == 1








class TestSelectorOwnerSemantics:
    def _plan(self, intent_ids):
        return ContextSearchPlan(intents=[
            ContextSearchIntent(
                intent_id=intent_id,
                focus="entity",
                origin="explicit_input",
                entities=[ContextSearchEntity(name=f"E{intent_id}", aliases=[])],
                topics=[],
            )
            for intent_id in intent_ids
        ])

    def _candidate(self, doc_id, turn, *, matched=(), admission=None):
        document = SimpleNamespace(
            document_id=doc_id,
            turn_index=turn,
            source_type="scene_paragraph",
        )
        return _FakeCandidate(
            document=document,
            matched_intent_ids=set(matched),
            relation_admission=admission,
        )

    def _admission(self, owners, intent_owners, intents):
        return RelationAdmission(
            intent_quality={intent_id: 0.9 for intent_id in intents},
            intent_class={intent_id: "strong" for intent_id in intents},
            intent_degraded={intent_id: False for intent_id in intents},
            owner_keys=frozenset(owners),
            intent_owner_keys={
                intent_id: frozenset(intent_owners[intent_id])
                for intent_id in intents
            },
            intent_continuity_ids={
                intent_id: frozenset()
                for intent_id in intents
            },
            intent_operations={
                intent_id: frozenset()
                for intent_id in intents
            },
            intent_npc_changes={
                intent_id: frozenset()
                for intent_id in intents
            },
        )

    def _proposal(
        self,
        candidate,
        intent_id,
        channel_rank,
        *,
        route: Literal["direct", "relation"] = "relation",
    ) -> SelectionProposal:
        """Build a proposal carrying source-scoped fields derived from the
        candidate's admission, matching how the retriever constructs them."""
        admission = candidate.relation_admission or RelationAdmission()
        return SelectionProposal(
            candidate=candidate,
            owner_intent_id=intent_id,
            route=route,
            channel_rank=channel_rank,
            signal_rank=0,
            source_evidence_id=(
                candidate.document.document_id if route == "relation" else None
            ),
            scoped_quality=admission.intent_quality.get(intent_id, 0.0),
            scoped_class=admission.intent_class.get(intent_id),
            scoped_degraded=admission.intent_degraded.get(intent_id, False),
            scoped_match_reasons=admission.intent_match_reasons.get(
                intent_id, frozenset()
            ),
            scoped_owner_keys=admission.intent_owner_keys.get(
                intent_id, frozenset()
            ),
            scoped_npc_changes=admission.intent_npc_changes.get(
                intent_id, frozenset()
            ),
            scoped_continuity_ids=admission.intent_continuity_ids.get(
                intent_id, frozenset()
            ),
            scoped_operations=admission.intent_operations.get(
                intent_id, frozenset()
            ),
        )

    def _select(self, plan, proposals, policy):
        def preview(candidate):
            return AdmissionPreview(
                state=[candidate],
                payload_chars=200,
                visible_evidence_ids=frozenset(
                    proposal.source_evidence_id
                    for proposal in proposals
                    if (
                        proposal.candidate.document.document_id
                        == candidate.document.document_id
                        and proposal.source_evidence_id is not None
                    )
                )
                or frozenset({candidate.document.document_id}),
            )

        def commit(preview_state):
            pass

        result = select_diverse_candidates(
            plan,
            proposals=proposals,
            policy=policy,
            preview=preview,
            commit=commit,
        )
        return result.selected


    def test_npc_relation_owner_cap_limits_profile_independently(self):
        plan = self._plan(["b", "l"])
        b_docs = []
        for index in range(4):
            b_docs.append(self._candidate(
                f"b{index}",
                turn=index + 1,
                admission=self._admission(
                    ["npc_profile:npc-b"],
                    {"b": ["npc_profile:npc-b"]},
                    ["b"],
                ),
            ))
        l_doc = self._candidate(
            "l0",
            turn=9,
            admission=self._admission(
                ["npc_profile:npc-l"],
                {"l": ["npc_profile:npc-l"]},
                ["l"],
            ),
        )
        proposals = []
        for index, candidate in enumerate(b_docs):
            proposals.append(self._proposal(candidate, "b", index))
        proposals.append(self._proposal(l_doc, "l", 0))

        selected = self._select(
            plan,
            proposals,
            HistoryDiversityPolicy(
                max_hits=5,
                relation_owner_cap=3,
                intent_target=2,
            ),
        )

        selected_ids = {candidate.document.document_id for candidate in selected}
        assert "l0" in selected_ids
        b_selected = {doc_id for doc_id in selected_ids if doc_id.startswith("b")}
        assert len(b_selected) == 3




class TestRetrieverSeam:
    def _plan(self, name: str, intent_id: str = "q"):
        return ContextSearchPlan(intents=[
            ContextSearchIntent(
                intent_id=intent_id,
                focus="entity",
                origin="explicit_input",
                entities=[ContextSearchEntity(name=name, aliases=[])],
                topics=[],
            )
        ])


    def test_older_stronger_relation_beats_newer_weaker_relation(self):
        # issue 16 必测 4：较旧 strong relation 排在较新 weak（但仍 strong）
        # 之前；recency 只在关联质量相等后决胜。
        text_older = "她接过铜印，正式成为北门巡检。"
        text_newer = "她巡防北门，责任重大。"
        with open_confirmed_history_store() as store:
            store.upsert_turn(
                scene_id="scene_001",
                turn_index=1,
                user_input="",
                scene_text=text_older,
            )
            store.upsert_turn(
                scene_id="scene_005",
                turn_index=5,
                user_input="",
                scene_text=text_newer,
            )
            documents = store.list_documents()
            older = next(
                item
                for item in documents
                if item.source_type == "scene_paragraph" and item.turn_index == 1
            )
            newer = next(
                item
                for item in documents
                if item.source_type == "scene_paragraph" and item.turn_index == 5
            )
            older_link = _npc_link(
                npc_id="npc-x",
                log_id=1,
                turn_index=1,
                evidence_id=older.document_id,
                evidence_hash=history_document_text_hash(older),
                intents=("b",),
                basis="北门巡检",
            )
            newer_link = _npc_link(
                npc_id="npc-y",
                log_id=2,
                turn_index=5,
                evidence_id=newer.document_id,
                evidence_hash=history_document_text_hash(newer),
                intents=("b",),
                basis="北门巡检",
            )
            result = ContextHistoryRetriever(
                store,
                diversity_policy=HistoryDiversityPolicy(max_hits=1),
            ).retrieve(
                self._plan("顾青", intent_id="b"),
                current_turn_index=6,
                continuity_links=[],
                npc_field_links=[newer_link, older_link],
            )

        # 较旧节点 support 1.0（strong）、较新节点 ~0.33（strong）：质量优先
        # 于 recency，较旧者必须胜出（旧行为按 recency 会选较新者）。
        assert [hit.evidence_id for hit in result.hits] == [
            older.document_id
        ]







class TestIssue17Eligibility:
    """issue 17: eligibility belongs to (document, intent, route); selection of
    a document never extinguishes other independently valid proposals."""

    def _plan(self, intent_ids):
        return ContextSearchPlan(intents=[
            ContextSearchIntent(
                intent_id=intent_id,
                focus="entity",
                origin="explicit_input",
                entities=[ContextSearchEntity(name=f"E{intent_id}", aliases=[])],
                topics=[],
            )
            for intent_id in intent_ids
        ])

    def _candidate(self, doc_id, turn, *, matched=(), admission=None):
        document = SimpleNamespace(
            document_id=doc_id,
            turn_index=turn,
            source_type="scene_paragraph",
        )
        return _FakeCandidate(
            document=document,
            matched_intent_ids=set(matched),
            relation_admission=admission,
        )

    def _admission(self, intents, degraded=()):
        return RelationAdmission(
            intent_quality={intent_id: 0.9 for intent_id in intents},
            intent_class={
                intent_id: ("weak" if intent_id in degraded else "strong")
                for intent_id in intents
            },
            intent_degraded={
                intent_id: intent_id in degraded for intent_id in intents
            },
            owner_keys=frozenset({f"owner-{intent_id}" for intent_id in intents}),
            intent_owner_keys={
                intent_id: frozenset({f"owner-{intent_id}"})
                for intent_id in intents
            },
            intent_continuity_ids={
                intent_id: frozenset() for intent_id in intents
            },
            intent_operations={
                intent_id: frozenset() for intent_id in intents
            },
            intent_npc_changes={
                intent_id: frozenset() for intent_id in intents
            },
        )

    def _proposal(
        self,
        candidate,
        intent_id,
        channel_rank,
        *,
        route: Literal["direct", "relation"] = "relation",
    ) -> SelectionProposal:
        admission = candidate.relation_admission or RelationAdmission()
        return SelectionProposal(
            candidate=candidate,
            owner_intent_id=intent_id,
            route=route,
            channel_rank=channel_rank,
            signal_rank=0,
            source_evidence_id=(
                candidate.document.document_id if route == "relation" else None
            ),
            scoped_quality=admission.intent_quality.get(intent_id, 0.0),
            scoped_class=admission.intent_class.get(intent_id),
            scoped_degraded=admission.intent_degraded.get(intent_id, False),
            scoped_match_reasons=admission.intent_match_reasons.get(
                intent_id, frozenset()
            ),
            scoped_owner_keys=admission.intent_owner_keys.get(
                intent_id, frozenset()
            ),
            scoped_npc_changes=admission.intent_npc_changes.get(
                intent_id, frozenset()
            ),
            scoped_continuity_ids=admission.intent_continuity_ids.get(
                intent_id, frozenset()
            ),
            scoped_operations=admission.intent_operations.get(
                intent_id, frozenset()
            ),
        )

    def _select(self, plan, proposals, policy):
        def preview(candidate):
            return AdmissionPreview(
                state=[candidate],
                payload_chars=200,
                visible_evidence_ids=frozenset(
                    proposal.source_evidence_id
                    for proposal in proposals
                    if (
                        proposal.candidate.document.document_id
                        == candidate.document.document_id
                        and proposal.source_evidence_id is not None
                    )
                )
                or frozenset({candidate.document.document_id}),
            )

        def commit(preview_state):
            pass

        return select_diverse_candidates(
            plan,
            proposals=proposals,
            policy=policy,
            preview=preview,
            commit=commit,
        )

    def test_same_document_direct_and_relation_proposals_both_considered(self):
        # doc42 direct serves intent A; relation serves intent B. B must keep
        # its own proposal instead of being deleted by A's direct match.
        plan = self._plan(["A", "B"])
        doc = self._candidate(
            "doc42",
            turn=1,
            matched=("A",),
            admission=self._admission(["B"]),
        )
        result = self._select(plan, [
            self._proposal(doc, "A", 0, route="direct"),
            self._proposal(doc, "B", 0),
        ], HistoryDiversityPolicy(max_hits=10, intent_target=1))

        assert [item.document.document_id for item in result.selected] == ["doc42"]
        routes = {entry.proposal.owner_intent_id: entry.proposal.route
                  for entry in result.entries}
        assert routes == {"A": "direct"}
        # B was eligible but A's direct already owns the only seat; B is not
        # silently dropped as ineligible — it must surface as a considered
        # proposal (proposal-level, not document-level deletion).
        assert any(
            p.owner_intent_id == "B" and p.route == "relation"
            for p in [entry.proposal for entry in result.entries]
            + [aug.proposal for aug in result.augmentations]
        )







class TestIssue17EndToEnd:
    """issue 17 regressions through the real retriever with genuinely
    constructed scenarios (near-duplicate clusters, budget compression,
    multi-route provenance, owner-cap boundaries)."""

    def _plan(self, intent_ids):
        return ContextSearchPlan(intents=[
            ContextSearchIntent(
                intent_id=intent_id,
                focus="entity",
                origin="explicit_input",
                entities=[ContextSearchEntity(name=f"E{intent_id}", aliases=[])],
                topics=[],
            )
            for intent_id in intent_ids
        ])

    def _plan_entities(self, specs):
        return ContextSearchPlan(intents=[
            ContextSearchIntent(
                intent_id=intent_id,
                focus="entity",
                origin="explicit_input",
                entities=[ContextSearchEntity(name=name, aliases=[])],
                topics=[],
            )
            for intent_id, name in specs
        ])

    def _turn(self, store, turn_index, scene_text):
        store.upsert_turn(
            scene_id=f"scene_{turn_index:03d}",
            turn_index=turn_index,
            user_input="",
            scene_text=scene_text,
        )

    def _paragraphs(self, store, turn_index):
        return sorted(
            (
                item
                for item in store.list_documents()
                if item.source_type == "scene_paragraph"
                and item.turn_index == turn_index
            ),
            key=lambda item: item.paragraph_index,
        )

    def test_near_duplicate_cluster_cross_route_both_eligible(self):
        # Two adjacent paragraphs in the same turn/scene with identical
        # normalized text cluster into one representative. Both members match
        # the direct entity (same entity set, so they merge); the relation
        # attribution from member B must survive on the representative output.
        base = "艾琳与那女人约定在青岩镇以铜牌接头"
        with open_confirmed_history_store() as store:
            store.upsert_turn(
                scene_id="scene_001", turn_index=1, user_input="",
                # Only punctuation differs (no negation tokens), so normalized
                # text is identical and the two adjacent paragraphs cluster.
                scene_text=f"{base}。\n\n{base}！",
            )
            paragraphs = self._paragraphs(store, 1)
            assert len(paragraphs) >= 2
            member_b = paragraphs[1]
            plan = self._plan_entities([("A", "艾琳")])
            link = ContinuityHistoryLink(
                continuity_id="agreement",
                continuity_status="active",
                operation="create",
                evidence_id=member_b.document_id,
                evidence_hash=history_document_text_hash(member_b),
                triggering_intent_ids=["A"],
                match_reasons=["exact_entity_match"],
                evidence_position=0,
                evidence_basis_primary="约定在青岩镇以铜牌接头。",
                basis_complete=True,
            )
            result = ContextHistoryRetriever(store).retrieve(
                plan,
                current_turn_index=2,
                continuity_links=[link],
            )
            # The representative carries the direct hit and, via augmentation,
            # the relation attribution from the cluster member.
            assert len(result.hits) >= 1
            assert any(hit.matched_intent_ids for hit in result.hits)
            assert any(
                "agreement" in hit.linked_continuity_ids
                for hit in result.hits
            )
            # The cluster really merged: the selected hit carries the other
            # member as supporting evidence, and augmentation happened.
            assert result.relation_augmented_on_selected_document == 1
            assert any(
                hit.supporting_evidence_ids for hit in result.hits
            )
