from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.domain.confirmed_history import (
    ConfirmedHistoryStore,
    HistoryDocument,
    best_trigram_match,
    document_window,
)

from .clustering import SUPPORTING_EVIDENCE_MAX, cluster_near_duplicates
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


class ContextHistoryRetriever:
    """High-recall confirmed-history retrieval for Context Facts assembly."""

    def __init__(self, store: ConfirmedHistoryStore):
        self.store = store

    def retrieve(
        self,
        search_plan: ContextSearchPlan,
        *,
        current_turn_index: int,
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

        representative_candidates, member_clusters, representative_for = (
            _cluster_candidates(candidates)
        )
        by_intent_clustered = _remap_to_representatives(
            by_intent,
            representative_for,
        )

        selected = _rank_diverse(
            search_plan,
            by_intent=by_intent_clustered,
            candidates=representative_candidates,
        )
        hits = []
        for candidate in selected:
            members = member_clusters[candidate.document.document_id]
            cluster_ids = {
                member.document.document_id for member in members
            }
            window = document_window(
                candidate.document,
                by_turn[candidate.document.turn_index],
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
            hits.append(ContextHistoryHit(
                evidence_id=candidate.document.document_id,
                source_type=candidate.document.source_type,
                turn_index=candidate.document.turn_index,
                scene_id=candidate.document.scene_id,
                paragraph_index=candidate.document.paragraph_index,
                text=candidate.document.text,
                context_before=context_before,
                context_after=context_after,
                matched_intent_ids=sorted(candidate.matched_intent_ids),
                matched_entities=sorted(candidate.matched_entities),
                matched_topics=sorted(candidate.matched_topics),
                score=round(candidate.score, 6),
                supporting_evidence_ids=supporting_evidence_ids,
                supporting_evidence=supporting_evidence,
            ))
        return ContextHistoryEvidenceSet(
            hits=_deduplicate_evidence_across_hits(hits),
            indexed_documents=self.store.count(),
            indexed_turns=self.store.turn_count(),
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
            direct_has_fts = document.document_id in fts_hits
            direct_topic_score, _ = best_trigram_match(
                intent.topics,
                direct_text,
            )
            if (
                not direct_matched_entities
                and not direct_has_fts
                and direct_topic_score < DIRECT_TOPIC_GATE
            ):
                continue

            window = document_window(
                document,
                by_turn[document.turn_index],
            )
            window_text = "\n\n".join(item.text for item in window)
            matched_topics = {
                topic
                for topic in intent.topics
                if topic.casefold() in direct_text.casefold()
            }
            topic_score, _ = best_trigram_match(intent.topics, window_text)
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


def _rank_diverse(
    search_plan: ContextSearchPlan,
    *,
    by_intent: dict[str, list[_Candidate]],
    candidates: dict[str, _Candidate],
) -> list[_Candidate]:
    selected: list[_Candidate] = []
    selected_ids: set[str] = set()

    def add(candidate: _Candidate) -> bool:
        document = candidate.document
        if document.document_id in selected_ids:
            return False
        selected.append(candidates[document.document_id])
        selected_ids.add(document.document_id)
        return True

    for intent in search_plan.intents:
        for candidate in by_intent.get(intent.intent_id, []):
            if add(candidate):
                break

    remaining = sorted(
        candidates.values(),
        key=lambda candidate: (
            candidate.score,
            len(candidate.matched_intent_ids),
            candidate.document.turn_index,
        ),
        reverse=True,
    )
    for candidate in remaining:
        add(candidate)
    return selected


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
