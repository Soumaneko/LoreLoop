from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.domain.confirmed_history import (
    ConfirmedHistoryStore,
    HistoryDocument,
    adjacent_context,
    best_trigram_match,
    document_window,
)

from .schemas import (
    ContextHistoryEvidenceSet,
    ContextHistoryHit,
    ContextSearchIntent,
    ContextSearchPlan,
)


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

        selected = _rank_diverse(
            search_plan,
            by_intent=by_intent,
            candidates=candidates,
        )
        hits = []
        for candidate in selected:
            before, after = adjacent_context(
                candidate.document,
                by_turn[candidate.document.turn_index],
                before=1,
                after=1,
            )
            hits.append(ContextHistoryHit(
                evidence_id=candidate.document.document_id,
                source_type=candidate.document.source_type,
                turn_index=candidate.document.turn_index,
                scene_id=candidate.document.scene_id,
                paragraph_index=candidate.document.paragraph_index,
                text=candidate.document.text,
                context_before=before,
                context_after=after,
                matched_intent_ids=sorted(candidate.matched_intent_ids),
                matched_entities=sorted(candidate.matched_entities),
                matched_topics=sorted(candidate.matched_topics),
                score=round(candidate.score, 6),
            ))
        return ContextHistoryEvidenceSet(
            hits=hits,
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
            window = document_window(
                document,
                by_turn[document.turn_index],
            )
            window_text = "\n\n".join(item.text for item in window)
            matched_entities = _matched_entities(intent, window_text)
            matched_topics = {
                topic
                for topic in intent.topics
                if topic.casefold() in window_text.casefold()
            }
            topic_score, _ = best_trigram_match(intent.topics, window_text)
            has_fts = any(item.document_id in fts_hits for item in window)
            if not matched_entities and not has_fts and topic_score < 0.2:
                continue

            entity_score = (
                len(matched_entities) / len(intent.entities)
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
            if intent.focus == "relationship" and len(matched_entities) >= 2:
                score += 0.12
            if document.source_type == "player_input":
                score *= 0.72

            scored.append(_Candidate(
                document=document,
                score=score,
                matched_intent_ids={intent.intent_id},
                matched_entities=matched_entities,
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
