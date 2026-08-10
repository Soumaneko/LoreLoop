from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.domain.confirmed_history import (
    ConfirmedHistoryStore,
    HistoryDocument,
    best_trigram_match,
    document_window,
    history_document_text_hash,
)
from app.domain.continuity import ContinuityHistoryLink

from .clustering import SUPPORTING_EVIDENCE_MAX, cluster_near_duplicates
from .diversity import (
    AdmissionPreview,
    HistoryDiversityPolicy,
    SelectionProposal,
    select_diverse_candidates,
)
from .evidence_support import (
    LinkSupport,
    classify_link_support,
    link_window_text,
)
from .schemas import (
    AdjacentEvidence,
    ContextHistoryEvidenceSet,
    ContextHistoryHit,
    ContextSearchIntent,
    ContextSearchPlan,
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
    relation_intent_ids: set[str] = field(default_factory=set)
    linked_continuity_ids: set[str] = field(default_factory=set)
    linked_operations: set[str] = field(default_factory=set)
    relation_match_reasons: set[str] = field(default_factory=set)
    relation_order: int = 1_000_000_000
    relation_intent_quality: dict[str, float] = field(default_factory=dict)
    relation_intent_class: dict[str, str] = field(default_factory=dict)
    relation_intent_degraded: dict[str, bool] = field(default_factory=dict)

    @property
    def relation_evidence_quality(self) -> float:
        return max(self.relation_intent_quality.values(), default=0.0)

    @property
    def relation_evidence_class(self) -> str | None:
        from .evidence_support import CLASS_RANK

        qualities = self.relation_intent_quality
        if not qualities:
            return None
        best_intent = max(
            qualities,
            key=lambda intent_id: (
                qualities[intent_id],
                CLASS_RANK.get(
                    self.relation_intent_class.get(intent_id, ""),
                    -1,
                ),
            ),
        )
        return self.relation_intent_class.get(best_intent)


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
        matched_active_continuities: int = 0,
        matched_archived_continuities: int = 0,
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

        node_supports, strong_documents = _compute_link_supports(
            continuity_links or [],
            documents_by_id=documents_by_id,
            by_turn=by_turn,
        )

        relation_document_ids: set[str] = set()
        stale_relation_links = 0
        valid_relation_links = 0
        invalid_relation_links = 0
        degraded_relation_links = 0
        for relation_order, link in enumerate(continuity_links or []):
            document = documents_by_id.get(link.evidence_id)
            if (
                document is None
                or document.source_type != "scene_paragraph"
                or (
                    link.evidence_hash is not None
                    and history_document_text_hash(document)
                    != link.evidence_hash
                )
            ):
                stale_relation_links += 1
                continue
            valid_relation_links += 1
            support = node_supports.get(
                (link.continuity_id, link.operation),
                {},
            ).get(link.evidence_id)
            if support is None:
                continue
            if _suppress_attribution(
                link,
                support,
                node_supports,
                strong_documents,
            ):
                invalid_relation_links += 1
                continue
            if support.degraded:
                degraded_relation_links += 1
            relation_document_ids.add(document.document_id)
            candidate = candidates.get(document.document_id)
            if candidate is None:
                candidate = _Candidate(document=document, score=0.0)
                candidates[document.document_id] = candidate
            candidate.relation_intent_ids.update(link.triggering_intent_ids)
            candidate.linked_continuity_ids.add(link.continuity_id)
            candidate.linked_operations.add(link.operation)
            candidate.relation_match_reasons.update(link.match_reasons)
            candidate.relation_order = min(
                candidate.relation_order,
                relation_order,
            )
            for intent_id in link.triggering_intent_ids:
                current = candidate.relation_intent_quality.get(intent_id)
                if current is None or support.quality > current:
                    candidate.relation_intent_quality[intent_id] = (
                        support.quality
                    )
                    candidate.relation_intent_class[intent_id] = (
                        support.support_class
                    )
                candidate.relation_intent_degraded[intent_id] = (
                    candidate.relation_intent_degraded.get(intent_id, True)
                    and support.degraded
                )

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

        intents = {intent.intent_id: intent for intent in search_plan.intents}
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

        relation_by_intent: dict[str, list[_Candidate]] = defaultdict(list)
        for candidate in relation_representatives.values():
            for intent_id in candidate.relation_intent_ids:
                if intent_id in intents:
                    relation_by_intent[intent_id].append(candidate)
        for intent in search_plan.intents:
            relation_candidates = sorted(
                relation_by_intent.get(intent.intent_id, []),
                key=lambda candidate: (
                    -candidate.relation_intent_quality.get(
                        intent.intent_id,
                        0.0,
                    ),
                    candidate.relation_order,
                    -candidate.document.turn_index,
                    candidate.document.document_id,
                ),
            )
            for channel_rank, candidate in enumerate(relation_candidates):
                proposals.append(SelectionProposal(
                    candidate=candidate,
                    owner_intent_id=intent.intent_id,
                    route="relation",
                    channel_rank=channel_rank,
                    signal_rank=_relation_signal_rank(candidate),
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
        return ContextHistoryEvidenceSet(
            hits=hits,
            indexed_documents=self.store.count(),
            indexed_turns=self.store.turn_count(),
            matched_active_continuities=matched_active_continuities,
            matched_archived_continuities=matched_archived_continuities,
            valid_relation_links=valid_relation_links,
            relation_candidates_considered=len(relation_representatives),
            relation_hits_added=sum(
                1
                for hit in hits
                if hit.relation_intent_ids and not hit.matched_intent_ids
            ),
            stale_relation_links=stale_relation_links,
            invalid_relation_links=invalid_relation_links,
            degraded_relation_links=degraded_relation_links,
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


def _compute_link_supports(
    links: list[ContinuityHistoryLink],
    *,
    documents_by_id: dict[str, HistoryDocument],
    by_turn: dict[int, list[HistoryDocument]],
) -> tuple[
    dict[tuple[str, str], dict[str, LinkSupport]],
    set[str],
]:
    """Score every lifecycle link against its node basis.

    Returns a node-keyed map of per-evidence LinkSupport plus the set of
    documents that carry at least one strong link (used to decide whether a
    zero-support link can be safely de-attributed).
    """
    nodes: dict[tuple[str, str], list[ContinuityHistoryLink]] = defaultdict(list)
    for link in links:
        nodes[(link.continuity_id, link.operation)].append(link)

    node_supports: dict[tuple[str, str], dict[str, LinkSupport]] = {}
    strong_documents: set[str] = set()
    for node_key, node_links in nodes.items():
        node_evidence_ids = {link.evidence_id for link in node_links}
        for link in node_links:
            document = documents_by_id.get(link.evidence_id)
            if (
                document is None
                or document.source_type != "scene_paragraph"
                or (
                    link.evidence_hash is not None
                    and history_document_text_hash(document)
                    != link.evidence_hash
                )
            ):
                continue
            window_text = link_window_text(
                document,
                by_turn[document.turn_index],
                node_evidence_ids,
            )
            support = classify_link_support(
                basis_primary=link.evidence_basis_primary,
                basis_context=link.evidence_basis_context,
                basis_complete=link.basis_complete,
                window_text=window_text,
            )
            node_supports.setdefault(node_key, {})[link.evidence_id] = support
            if support.support_class == "strong":
                strong_documents.add(link.evidence_id)
    return node_supports, strong_documents


def _suppress_attribution(
    link: ContinuityHistoryLink,
    support: LinkSupport,
    node_supports: dict[tuple[str, str], dict[str, LinkSupport]],
    strong_documents: set[str],
) -> bool:
    """Correct a misleading relation attribution without deleting evidence.

    A zero-support link may be de-attributed only when it is provably
    redundant: either the same lifecycle node carries another strong evidence,
    or the same document is already represented by a strong link on another
    node. Everything else fails open.
    """
    if not support.basis_complete or support.support_class != "zero":
        return False
    node_has_other_strong = any(
        other.support_class == "strong"
        for other in node_supports.get(
            (link.continuity_id, link.operation),
            {},
        ).values()
    )
    return node_has_other_strong or link.evidence_id in strong_documents


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
        relation_intent_ids=set().union(
            *(member.relation_intent_ids for member in cluster)
        ),
        linked_continuity_ids=set().union(
            *(member.linked_continuity_ids for member in cluster)
        ),
        linked_operations=set().union(
            *(member.linked_operations for member in cluster)
        ),
        relation_match_reasons=set().union(
            *(member.relation_match_reasons for member in cluster)
        ),
        relation_order=min(member.relation_order for member in cluster),
        relation_intent_quality=_merge_intent_quality(cluster),
        relation_intent_class=_merge_intent_class(cluster),
        relation_intent_degraded=_merge_intent_degraded(cluster),
    )


def _merged_intent_keys(
    members: list[_Candidate],
) -> set[str]:
    return set().union(*(set(member.relation_intent_quality) for member in members))


def _merge_intent_quality(members: list[_Candidate]) -> dict[str, float]:
    return {
        intent_id: max(
            member.relation_intent_quality.get(intent_id, 0.0)
            for member in members
        )
        for intent_id in _merged_intent_keys(members)
    }


def _merge_intent_class(members: list[_Candidate]) -> dict[str, str]:
    from .evidence_support import CLASS_RANK

    quality = _merge_intent_quality(members)
    merged: dict[str, str] = {}
    for intent_id, best in quality.items():
        best_class: str | None = None
        best_rank = -1
        for member in members:
            member_quality = member.relation_intent_quality.get(intent_id)
            member_class = member.relation_intent_class.get(intent_id)
            if (
                member_quality is None
                or member_class is None
                or member_quality != best
            ):
                continue
            rank = CLASS_RANK.get(member_class, -1)
            if rank > best_rank:
                best_rank = rank
                best_class = member_class
        if best_class is not None:
            merged[intent_id] = best_class
    return merged


def _merge_intent_degraded(members: list[_Candidate]) -> dict[str, bool]:
    # Only relation-bearing members that actually serve an intent decide its
    # degraded status; a direct-only member must not un-degrade it.
    merged: dict[str, bool] = {}
    for intent_id in _merged_intent_keys(members):
        serving = [
            member
            for member in members
            if intent_id in member.relation_intent_quality
        ]
        merged[intent_id] = all(
            member.relation_intent_degraded.get(intent_id, True)
            for member in serving
        )
    return merged


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


def _relation_signal_rank(candidate: _Candidate) -> int:
    """Return a categorical structural-link tier for final selection."""
    reasons = candidate.relation_match_reasons
    if reasons.intersection({
        "two_entity_match",
        "subject_topic_match",
        "premise_topic_match",
        "current_state_topic_match",
    }):
        return 0
    if reasons.intersection({"exact_entity_match", "alias_match"}):
        return 2
    if "location_match" in reasons:
        return 4
    return 6


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
        relation_intent_ids=sorted(candidate.relation_intent_ids),
        linked_continuity_ids=sorted(candidate.linked_continuity_ids),
        linked_operations=sorted(candidate.linked_operations),
        relation_match_reasons=sorted(candidate.relation_match_reasons),
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
