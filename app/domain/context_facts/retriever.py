from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

from app.domain.confirmed_history import (
    ConfirmedHistoryStore,
    HistoryDocument,
    best_trigram_match,
    document_window,
)
from app.domain.continuity import ContinuityHistoryLink
from app.domain.npc_profile.field_history import NPCFieldHistoryName
from app.domain.npc_profile.history_bridge import NPCFieldHistoryLink

from .clustering import SUPPORTING_EVIDENCE_MAX, cluster_near_duplicates
from .diversity import (
    AdmissionPreview,
    HistoryDiversityPolicy,
    SelectionProposal,
    select_diverse_candidates,
)
from .relation_admission import (
    RelationAdmission,
    continuity_link_to_evidence,
    judge_relation_links,
    npc_field_link_to_evidence,
)
from .schemas import (
    AdjacentEvidence,
    ContextHistoryEvidenceSet,
    ContextHistoryHit,
    ContextSearchIntent,
    ContextSearchPlan,
    LinkedNPCFieldChange,
    SupportingEvidence,
)

DIRECT_TOPIC_GATE = 0.2


@dataclass
class _Candidate:
    document: HistoryDocument
    score: float
    matched_intent_ids: set[str] = field(default_factory=set)
    matched_entities: set[str] = field(default_factory=set)
    matched_topics: set[str] = field(default_factory=set)
    intent_scores: dict[str, float] = field(default_factory=dict)
    intent_matched_entities: dict[str, set[str]] = field(default_factory=dict)
    intent_matched_topics: dict[str, set[str]] = field(default_factory=dict)
    relation_admission: RelationAdmission | None = None

    @property
    def relation_evidence_quality(self) -> float:
        admission = self.relation_admission
        return admission.evidence_quality if admission is not None else 0.0

    @property
    def relation_evidence_class(self) -> str | None:
        admission = self.relation_admission
        return admission.evidence_class if admission is not None else None


class ContextHistoryRetriever:
    """High-recall confirmed-history retrieval for Context Facts assembly."""

    def __init__(
        self,
        store: ConfirmedHistoryStore,
        *,
        diversity_policy: HistoryDiversityPolicy | None = None,
    ):
        self.store = store
        self.diversity_policy = diversity_policy or HistoryDiversityPolicy()

    def retrieve(
        self,
        search_plan: ContextSearchPlan,
        *,
        current_turn_index: int,
        continuity_links: list[ContinuityHistoryLink] | None = None,
        npc_field_links: Sequence[NPCFieldHistoryLink] | None = None,
        matched_active_continuities: int = 0,
        matched_archived_continuities: int = 0,
        matched_npc_profiles: int = 0,
    ) -> ContextHistoryEvidenceSet:
        documents = self.store.list_documents()
        by_turn: dict[int, list[HistoryDocument]] = defaultdict(list)
        for document in documents:
            by_turn[document.turn_index].append(document)

        fts_cache: dict[str, dict[str, float]] = {}
        candidates: dict[str, _Candidate] = {}
        by_intent: dict[str, list[_Candidate]] = {}
        for intent in search_plan.intents:
            intent_candidates = self._retrieve_intent(
                intent,
                documents=documents,
                by_turn=by_turn,
                current_turn_index=current_turn_index,
                fts_cache=fts_cache,
            )
            by_intent[intent.intent_id] = intent_candidates
            for candidate in intent_candidates:
                existing = candidates.get(candidate.document.document_id)
                if existing is None:
                    candidates[candidate.document.document_id] = candidate
                    continue
                existing.score = max(existing.score, candidate.score)
                existing.matched_intent_ids.update(candidate.matched_intent_ids)
                existing.matched_entities.update(candidate.matched_entities)
                existing.matched_topics.update(candidate.matched_topics)
                existing.intent_scores.update(candidate.intent_scores)
                existing.intent_matched_entities.update(
                    candidate.intent_matched_entities
                )
                existing.intent_matched_topics.update(
                    candidate.intent_matched_topics
                )

        documents_by_id = {
            document.document_id: document for document in documents
        }

        evidence_links = [
            *(continuity_link_to_evidence(link) for link in (continuity_links or [])),
            *(
                npc_field_link_to_evidence(link)
                for link in (npc_field_links or [])
            ),
        ]
        admissions, diagnostics = judge_relation_links(
            evidence_links,
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )
        relation_document_ids: set[str] = set(admissions)
        for document_id, admission in admissions.items():
            candidate = candidates.get(document_id)
            if candidate is None:
                candidate = _Candidate(
                    document=documents_by_id[document_id],
                    score=0.0,
                )
                candidates[document_id] = candidate
            candidate.relation_admission = admission

        representative_candidates, member_clusters, representative_for = (
            _cluster_candidates(candidates)
        )
        by_intent_clustered = _remap_to_representatives(
            by_intent,
            representative_for,
        )
        relation_representatives: dict[str, _Candidate] = {}
        for document_id in relation_document_ids:
            representative = representative_for[document_id]
            if representative.matched_intent_ids:
                continue
            relation_representatives[
                representative.document.document_id
            ] = representative

        proposals: list[SelectionProposal[_Candidate]] = []
        for intent in search_plan.intents:
            for channel_rank, candidate in enumerate(
                by_intent_clustered.get(intent.intent_id, [])
            ):
                proposals.append(SelectionProposal(
                    candidate=candidate,
                    owner_intent_id=intent.intent_id,
                    route="direct",
                    channel_rank=channel_rank,
                    signal_rank=_direct_signal_rank(candidate, intent),
                ))

        relation_pairs: list[tuple[_Candidate, RelationAdmission]] = []
        for candidate in relation_representatives.values():
            admission = candidate.relation_admission
            if admission is None:
                continue
            relation_pairs.append((candidate, admission))
        for intent in search_plan.intents:
            channel = [
                pair
                for pair in relation_pairs
                if intent.intent_id in pair[1].served_intent_ids
            ]
            channel.sort(key=lambda pair: (
                -pair[1].intent_quality.get(intent.intent_id, 0.0),
                # Recency is the quality tie-break: equal-quality nodes rank
                # newest first (issue 16); link arrival order only resolves
                # remaining determinism.
                -pair[0].document.turn_index,
                pair[1].intent_first_order.get(
                    intent.intent_id,
                    1_000_000_000,
                ),
                pair[0].document.document_id,
            ))
            for channel_rank, (candidate, admission) in enumerate(channel):
                owners = admission.intent_owner_keys.get(
                    intent.intent_id,
                    frozenset(),
                )
                is_pure_npc = bool(owners) and all(
                    owner.startswith("npc_profile:") for owner in owners
                )
                proposals.append(SelectionProposal(
                    candidate=candidate,
                    owner_intent_id=intent.intent_id,
                    route="relation",
                    channel_rank=channel_rank,
                    signal_rank=admission.signal_rank_for(
                        intent.intent_id
                    ),
                    recency_rank=(
                        max(
                            0,
                            current_turn_index
                            - candidate.document.turn_index,
                        )
                        if is_pure_npc
                        else 0
                    ),
                ))

        hits: list[ContextHistoryHit] = []

        def preview(candidate: _Candidate) -> AdmissionPreview | None:
            hit = _build_history_hit(
                candidate,
                members=member_clusters[candidate.document.document_id],
                turn_documents=by_turn[candidate.document.turn_index],
            )
            fitted = _fit_hit_to_payload_budget(
                hits,
                hit,
                policy=self.diversity_policy,
            )
            if fitted is None:
                return None
            return AdmissionPreview(
                state=fitted,
                payload_chars=_projected_history_chars(fitted),
            )

        def commit(candidate_preview: AdmissionPreview) -> None:
            hits[:] = candidate_preview.state

        select_diverse_candidates(
            search_plan,
            proposals=proposals,
            policy=self.diversity_policy,
            preview=preview,
            commit=commit,
        )
        npc_relation_candidates = sum(
            1
            for candidate in relation_representatives.values()
            if candidate.relation_admission is not None
            and candidate.relation_admission.linked_npc_changes
        )
        return ContextHistoryEvidenceSet(
            hits=hits,
            indexed_documents=self.store.count(),
            indexed_turns=self.store.turn_count(),
            matched_active_continuities=matched_active_continuities,
            matched_archived_continuities=matched_archived_continuities,
            matched_npc_profiles=matched_npc_profiles,
            valid_relation_links=diagnostics.valid_links,
            relation_candidates_considered=len(relation_representatives),
            relation_hits_added=sum(
                1
                for hit in hits
                if hit.relation_intent_ids and not hit.matched_intent_ids
            ),
            stale_relation_links=diagnostics.stale_links,
            invalid_relation_links=diagnostics.invalid_links,
            degraded_relation_links=diagnostics.degraded_links,
            npc_valid_links=diagnostics.npc_valid_links,
            npc_stale_links=diagnostics.npc_stale_links,
            npc_invalid_links=diagnostics.npc_invalid_links,
            npc_degraded_links=diagnostics.npc_degraded_links,
            npc_relation_candidates=npc_relation_candidates,
            npc_relation_hits_added=sum(
                1
                for hit in hits
                if hit.linked_npc_changes and not hit.matched_intent_ids
            ),
        )

    def _retrieve_intent(
        self,
        intent: ContextSearchIntent,
        *,
        documents: list[HistoryDocument],
        by_turn: dict[int, list[HistoryDocument]],
        current_turn_index: int,
        fts_cache: dict[str, dict[str, float]],
    ) -> list[_Candidate]:
        query_terms = _query_terms(intent)
        fts_hits: set[str] = set()
        for term in query_terms:
            if len(term) < 3:
                continue
            if term not in fts_cache:
                fts_cache[term] = self.store.fts_search(term)
            hits = fts_cache[term]
            fts_hits.update(hits)

        scored: list[_Candidate] = []
        for document in documents:
            direct_text = document.text
            direct_matched_entities = _matched_entities(intent, direct_text)
            direct_matched_topics = _matched_topics(intent, direct_text)
            direct_has_fts = document.document_id in fts_hits
            direct_topic_score, _ = best_trigram_match(
                intent.topics,
                direct_text,
            )
            if (
                not direct_matched_entities
                and not direct_matched_topics
                and not direct_has_fts
                and direct_topic_score < DIRECT_TOPIC_GATE
            ):
                continue

            window = document_window(
                document,
                by_turn[document.turn_index],
            )
            window_text = "\n\n".join(item.text for item in window)
            matched_topics = direct_matched_topics
            topic_score, _ = best_trigram_match(intent.topics, window_text)
            if direct_matched_topics:
                topic_score = max(topic_score, 1.0)
            has_fts = any(item.document_id in fts_hits for item in window)
            entity_score = (
                len(direct_matched_entities) / len(intent.entities)
                if intent.entities
                else 0.0
            )
            age = max(0, current_turn_index - document.turn_index)
            recency_score = 1.0 / (1.0 + age / 8.0)
            if intent.entities:
                score = (
                    0.56 * entity_score
                    + 0.22 * topic_score
                    + (0.10 if has_fts else 0.0)
                    + 0.12 * recency_score
                )
            else:
                score = (
                    0.72 * topic_score
                    + (0.14 if has_fts else 0.0)
                    + 0.14 * recency_score
                )
            if intent.focus == "relationship" and len(direct_matched_entities) >= 2:
                score += 0.12
            if document.source_type == "player_input":
                score *= 0.72

            scored.append(_Candidate(
                document=document,
                score=score,
                matched_intent_ids={intent.intent_id},
                matched_entities=direct_matched_entities,
                matched_topics=matched_topics,
                intent_scores={intent.intent_id: score},
                intent_matched_entities={
                    intent.intent_id: set(direct_matched_entities)
                },
                intent_matched_topics={
                    intent.intent_id: set(direct_matched_topics)
                },
            ))

        scored.sort(
            key=lambda candidate: (
                candidate.score,
                candidate.document.source_type == "scene_paragraph",
                candidate.document.turn_index,
            ),
            reverse=True,
        )
        return scored


def _cluster_candidates(
    candidates: dict[str, _Candidate],
) -> tuple[
    dict[str, _Candidate],
    dict[str, list[_Candidate]],
    dict[str, _Candidate],
]:
    """Group near-duplicate scene paragraphs and expose cluster membership.

    Returns representative candidates keyed by representative document ID, the
    member list for every representative, and a member-to-representative map.
    player_input documents are never merged and stay as single-member clusters.
    """
    clusters = cluster_near_duplicates([
        candidate
        for candidate in candidates.values()
        if candidate.document.source_type == "scene_paragraph"
    ])
    representative_for: dict[str, _Candidate] = {}
    for candidate in candidates.values():
        representative_for[candidate.document.document_id] = candidate

    representative_candidates: dict[str, _Candidate] = {}
    member_clusters: dict[str, list[_Candidate]] = {}
    for cluster in clusters:
        representative = _merged_representative(cluster)
        representative_candidates[
            representative.document.document_id
        ] = representative
        member_clusters[representative.document.document_id] = cluster
        for member in cluster:
            representative_for[member.document.document_id] = representative
    for candidate in candidates.values():
        if candidate.document.source_type != "scene_paragraph":
            representative_candidates[candidate.document.document_id] = candidate
            member_clusters[candidate.document.document_id] = [candidate]

    return representative_candidates, member_clusters, representative_for


def _merged_representative(cluster: list[_Candidate]) -> _Candidate:
    """Merge a cluster's metadata onto its strongest member.

    The representative keeps the strongest member's document (score first) but
    carries the union of every member's matched intents/entities/topics and the
    highest member score, so ranking and hits reflect the whole cluster.
    """
    representative = cluster[0]
    relation_admissions = [
        member.relation_admission
        for member in cluster
        if member.relation_admission is not None
    ]
    return _Candidate(
        document=representative.document,
        score=max(member.score for member in cluster),
        matched_intent_ids=set().union(
            *(member.matched_intent_ids for member in cluster)
        ),
        matched_entities=set().union(
            *(member.matched_entities for member in cluster)
        ),
        matched_topics=set().union(
            *(member.matched_topics for member in cluster)
        ),
        intent_scores={
            intent_id: max(
                member.intent_scores.get(intent_id, 0.0)
                for member in cluster
            )
            for intent_id in set().union(
                *(set(member.intent_scores) for member in cluster)
            )
        },
        intent_matched_entities={
            intent_id: set().union(*(
                member.intent_matched_entities.get(intent_id, set())
                for member in cluster
            ))
            for intent_id in set().union(
                *(set(member.intent_matched_entities) for member in cluster)
            )
        },
        intent_matched_topics={
            intent_id: set().union(*(
                member.intent_matched_topics.get(intent_id, set())
                for member in cluster
            ))
            for intent_id in set().union(
                *(set(member.intent_matched_topics) for member in cluster)
            )
        },
        relation_admission=(
            RelationAdmission.merge(relation_admissions)
            if relation_admissions
            else None
        ),
    )


def _remap_to_representatives(
    by_intent: dict[str, list[_Candidate]],
    representative_for: dict[str, _Candidate],
) -> dict[str, list[_Candidate]]:
    """Rewrite per-intent candidate lists so merged members point at their
    cluster representative, de-duplicating each intent's list."""
    remapped: dict[str, list[_Candidate]] = {}
    for intent_id, intent_candidates in by_intent.items():
        seen: set[str] = set()
        grouped: list[_Candidate] = []
        for candidate in intent_candidates:
            representative = representative_for.get(
                candidate.document.document_id,
                candidate,
            )
            if representative.document.document_id in seen:
                continue
            seen.add(representative.document.document_id)
            grouped.append(representative)
        remapped[intent_id] = grouped
    return remapped


def _query_terms(intent: ContextSearchIntent) -> list[str]:
    values = [
        *[
            surface
            for entity in intent.entities
            for surface in [entity.name, *entity.aliases]
        ],
        *intent.topics,
    ]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = value.strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _matched_entities(
    intent: ContextSearchIntent,
    text: str,
) -> set[str]:
    folded_text = text.casefold()
    return {
        entity.name
        for entity in intent.entities
        if any(
            surface.strip()
            and surface.strip().casefold() in folded_text
            for surface in [entity.name, *entity.aliases]
        )
    }


def _matched_topics(
    intent: ContextSearchIntent,
    text: str,
) -> set[str]:
    folded_text = text.casefold()
    return {
        clean
        for topic in intent.topics
        if (clean := topic.strip()) and clean.casefold() in folded_text
    }


def _direct_signal_rank(
    candidate: _Candidate,
    intent: ContextSearchIntent,
) -> int:
    """Return a categorical direct-match tier, never a cross-route score."""
    matched_entities = candidate.intent_matched_entities.get(
        intent.intent_id,
        set(),
    )
    matched_topics = candidate.intent_matched_topics.get(
        intent.intent_id,
        set(),
    )
    if matched_topics or (
        intent.focus == "relationship" and len(matched_entities) >= 2
    ):
        category = 0
    elif matched_entities:
        category = 1
    else:
        category = 2
    source_penalty = candidate.document.source_type == "player_input"
    return category * 2 + int(source_penalty)


def _build_history_hit(
    candidate: _Candidate,
    *,
    members: list[_Candidate],
    turn_documents: list[HistoryDocument],
) -> ContextHistoryHit:
    cluster_ids = {member.document.document_id for member in members}
    window = document_window(
        candidate.document,
        turn_documents,
        before=1,
        after=1,
    )
    context_before = [
        AdjacentEvidence(evidence_id=item.document_id, text=item.text)
        for item in window
        if (
            item.document_id not in cluster_ids
            and item.paragraph_index is not None
            and candidate.document.paragraph_index is not None
            and item.paragraph_index < candidate.document.paragraph_index
        )
    ]
    context_after = [
        AdjacentEvidence(evidence_id=item.document_id, text=item.text)
        for item in window
        if (
            item.document_id not in cluster_ids
            and item.paragraph_index is not None
            and candidate.document.paragraph_index is not None
            and item.paragraph_index > candidate.document.paragraph_index
        )
    ]
    supporting_members = [
        member
        for member in members
        if member.document.document_id != candidate.document.document_id
    ][:SUPPORTING_EVIDENCE_MAX]
    supporting_evidence_ids = sorted(
        member.document.document_id for member in supporting_members
    )
    supporting_evidence = [
        SupportingEvidence(
            evidence_id=member.document.document_id,
            text=member.document.text,
        )
        for member in supporting_members
    ]
    admission = candidate.relation_admission
    return ContextHistoryHit(
        evidence_id=candidate.document.document_id,
        source_type=candidate.document.source_type,
        turn_index=candidate.document.turn_index,
        scene_id=candidate.document.scene_id,
        paragraph_index=candidate.document.paragraph_index,
        text=candidate.document.text,
        context_before=context_before,
        context_after=context_after,
        matched_intent_ids=sorted(candidate.matched_intent_ids),
        relation_intent_ids=(
            sorted(admission.served_intent_ids) if admission is not None else []
        ),
        linked_continuity_ids=(
            sorted(admission.matched_continuity_ids)
            if admission is not None
            else []
        ),
        linked_operations=(
            sorted(admission.linked_operations)
            if admission is not None
            else []
        ),
        linked_npc_changes=(
            [
                LinkedNPCFieldChange(
                    npc_id=npc_id,
                    field_name=cast(NPCFieldHistoryName, field_name),
                    log_id=log_id,
                )
                for npc_id, field_name, log_id in sorted(
                    admission.linked_npc_changes
                )
            ]
            if admission is not None
            else []
        ),
        linked_npc_profile_ids=(
            sorted({
                npc_id
                for npc_id, _, _ in admission.linked_npc_changes
            })
            if admission is not None
            else []
        ),
        relation_match_reasons=(
            sorted(admission.match_reasons) if admission is not None else []
        ),
        relation_evidence_quality=(
            round(candidate.relation_evidence_quality, 6)
            if candidate.relation_evidence_class is not None
            else None
        ),
        relation_evidence_class=candidate.relation_evidence_class,
        matched_entities=sorted(candidate.matched_entities),
        matched_topics=sorted(candidate.matched_topics),
        score=round(candidate.score, 6),
        supporting_evidence_ids=supporting_evidence_ids,
        supporting_evidence=supporting_evidence,
    )




def _fit_hit_to_payload_budget(
    existing_hits: list[ContextHistoryHit],
    hit: ContextHistoryHit,
    *,
    policy: HistoryDiversityPolicy,
) -> list[ContextHistoryHit] | None:
    """Return the exact globally de-duplicated payload if the hit can fit."""
    if len(hit.text) > policy.max_evidence_text_chars:
        hit = _truncate_hit(hit, policy.max_evidence_text_chars)

    def materialize(candidate_hit: ContextHistoryHit) -> list[ContextHistoryHit]:
        return _deduplicate_evidence_across_hits([*existing_hits, candidate_hit])

    def fits(candidate_hits: list[ContextHistoryHit]) -> bool:
        return _projected_history_chars(candidate_hits) <= policy.max_payload_chars

    full = materialize(hit)
    if fits(full):
        return full

    stripped = hit.model_copy(update={
        "context_before": [],
        "context_after": [],
        "supporting_evidence_ids": [],
        "supporting_evidence": [],
    })
    stripped_hits = materialize(stripped)
    if fits(stripped_hits):
        return stripped_hits

    max_excerpt = min(len(hit.text) - 1, policy.max_evidence_text_chars)
    if max_excerpt < policy.min_excerpt_chars:
        return None
    minimum = _truncate_hit(stripped, policy.min_excerpt_chars)
    minimum_hits = materialize(minimum)
    if not fits(minimum_hits):
        return None

    best = minimum_hits
    low = policy.min_excerpt_chars
    high = max_excerpt
    while low <= high:
        middle = (low + high) // 2
        excerpt_hits = materialize(_truncate_hit(stripped, middle))
        if fits(excerpt_hits):
            best = excerpt_hits
            low = middle + 1
        else:
            high = middle - 1
    return best


def _projected_history_chars(hits: list[ContextHistoryHit]) -> int:
    from .views import project_context_history

    evidence = ContextHistoryEvidenceSet(hits=hits)
    return len(project_context_history(evidence))


def _truncate_hit(hit: ContextHistoryHit, char_limit: int) -> ContextHistoryHit:
    original_chars = hit.original_text_chars or len(hit.text)
    text = _bounded_excerpt(hit.text, char_limit)
    return hit.model_copy(update={
        "text": text,
        "text_truncated": True,
        "original_text_chars": original_chars,
    })


def _bounded_excerpt(text: str, char_limit: int) -> str:
    marker = "\n…[bounded excerpt]…\n"
    if len(text) <= char_limit:
        return text
    if char_limit <= len(marker) + 2:
        return text[:char_limit]
    remaining = char_limit - len(marker)
    head = (remaining + 1) // 2
    tail = remaining // 2
    return f"{text[:head]}{marker}{text[-tail:]}"


def _deduplicate_evidence_across_hits(
    hits: list[ContextHistoryHit],
) -> list[ContextHistoryHit]:
    """Keep every HistoryDocument at most once in the projected evidence set.

    Main hits and supporting evidence keep priority; adjacent context is only
    kept when its document has not appeared anywhere yet, claimed in hit order
    (higher-ranked hits first). This makes the LLM projection match the SPEC
    invariant that a document never appears twice.
    """
    reserved: set[str] = set()
    for hit in hits:
        reserved.add(hit.evidence_id)
        reserved.update(hit.supporting_evidence_ids)

    deduped: list[ContextHistoryHit] = []
    for hit in hits:
        context_before = [
            item
            for item in hit.context_before
            if item.evidence_id not in reserved
        ]
        context_after = [
            item
            for item in hit.context_after
            if item.evidence_id not in reserved
        ]
        reserved.update(item.evidence_id for item in context_before)
        reserved.update(item.evidence_id for item in context_after)
        if context_before == hit.context_before and context_after == hit.context_after:
            deduped.append(hit)
            continue
        deduped.append(hit.model_copy(update={
            "context_before": context_before,
            "context_after": context_after,
        }))
    return deduped
