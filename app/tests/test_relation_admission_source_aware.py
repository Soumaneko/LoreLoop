import pytest
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

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

    def test_npc_adapter_normalizes_source_aware_fields(self):
        link = _npc_link(
            npc_id="npc-a",
            field_name="faction",
            log_id=9,
            evidence_id="turn_1_p001",
            evidence_hash="h" * 64,
            basis="白鹿堂",
        )
        evidence = npc_field_link_to_evidence(link)

        assert evidence.source == "npc_profile"
        assert evidence.node_key == "log:9"
        assert evidence.owner_key == "npc_profile:npc-a"
        assert evidence.evidence_turn_index == 1
        assert evidence.npc == NPCAttribution(
            npc_id="npc-a",
            field_name="faction",
            log_id=9,
        )
        assert evidence.continuity is None

    def test_link_rejects_missing_source_attribution(self):
        with pytest.raises(ValueError, match="continuity attribution"):
            RelationEvidenceLink(
                source="continuity",
                node_key="n",
                owner_key="o",
                evidence_id="e",
                evidence_hash=None,
                evidence_position=0,
                triggering_intent_ids=("a",),
                match_reasons=frozenset(),
                evidence_basis_primary=None,
                evidence_basis_context=None,
                basis_complete=True,
            )

    def test_link_rejects_wrong_source_attribution_pairing(self):
        with pytest.raises(ValueError, match="continuity source"):
            RelationEvidenceLink(
                source="continuity",
                node_key="n",
                owner_key="o",
                evidence_id="e",
                evidence_hash=None,
                evidence_position=0,
                triggering_intent_ids=("a",),
                match_reasons=frozenset(),
                evidence_basis_primary=None,
                evidence_basis_context=None,
                basis_complete=True,
                npc=NPCAttribution(npc_id="npc-a", field_name="role", log_id=1),
            )

    def test_link_rejects_non_bridgeable_npc_field(self):
        with pytest.raises(ValueError, match="non-bridgeable field"):
            RelationEvidenceLink(
                source="npc_profile",
                node_key="n",
                owner_key="o",
                evidence_id="e",
                evidence_hash=None,
                evidence_position=0,
                triggering_intent_ids=("a",),
                match_reasons=frozenset(),
                evidence_basis_primary=None,
                evidence_basis_context=None,
                basis_complete=True,
                npc=NPCAttribution(
                    npc_id="npc-a",
                    field_name="current_intent",
                    log_id=1,
                ),
            )

    def test_link_rejects_unknown_match_reason(self):
        with pytest.raises(ValueError, match="unknown relation match reasons"):
            RelationEvidenceLink(
                source="npc_profile",
                node_key="n",
                owner_key="o",
                evidence_id="e",
                evidence_hash=None,
                evidence_position=0,
                triggering_intent_ids=("a",),
                match_reasons=frozenset({"npc_mystery_match"}),
                evidence_basis_primary=None,
                evidence_basis_context=None,
                basis_complete=True,
                npc=NPCAttribution(npc_id="npc-a", field_name="role", log_id=1),
            )

    def test_npc_link_rejects_unknown_reason_vocabulary(self):
        with pytest.raises(ValueError, match="match_reasons"):
            _npc_link(reasons=("bogus_reason",))

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

    def test_link_rejects_partial_intent_match_reasons(self):
        with pytest.raises(
            ValueError,
            match="must cover exactly the triggering intents",
        ):
            self._npc_evidence_link(
                intents=("a", "b"),
                match_reasons=frozenset({
                    "npc_name_match",
                    "npc_value_topic_match",
                }),
                intent_match_reasons={"a": {"npc_name_match"}},
            )

    def test_link_rejects_empty_per_intent_reason_set(self):
        with pytest.raises(ValueError, match="must be non-empty"):
            self._npc_evidence_link(
                intents=("a", "b"),
                match_reasons=frozenset({"npc_name_match"}),
                intent_match_reasons={
                    "a": {"npc_name_match"},
                    "b": frozenset(),
                },
            )

    def test_link_rejects_intent_union_not_equal_to_global(self):
        with pytest.raises(ValueError, match="must equal match_reasons"):
            self._npc_evidence_link(
                intents=("a", "b"),
                match_reasons=frozenset({
                    "npc_name_match",
                    "npc_alias_match",
                }),
                intent_match_reasons={
                    "a": {"npc_name_match"},
                    "b": {"npc_value_topic_match"},
                },
            )

    def test_legacy_empty_intent_match_reasons_allowed(self):
        link = self._npc_evidence_link(
            intents=("a", "b"),
            match_reasons=frozenset({
                "npc_name_match",
                "npc_value_topic_match",
            }),
        )
        assert link.intent_match_reasons == {}

    def test_legacy_flat_link_falls_back_to_global_reasons(self):
        text = "她接过铜印，正式成为北门巡检。"
        documents_by_id, by_turn, paragraph = _scene_context(text)
        evidence_hash = history_document_text_hash(paragraph)
        npc = npc_field_link_to_evidence(_npc_link(
            npc_id="npc-a",
            log_id=7,
            evidence_id=paragraph.document_id,
            evidence_hash=evidence_hash,
            intents=("a", "b"),
            reasons=("npc_name_match", "npc_value_topic_match"),
            basis="北门巡检",
        ))

        admissions, _ = judge_relation_links(
            [npc],
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )

        admission = admissions[paragraph.document_id]
        assert admission.intent_match_reasons["a"] == {
            "npc_name_match",
            "npc_value_topic_match",
        }
        assert admission.intent_match_reasons["b"] == {
            "npc_name_match",
            "npc_value_topic_match",
        }

    def test_npc_reasons_fall_into_fixed_tiers(self):
        from app.domain.context_facts.relation_admission import RelationAdmission

        def admission(reasons):
            return RelationAdmission(
                match_reasons=frozenset(reasons),
                intent_match_reasons={"i": frozenset(reasons)},
            )

        assert admission({"npc_delta_topic_match"}).signal_rank_for("i") == 0
        assert admission({"npc_name_match"}).signal_rank_for("i") == 2
        assert admission({"npc_alias_match"}).signal_rank_for("i") == 2
        assert admission({"npc_value_topic_match"}).signal_rank_for("i") == 4


def _scene_context(scene_text, user_input=""):
    with ConfirmedHistoryStore() as store:
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

    def test_judge_counts_npc_stale_links_per_source(self):
        documents_by_id, by_turn, _ = _scene_context("正文。")
        npc = npc_field_link_to_evidence(_npc_link(evidence_id="missing"))

        admissions, diagnostics = judge_relation_links(
            [npc],
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )

        assert admissions == {}
        assert diagnostics.stale_links == 1
        assert diagnostics.npc_stale_links == 1
        assert diagnostics.valid_links == 0

    def test_judge_rejects_hash_matching_evidence_from_another_turn(self):
        text = "她接过铜印，正式成为北门巡检。"
        with ConfirmedHistoryStore() as store:
            store.upsert_turn(
                scene_id="scene_001",
                turn_index=1,
                user_input="",
                scene_text=text,
            )
            store.upsert_turn(
                scene_id="scene_002",
                turn_index=2,
                user_input="",
                scene_text=text,
            )
            documents = store.list_documents()
            documents_by_id = {item.document_id: item for item in documents}
            by_turn: dict[int, list] = {}
            for item in documents:
                by_turn.setdefault(item.turn_index, []).append(item)
        turn_two = next(
            item
            for item in documents
            if item.source_type == "scene_paragraph" and item.turn_index == 2
        )
        # Identical text means an identical hash, but the pointer claims turn 1.
        npc = npc_field_link_to_evidence(_npc_link(
            turn_index=1,
            evidence_id=turn_two.document_id,
            evidence_hash=history_document_text_hash(turn_two),
            basis=text,
        ))

        admissions, diagnostics = judge_relation_links(
            [npc],
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )

        assert turn_two.document_id not in admissions
        assert diagnostics.valid_links == 0
        assert diagnostics.stale_links == 1
        assert diagnostics.npc_stale_links == 1

    def test_complete_zero_suppression_applies_to_npc_links(self):
        text = "她接过铜印，正式成为北门巡检。"
        documents_by_id, by_turn, paragraph = _scene_context(text)
        npc = npc_field_link_to_evidence(_npc_link(
            evidence_id=paragraph.document_id,
            evidence_hash=history_document_text_hash(paragraph),
            basis="对岸灯火在薄雾里明灭。",
            basis_complete=True,
        ))

        admissions, diagnostics = judge_relation_links(
            [npc],
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )

        assert paragraph.document_id not in admissions
        assert diagnostics.valid_links == 1
        assert diagnostics.invalid_links == 1
        assert diagnostics.npc_invalid_links == 1

    def test_continuity_counters_unchanged_without_npc_links(self):
        text = "她接过铜印，正式成为北门巡检。"
        documents_by_id, by_turn, paragraph = _scene_context(text)
        continuity = continuity_link_to_evidence(_continuity_link(
            evidence_id=paragraph.document_id,
            evidence_hash=history_document_text_hash(paragraph),
            basis=text,
        ))

        admissions, diagnostics = judge_relation_links(
            [continuity],
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )

        assert paragraph.document_id in admissions
        assert diagnostics.valid_links == 1
        assert diagnostics.npc_valid_links == 0
        assert diagnostics.npc_stale_links == 0

    def test_signal_rank_is_scoped_per_intent_across_sources(self):
        text = "她接过铜印，正式成为北门巡检。"
        documents_by_id, by_turn, paragraph = _scene_context(text)
        evidence_hash = history_document_text_hash(paragraph)
        rank_zero = continuity_link_to_evidence(_continuity_link(
            evidence_id=paragraph.document_id,
            evidence_hash=evidence_hash,
            intents=("a",),
            reasons=("subject_topic_match",),
            basis=text,
        ))
        rank_four = npc_field_link_to_evidence(_npc_link(
            npc_id="npc-a",
            log_id=7,
            evidence_id=paragraph.document_id,
            evidence_hash=evidence_hash,
            intents=("b",),
            reasons=("npc_value_topic_match",),
            basis="北门巡检",
        ))

        admissions, _ = judge_relation_links(
            [rank_zero, rank_four],
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )

        admission = admissions[paragraph.document_id]
        assert admission.signal_rank_for("a") == 0
        assert admission.signal_rank_for("b") == 4

    def test_npc_per_intent_reasons_do_not_cross_inflate(self):
        text = "她接过铜印，正式成为北门巡检。"
        documents_by_id, by_turn, paragraph = _scene_context(text)
        evidence_hash = history_document_text_hash(paragraph)
        npc = npc_field_link_to_evidence(_npc_link(
            npc_id="npc-a",
            log_id=7,
            evidence_id=paragraph.document_id,
            evidence_hash=evidence_hash,
            intents=("a", "b"),
            reasons=("npc_name_match", "npc_value_topic_match"),
            intent_match_reasons={
                "a": ["npc_name_match"],
                "b": ["npc_value_topic_match"],
            },
            basis="北门巡检",
        ))

        admissions, _ = judge_relation_links(
            [npc],
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )

        admission = admissions[paragraph.document_id]
        assert admission.intent_match_reasons["a"] == {"npc_name_match"}
        assert admission.intent_match_reasons["b"] == {"npc_value_topic_match"}
        assert admission.signal_rank_for("a") == 2
        assert admission.signal_rank_for("b") == 4


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
        )

    def _select(self, plan, proposals, policy):
        def preview(candidate):
            return AdmissionPreview(state=[candidate], payload_chars=200)

        def commit(preview_state):
            pass

        return select_diverse_candidates(
            plan,
            proposals=proposals,
            policy=policy,
            preview=preview,
            commit=commit,
        )

    def test_policy_uses_relation_owner_cap_without_dual_name(self):
        policy = HistoryDiversityPolicy()
        assert policy.relation_owner_cap == 3
        assert not hasattr(policy, "continuity_item_cap")

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
            proposals.append(SelectionProposal(
                candidate=candidate,
                owner_intent_id="b",
                route="relation",
                channel_rank=index,
                signal_rank=0,
            ))
        proposals.append(SelectionProposal(
            candidate=l_doc,
            owner_intent_id="l",
            route="relation",
            channel_rank=0,
            signal_rank=0,
        ))

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

    def test_owner_at_cap_on_one_intent_does_not_block_another(self):
        plan = self._plan(["a", "b", "c"])
        shared = self._candidate(
            "shared",
            turn=1,
            admission=self._admission(
                ["continuity:x", "npc_profile:npc-b"],
                {"a": ["continuity:x"], "b": ["npc_profile:npc-b"]},
                ["a", "b"],
            ),
        )
        b_consumer = self._candidate(
            "b-consume",
            turn=2,
            admission=self._admission(
                ["npc_profile:npc-b"],
                {"a": ["npc_profile:npc-b"], "c": ["npc_profile:npc-b"]},
                ["a", "c"],
            ),
        )
        proposals = [
            SelectionProposal(
                candidate=b_consumer,
                owner_intent_id="a",
                route="relation",
                channel_rank=0,
                signal_rank=0,
            ),
            SelectionProposal(
                candidate=shared,
                owner_intent_id="a",
                route="relation",
                channel_rank=1,
                signal_rank=0,
            ),
            SelectionProposal(
                candidate=shared,
                owner_intent_id="b",
                route="relation",
                channel_rank=2,
                signal_rank=0,
            ),
        ]

        selected = self._select(
            plan,
            proposals,
            HistoryDiversityPolicy(
                max_hits=10,
                relation_owner_cap=1,
                intent_target=1,
            ),
        )

        selected_ids = {candidate.document.document_id for candidate in selected}
        assert "shared" in selected_ids
        assert "b-consume" in selected_ids

    def test_dual_route_direct_selection_does_not_consume_relation_owner_cap(
        self,
    ):
        plan = self._plan(["a"])
        direct_doc = self._candidate(
            "direct",
            turn=2,
            matched=("a",),
            admission=self._admission(
                ["npc_profile:npc-b"],
                {"a": ["npc_profile:npc-b"]},
                ["a"],
            ),
        )
        relation_doc = self._candidate(
            "relation",
            turn=1,
            admission=self._admission(
                ["npc_profile:npc-b"],
                {"a": ["npc_profile:npc-b"]},
                ["a"],
            ),
        )
        proposals = [
            SelectionProposal(
                candidate=direct_doc,
                owner_intent_id="a",
                route="direct",
                channel_rank=0,
                signal_rank=0,
            ),
            SelectionProposal(
                candidate=relation_doc,
                owner_intent_id="a",
                route="relation",
                channel_rank=0,
                signal_rank=0,
            ),
        ]

        selected = self._select(
            plan,
            proposals,
            HistoryDiversityPolicy(
                max_hits=10,
                relation_owner_cap=1,
                intent_target=1,
            ),
        )

        selected_ids = {candidate.document.document_id for candidate in selected}
        assert "direct" in selected_ids
        assert "relation" in selected_ids


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

    def test_retriever_npc_recency_is_scoped_to_the_proposal_intent(self):
        text_one = "她接过铜印，正式成为北门巡检。"
        text_three = "他换上新甲，改任南门守备。"
        with ConfirmedHistoryStore() as store:
            store.upsert_turn(
                scene_id="scene_001",
                turn_index=1,
                user_input="",
                scene_text=text_one,
            )
            store.upsert_turn(
                scene_id="scene_003",
                turn_index=3,
                user_input="",
                scene_text=text_three,
            )
            documents = store.list_documents()
            first = next(
                item
                for item in documents
                if item.source_type == "scene_paragraph" and item.turn_index == 1
            )
            third = next(
                item
                for item in documents
                if item.source_type == "scene_paragraph" and item.turn_index == 3
            )
            hash_one = history_document_text_hash(first)
            hash_three = history_document_text_hash(third)
            # The same document is served by a Continuity link (intent "a")
            # and an NPC link (intent "b").
            continuity = _continuity_link(
                item_id="thread",
                evidence_id=first.document_id,
                evidence_hash=hash_one,
                intents=("a",),
                basis=text_one,
            )
            npc_mixed = _npc_link(
                npc_id="npc-x",
                log_id=7,
                turn_index=1,
                evidence_id=first.document_id,
                evidence_hash=hash_one,
                intents=("b",),
                basis="北门巡检",
            )
            npc_pure = _npc_link(
                npc_id="npc-y",
                log_id=9,
                turn_index=3,
                evidence_id=third.document_id,
                evidence_hash=hash_three,
                intents=("b",),
                basis="南门守备",
            )
            result = ContextHistoryRetriever(
                store,
                diversity_policy=HistoryDiversityPolicy(max_hits=1),
            ).retrieve(
                self._plan("顾青", intent_id="b"),
                current_turn_index=5,
                continuity_links=[continuity],
                npc_field_links=[npc_mixed, npc_pure],
            )

        # Both documents are pure-NPC for intent "b"; recency prefers the newer
        # pure document over the mixed one even though the mixed document also
        # carries a Continuity attribution for another intent.
        assert [hit.evidence_id for hit in result.hits] == [
            third.document_id
        ]

    def test_older_stronger_relation_beats_newer_weaker_relation(self):
        # issue 16 必测 4：较旧 strong relation 排在较新 weak（但仍 strong）
        # 之前；recency 只在关联质量相等后决胜。
        text_older = "她接过铜印，正式成为北门巡检。"
        text_newer = "她巡防北门，责任重大。"
        with ConfirmedHistoryStore() as store:
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

    def test_bridge_basis_has_no_full_value_context_backfeed(self):
        # issue 16 P1-2：累积字段 node basis 只有持久化 change_basis，
        # 不再把完整 new_value 作为 context —— 只支持 retained 旧事实的
        # evidence 对 change_basis 得 zero，被 complete-basis 剥离。
        text = "她仍是北门巡检，今日照常巡视北门。"
        with ConfirmedHistoryStore() as store:
            store.upsert_turn(
                scene_id="scene_005",
                turn_index=5,
                user_input="",
                scene_text=text,
            )
            doc = next(
                item
                for item in store.list_documents()
                if item.source_type == "scene_paragraph"
            )
            link = _npc_link(
                npc_id="npc-a",
                log_id=7,
                turn_index=5,
                evidence_id=doc.document_id,
                evidence_hash=history_document_text_hash(doc),
                intents=("b",),
                basis="新增兼管东市职责",
                basis_complete=True,
            )
            result = ContextHistoryRetriever(
                store,
                diversity_policy=HistoryDiversityPolicy(max_hits=2),
            ).retrieve(
                self._plan("顾青", intent_id="b"),
                current_turn_index=6,
                continuity_links=[],
                npc_field_links=[link],
                matched_active_continuities=0,
                matched_archived_continuities=0,
                matched_npc_profiles=1,
            )

        assert result.invalid_relation_links == 1
        assert [hit.evidence_id for hit in result.hits] == []

    def test_bridge_basis_positive_control_passes(self):
        # 对照：真正支持 change_basis 的 evidence 仍 strong 入选。
        text = "她接手东市巡务，兼管东市。"
        with ConfirmedHistoryStore() as store:
            store.upsert_turn(
                scene_id="scene_005",
                turn_index=5,
                user_input="",
                scene_text=text,
            )
            doc = next(
                item
                for item in store.list_documents()
                if item.source_type == "scene_paragraph"
            )
            link = _npc_link(
                npc_id="npc-a",
                log_id=7,
                turn_index=5,
                evidence_id=doc.document_id,
                evidence_hash=history_document_text_hash(doc),
                intents=("b",),
                basis="新增兼管东市职责",
                basis_complete=True,
            )
            result = ContextHistoryRetriever(
                store,
                diversity_policy=HistoryDiversityPolicy(max_hits=2),
            ).retrieve(
                self._plan("顾青", intent_id="b"),
                current_turn_index=6,
                continuity_links=[],
                npc_field_links=[link],
                matched_active_continuities=0,
                matched_archived_continuities=0,
                matched_npc_profiles=1,
            )

        assert [hit.evidence_id for hit in result.hits] == [doc.document_id]

    def test_npc_recency_clamps_when_document_turn_is_ahead(self):
        text = "她接过铜印，正式成为北门巡检。"
        with ConfirmedHistoryStore() as store:
            store.upsert_turn(
                scene_id="scene_003",
                turn_index=3,
                user_input="",
                scene_text=text,
            )
            documents = store.list_documents()
            paragraph = next(
                item for item in documents if item.source_type == "scene_paragraph"
            )
            link = _npc_link(
                npc_id="npc-a",
                log_id=7,
                turn_index=3,
                evidence_id=paragraph.document_id,
                evidence_hash=history_document_text_hash(paragraph),
                intents=("q",),
                basis="北门巡检",
            )
            result = ContextHistoryRetriever(store).retrieve(
                self._plan("顾青"),
                current_turn_index=1,
                npc_field_links=[link],
            )

        assert [hit.evidence_id for hit in result.hits] == [
            paragraph.document_id
        ]

    def test_npc_field_links_flow_into_relation_hits(self):
        text = "她接过铜印，正式成为北门巡检。"
        with ConfirmedHistoryStore() as store:
            store.upsert_turn(
                scene_id="scene_001",
                turn_index=1,
                user_input="",
                scene_text=text,
            )
            documents = store.list_documents()
            paragraph = next(
                item for item in documents if item.source_type == "scene_paragraph"
            )
            link = _npc_link(
                npc_id="npc-guqing",
                field_name="role",
                log_id=7,
                turn_index=1,
                evidence_id=paragraph.document_id,
                evidence_hash=history_document_text_hash(paragraph),
                intents=("q",),
                basis="北门巡检",
            )
            result = ContextHistoryRetriever(store).retrieve(
                self._plan("顾青"),
                current_turn_index=2,
                npc_field_links=[link],
            )

        assert [hit.evidence_id for hit in result.hits] == [
            paragraph.document_id
        ]
        hit = result.hits[0]
        assert hit.matched_intent_ids == []
        assert hit.relation_intent_ids == ["q"]
        assert result.valid_relation_links == 1
        assert result.relation_hits_added == 1

    def test_npc_owner_cap_limits_relation_hits_end_to_end(self):
        text_one = "她接过铜印，正式成为北门巡检。"
        text_two = "她换上新甲，改任南门守备。"
        with ConfirmedHistoryStore() as store:
            store.upsert_turn(
                scene_id="scene_001",
                turn_index=1,
                user_input="",
                scene_text=text_one,
            )
            store.upsert_turn(
                scene_id="scene_002",
                turn_index=2,
                user_input="",
                scene_text=text_two,
            )
            documents = store.list_documents()
            first = next(
                item
                for item in documents
                if item.source_type == "scene_paragraph" and item.turn_index == 1
            )
            second = next(
                item
                for item in documents
                if item.source_type == "scene_paragraph" and item.turn_index == 2
            )
            links = [
                _npc_link(
                    npc_id="npc-guqing",
                    field_name="role",
                    log_id=7,
                    turn_index=1,
                    evidence_id=first.document_id,
                    evidence_hash=history_document_text_hash(first),
                    intents=("q",),
                    basis="北门巡检",
                ),
                _npc_link(
                    npc_id="npc-guqing",
                    field_name="role",
                    log_id=8,
                    turn_index=2,
                    evidence_id=second.document_id,
                    evidence_hash=history_document_text_hash(second),
                    intents=("q",),
                    basis="南门守备",
                ),
            ]
            result = ContextHistoryRetriever(
                store,
                diversity_policy=HistoryDiversityPolicy(
                    max_hits=10,
                    relation_owner_cap=1,
                ),
            ).retrieve(
                self._plan("顾青"),
                current_turn_index=3,
                npc_field_links=links,
            )

        assert len(result.hits) == 1
        assert result.relation_candidates_considered == 2
        assert result.relation_hits_added == 1
