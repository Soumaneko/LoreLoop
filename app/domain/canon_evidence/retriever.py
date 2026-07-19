from __future__ import annotations

import re
from collections import defaultdict

from app.domain.narrative_canon_query.schemas import (
    CanonEntityReference,
    NarrativeCanonQuery,
    NarrativeCanonQuerySet,
)

from .schemas import (
    CanonEvidenceHit,
    CanonEvidenceSet,
    CanonQueryEvidence,
)
from .store import CanonEvidenceStore


SEMANTIC_ADMISSION_THRESHOLD = 0.12

COMMON_HISTORY_RELATION_CUES = {
    "告诉", "听说", "得知", "第一次", "首次", "陌生", "询问",
    "发现", "意识到", "决定", "曾经", "此前", "过去", "以前",
    "去过", "到访", "探索", "住过",
}
CLAIM_TYPE_RELATION_CUES = {
    "past_event": {"发生", "做过", "见过", "遇到", "经历"},
    "knowledge_provenance": {
        "知道", "知悉", "获知", "说出", "解释", "记得", "相信",
    },
    "causal_motivation": {"因为", "因此", "为了", "原因", "动机", "目标"},
    "identity_or_relationship": {
        "身份", "关系", "认识", "朋友", "敌人", "亲人", "化名",
    },
    "ownership_or_resource": {
        "拥有", "持有", "属于", "获得", "丢失", "购买", "支付", "交给",
    },
    "location_or_presence": {
        "到过", "抵达", "离开", "前往", "来到", "居住", "位于", "在场",
    },
    "world_rule_or_history": {
        "传说", "规则", "历史", "始终", "只能", "无法", "由来",
    },
    "persistent_state": {
        "仍然", "依然", "已经", "保持", "失去", "受伤", "死亡", "存活",
    },
    "other": {"起初", "后来", "当时", "原本", "从前"},
}


class CanonEvidenceRetriever:
    def __init__(
        self,
        store: CanonEvidenceStore,
        entity_catalog: list[CanonEntityReference],
    ):
        self.store = store
        self.entity_catalog = entity_catalog
        self._surfaces = _surface_map(entity_catalog)

    def retrieve(
        self,
        query_set: NarrativeCanonQuerySet,
        *,
        evidence_per_query: int = 5,
    ) -> CanonEvidenceSet:
        if evidence_per_query < 1:
            raise ValueError("evidence_per_query must be >= 1")
        documents = self.store.list_documents()
        doc_mentions = self.store.mentions_for_documents(
            [document.document_id for document in documents]
        )
        by_turn: dict[int, list] = defaultdict(list)
        for document in documents:
            by_turn[document.turn_index].append(document)
        results = [
            self._retrieve_one(
                query,
                documents=documents,
                by_turn=by_turn,
                doc_mentions=doc_mentions,
                limit=evidence_per_query,
            )
            for query in query_set.queries
        ]
        return CanonEvidenceSet(
            results=results,
            indexed_documents=self.store.count(),
            indexed_turns=self.store.turn_count(),
        )

    def _retrieve_one(
        self,
        query: NarrativeCanonQuery,
        *,
        documents,
        by_turn,
        doc_mentions,
        limit: int,
    ) -> CanonQueryEvidence:
        requested_entities = _canonicalize_entities(query.entities, self._surfaces)
        search_texts = [query.claim, *query.lookup_queries]
        semantic_texts = [
            _remove_entity_surfaces(text, requested_entities, self._surfaces)
            for text in search_texts
        ]
        query_trigram_sets = [
            trigrams
            for text in semantic_texts
            if (trigrams := _trigrams(text))
        ]

        fts_hits: dict[str, float] = {}
        search_terms = _fts_terms(search_texts, requested_entities, self._surfaces)
        for term in search_terms:
            for document_id, score in self.store.fts_search(term).items():
                fts_hits[document_id] = max(fts_hits.get(document_id, 0.0), score)

        strong_scored = []
        weak_scored = []
        for document in documents:
            admission_documents = _three_paragraph_window(
                document,
                by_turn[document.turn_index],
            )
            admission_text = "\n\n".join(
                item.text for item in admission_documents
            )
            document_mentions = set().union(*(
                doc_mentions.get(item.document_id, set())
                for item in admission_documents
            ))
            matched_entities = sorted(
                set(requested_entities) & document_mentions
            )
            lexical_score, matched_trigrams = _best_trigram_match(
                query_trigram_sets,
                admission_text,
            )
            matched_relation_cues = _matched_relation_cues(
                query.claim_type,
                admission_text,
            )
            entity_cooccurs = (
                len(requested_entities) >= 2
                and len(matched_entities) == len(requested_entities)
            )
            entity_admitted = entity_cooccurs and bool(matched_relation_cues)
            semantic_admitted = lexical_score >= SEMANTIC_ADMISSION_THRESHOLD
            weak_entity_fallback = (
                entity_cooccurs
                and not entity_admitted
                and not semantic_admitted
            )
            has_fts = document.document_id in fts_hits
            if not (entity_admitted or semantic_admitted or weak_entity_fallback):
                continue

            source_weight = 1.0 if document.source_type == "scene_paragraph" else 0.72
            if weak_entity_fallback:
                score = source_weight * (
                    0.12
                    + 0.10 * lexical_score
                    + (0.01 if has_fts else 0.0)
                )
            else:
                score = source_weight * (
                    (0.58 if entity_admitted else 0.0)
                    + 0.39 * lexical_score
                    + (0.03 if has_fts else 0.0)
                )
            if score <= 0:
                continue
            channels = []
            if entity_admitted:
                channels.append("entity_relation_window")
            if semantic_admitted:
                channels.append("semantic_trigram_threshold")
            if weak_entity_fallback:
                channels.append("entity_window_fallback")
            if has_fts:
                channels.append("fts5_trigram")
            if matched_trigrams:
                channels.append("trigram_overlap")
            candidate = (
                score,
                document,
                matched_entities,
                _matched_terms(
                    [*search_terms, *matched_relation_cues],
                    admission_text,
                ),
                channels,
            )
            if weak_entity_fallback:
                weak_scored.append(candidate)
            else:
                strong_scored.append(candidate)

        scored = strong_scored if strong_scored else weak_scored
        effective_limit = limit if strong_scored else min(limit, 1)

        scored.sort(
            key=lambda item: (
                item[0],
                item[1].source_type == "scene_paragraph",
                -item[1].turn_index,
            ),
            reverse=True,
        )

        hits: list[CanonEvidenceHit] = []
        used_paragraphs_by_turn: dict[int, list[int | None]] = defaultdict(list)
        for score, document, matched_entities, matched_terms, channels in scored:
            used_paragraphs = used_paragraphs_by_turn[document.turn_index]
            if len(used_paragraphs) >= 2:
                continue
            if (
                document.paragraph_index is not None
                and any(
                    prior is not None and abs(prior - document.paragraph_index) <= 2
                    for prior in used_paragraphs
                )
            ):
                continue
            before, after = _adjacent_context(document, by_turn[document.turn_index])
            hits.append(CanonEvidenceHit(
                evidence_id=document.document_id,
                source_type=document.source_type,
                turn_index=document.turn_index,
                scene_id=document.scene_id,
                paragraph_index=document.paragraph_index,
                text=document.text,
                context_before=before,
                context_after=after,
                matched_entities=matched_entities,
                matched_terms=matched_terms,
                retrieval_channels=channels,
                score=round(score, 6),
            ))
            used_paragraphs.append(document.paragraph_index)
            if len(hits) >= effective_limit:
                break

        return CanonQueryEvidence(
            query_id=query.query_id,
            claim=query.claim,
            evidence=hits,
        )


def _surface_map(
    catalog: list[CanonEntityReference],
) -> dict[str, tuple[str, list[str]]]:
    result: dict[str, tuple[str, list[str]]] = {}
    for entity in catalog:
        surfaces = list(dict.fromkeys([entity.name, *entity.aliases]))
        for surface in surfaces:
            clean = surface.strip()
            if clean:
                result[clean.casefold()] = (entity.name, surfaces)
    return result


def _canonicalize_entities(
    entities: list[str],
    surfaces: dict[str, tuple[str, list[str]]],
) -> list[str]:
    result = []
    for raw_entity in entities:
        clean = raw_entity.strip()
        canonical = surfaces.get(clean.casefold(), (clean, []))[0]
        if canonical and canonical not in result:
            result.append(canonical)
    return result


def _remove_entity_surfaces(
    text: str,
    entities: list[str],
    surfaces: dict[str, tuple[str, list[str]]],
) -> str:
    result = text
    entity_surfaces: list[str] = []
    for entity in entities:
        match = surfaces.get(entity.casefold())
        entity_surfaces.extend(match[1] if match else [entity])
    for surface in sorted(set(entity_surfaces), key=len, reverse=True):
        if surface:
            result = re.sub(re.escape(surface), "", result, flags=re.IGNORECASE)
    return result


def _fts_terms(
    texts: list[str],
    entities: list[str],
    surfaces: dict[str, tuple[str, list[str]]],
) -> list[str]:
    terms: list[str] = []
    for entity in entities:
        match = surfaces.get(entity.casefold())
        candidates = match[1] if match else [entity]
        terms.extend(candidate for candidate in candidates if len(candidate.strip()) >= 3)
    for text in texts:
        terms.extend(re.findall(r"《[^》]{2,}》|[\u4e00-\u9fff]{3,12}", text))
    clean_terms = []
    seen = set()
    for term in terms:
        clean = term.strip("《》？?，,。；;：:！!")
        key = clean.casefold()
        if len(clean) < 3 or key in seen:
            continue
        seen.add(key)
        clean_terms.append(clean)
    return clean_terms[:36]


def _trigrams(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", text.casefold())
    normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", normalized)
    if len(normalized) < 3:
        return {normalized} if normalized else set()
    return {normalized[index:index + 3] for index in range(len(normalized) - 2)}


def _best_trigram_match(
    query_trigram_sets: list[set[str]],
    document_text: str,
) -> tuple[float, set[str]]:
    document_trigrams = _trigrams(document_text)
    best_score = 0.0
    best_matches: set[str] = set()
    for query_trigrams in query_trigram_sets:
        matched = query_trigrams & document_trigrams
        score = len(matched) / max(
            1,
            min(len(query_trigrams), len(document_trigrams)),
        )
        if score > best_score:
            best_score = score
            best_matches = matched
    return best_score, best_matches


def _three_paragraph_window(document, turn_documents) -> list:
    if document.source_type != "scene_paragraph" or document.paragraph_index is None:
        return [document]
    paragraphs = {
        item.paragraph_index: item
        for item in turn_documents
        if item.source_type == "scene_paragraph" and item.paragraph_index is not None
    }
    return [
        paragraphs[index]
        for index in range(document.paragraph_index - 1, document.paragraph_index + 2)
        if index in paragraphs
    ]


def _matched_relation_cues(claim_type: str, text: str) -> list[str]:
    cues = COMMON_HISTORY_RELATION_CUES | CLAIM_TYPE_RELATION_CUES.get(
        claim_type,
        set(),
    )
    return sorted(
        (cue for cue in cues if cue in text),
        key=lambda cue: (-len(cue), cue),
    )


def _matched_terms(terms: list[str], text: str) -> list[str]:
    folded = text.casefold()
    return [term for term in terms if term.casefold() in folded][:8]


def _adjacent_context(document, turn_documents) -> tuple[str | None, str | None]:
    if document.source_type != "scene_paragraph" or document.paragraph_index is None:
        return None, None
    paragraphs = {
        item.paragraph_index: item.text
        for item in turn_documents
        if item.source_type == "scene_paragraph" and item.paragraph_index is not None
    }
    before = [
        paragraphs[index]
        for index in range(document.paragraph_index - 2, document.paragraph_index)
        if index in paragraphs
    ]
    after = [
        paragraphs[index]
        for index in range(document.paragraph_index + 1, document.paragraph_index + 3)
        if index in paragraphs
    ]
    return "\n\n".join(before) or None, "\n\n".join(after) or None
