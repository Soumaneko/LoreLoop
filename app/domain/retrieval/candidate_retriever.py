from pathlib import Path

from app.domain.action.schemas import ActionParseResult, Anchor, AtomicAction
from app.domain.recall_memory.schemas import MemoryRecord
from app.domain.recall_memory.pending_store import PendingMemoryStore
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.alias_store import AliasStore
from app.domain.retrieval.config import (
    LOW_WEIGHT_ANCHOR_KINDS,
    RetrievalConfig,
)
from app.domain.retrieval.rrf import rrf_fusion
from app.domain.retrieval.normalizer import normalize_text
from app.domain.retrieval.schemas import (
    CandidateRetrievalResult,
    MemoryCandidate,
    PendingMemoryNote,
)
from app.domain.retrieval.sub_retrievers import (
    build_context,
    content_keyword_retrieve,
    entity_retrieve,
    evidence_intent_retrieve,
    scene_context_retrieve,
    subject_tag_retrieve,
    memory_kind_intent_retrieve,
)

RRF_WEIGHTS = [
    "rrf_entity_weight",
    "rrf_subject_tag_weight",
    "rrf_content_keyword_weight",
    "rrf_memory_kind_intent_weight",
    "rrf_scene_context_weight",
    "rrf_evidence_intent_weight",
]


class CandidateRetriever:
    def __init__(
        self,
        memory_store: MemoryStore,
        config: RetrievalConfig | None = None,
        alias_store=None,
        pending_memory_store: PendingMemoryStore | None = None,
    ):
        self.memory_store = memory_store
        self.pending_memory_store = pending_memory_store
        self.config = config or RetrievalConfig()
        if alias_store is not None:
            self.alias_store = alias_store
            self._owns_alias_store = False
        else:
            self.alias_store = self._load_default_alias_store()
            self._owns_alias_store = self.alias_store is not None

    @staticmethod
    def _load_default_alias_store():
        repo_root = Path(__file__).resolve().parents[3]
        db_path = repo_root / "data" / "aliases.db"
        if not db_path.exists():
            return None
        try:
            return AliasStore(str(db_path))
        except Exception:
            return None

    def retrieve(
        self,
        parse_result: ActionParseResult,
        *,
        top_k: int | None = None,
    ) -> CandidateRetrievalResult:
        if top_k is None:
            top_k = self.config.top_k
        top_k = min(max(top_k, 1), 50)

        all_memories = self.memory_store.list_active()
        pending_notes = self._retrieve_pending_notes(parse_result, top_k=top_k)
        total_memories = len(all_memories)

        if total_memories == 0:
            pending_notes = pending_notes[:top_k]
            return CandidateRetrievalResult(
                candidates=[],
                pending_notes=pending_notes,
                total_scanned=len(pending_notes),
                total_returned=len(pending_notes),
            )

        anchors = parse_result.anchors
        if not anchors:
            pending_notes = pending_notes[:top_k]
            return CandidateRetrievalResult(
                candidates=[],
                pending_notes=pending_notes,
                total_scanned=total_memories + len(pending_notes),
                total_returned=len(pending_notes),
            )

        retrieval_result = self._run_turn_pipeline(
            all_memories,
            anchors,
            parse_result.atomic_actions,
        )
        candidates = self._build_candidates(
            all_memories,
            total_memories,
            retrieval_result,
            parse_result.atomic_actions,
            top_k,
        )
        candidates, pending_notes = _limit_combined_results(
            candidates,
            pending_notes,
            top_k=top_k,
        )

        return CandidateRetrievalResult(
            candidates=candidates,
            pending_notes=pending_notes,
            total_scanned=total_memories + len(pending_notes),
            total_returned=len(candidates) + len(pending_notes),
        )

    def close(self) -> None:
        if self._owns_alias_store and self.alias_store is not None:
            self.alias_store.close()
            self.alias_store = None
            self._owns_alias_store = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _retrieve_pending_notes(
        self,
        parse_result: ActionParseResult,
        *,
        top_k: int,
    ) -> list[PendingMemoryNote]:
        if self.pending_memory_store is None:
            return []
        pending_items = self.pending_memory_store.list_recent_unclassified(limit=50)
        if not pending_items:
            return []

        anchors = parse_result.anchors
        action_ids = [action.action_id for action in parse_result.atomic_actions]
        scored: list[tuple[float, PendingMemoryNote]] = []
        for item in pending_items:
            norm_text = normalize_text(
                " ".join(
                    [
                        item.raw_text,
                        " ".join(item.source_text_span),
                        " ".join(item.entities),
                    ]
                )
            )
            matched_anchors: set[str] = set()
            score = 0.0

            for anchor in anchors:
                term = normalize_text(anchor.normalized or anchor.text)
                if not term:
                    continue
                if term in norm_text:
                    matched_anchors.add(anchor.text)
                    score += 1.0

            if score <= 0:
                continue

            if item.importance_hint == "high":
                score += 0.4
            elif item.importance_hint == "medium":
                score += 0.2

            scored.append(
                (
                    score,
                    PendingMemoryNote(
                        pending_id=item.id,
                        scene_id=item.scene_id,
                        turn_index=item.turn_index,
                        raw_text=item.raw_text,
                        source_text_span=item.source_text_span,
                        entities=item.entities,
                        location=item.location,
                        time_label=item.time_label,
                        memory_kind=item.memory_kind,
                        subject=item.subject,
                        owner=item.owner,
                        importance_hint=item.importance_hint,
                        score=score,
                        matched_anchors=sorted(matched_anchors),
                        matched_actions=action_ids,
                    ),
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        return [note for _score, note in scored[: min(max(top_k, 1), 20)]]

    def _run_turn_pipeline(self, all_memories, anchors, actions) -> dict:
        ctx = build_context(all_memories, anchors, actions, self.alias_store)

        entity_scores = entity_retrieve(ctx, self.config)
        subject_tag_scores = subject_tag_retrieve(ctx, self.config)
        content_scores = content_keyword_retrieve(ctx, self.config)
        memory_kind_intent_scores = memory_kind_intent_retrieve(
            ctx, entity_scores, subject_tag_scores, content_scores, self.config
        )
        scene_context_scores = scene_context_retrieve(
            ctx, entity_scores, subject_tag_scores, content_scores, self.config
        )
        evidence_intent_scores = evidence_intent_retrieve(
            ctx, entity_scores, subject_tag_scores, content_scores, self.config
        )

        rrf_scores = rrf_fusion(
            [
                entity_scores,
                subject_tag_scores,
                content_scores,
                memory_kind_intent_scores,
                scene_context_scores,
                evidence_intent_scores,
            ],
            [
                self.config.rrf_entity_weight,
                self.config.rrf_subject_tag_weight,
                self.config.rrf_content_keyword_weight,
                self.config.rrf_memory_kind_intent_weight,
                self.config.rrf_scene_context_weight,
                self.config.rrf_evidence_intent_weight,
            ],
            k=self.config.rrf_k,
        )

        return {
            "entity_scores": entity_scores,
            "subject_tag_scores": subject_tag_scores,
            "content_scores": content_scores,
            "memory_kind_intent_scores": memory_kind_intent_scores,
            "scene_context_scores": scene_context_scores,
            "evidence_intent_scores": evidence_intent_scores,
            "rrf_scores": rrf_scores,
            "entity_ranks": self._compute_ranks(entity_scores),
            "subject_tag_ranks": self._compute_ranks(subject_tag_scores),
            "content_ranks": self._compute_ranks(content_scores),
            "memory_kind_intent_ranks": self._compute_ranks(memory_kind_intent_scores),
            "scene_context_ranks": self._compute_ranks(scene_context_scores),
            "evidence_intent_ranks": self._compute_ranks(evidence_intent_scores),
            "match_info": self._compute_match_info(ctx),
        }

    def _build_candidates(
        self, all_memories, total_memories, result, actions, top_k
    ) -> list[MemoryCandidate]:
        candidates: list = []
        action_ids = [action.action_id for action in actions]
        for mem in all_memories:
            mid = mem.id
            rrf = result["rrf_scores"].get(mid, 0.0)
            mi = result["match_info"].get(mid, {})
            matched_anchors = mi.get("matched_anchor_texts", set())
            matched_kinds = mi.get("matched_anchor_kinds", [])

            if not matched_anchors:
                continue
            if rrf <= 0:
                continue
            if all(k in LOW_WEIGHT_ANCHOR_KINDS for k in matched_kinds):
                continue

            score_data = {
                "entity_score": result["entity_scores"].get(mid, 0.0),
                "entity_rank": result["entity_ranks"].get(mid, 0),
                "subject_tag_score": result["subject_tag_scores"].get(mid, 0.0),
                "subject_tag_rank": result["subject_tag_ranks"].get(mid, 0),
                "content_score": result["content_scores"].get(mid, 0.0),
                "content_rank": result["content_ranks"].get(mid, 0),
                "memory_kind_intent_score": result["memory_kind_intent_scores"].get(mid, 0.0),
                "memory_kind_intent_rank": result["memory_kind_intent_ranks"].get(mid, 0),
                "scene_context_score": result["scene_context_scores"].get(mid, 0.0),
                "scene_context_rank": result["scene_context_ranks"].get(mid, 0),
                "evidence_intent_score": result["evidence_intent_scores"].get(mid, 0.0),
                "evidence_intent_rank": result["evidence_intent_ranks"].get(mid, 0),
                "rrf_score": rrf,
                "total_memories": total_memories,
            }

            candidates.append((rrf, mem, score_data, matched_anchors, action_ids))

        candidates.sort(key=lambda x: x[0], reverse=True)
        if len(candidates) > top_k:
            candidates = candidates[:top_k]

        results: list[MemoryCandidate] = []
        for final_score, mem, breakdown, matched_anchors, action_ids in candidates:
            results.append(
                MemoryCandidate(
                    memory_id=mem.id,
                    memory_kind=mem.memory_kind,
                    evidence_mode=mem.evidence_mode,
                    subject=mem.subject,
                    content=mem.content,
                    entities=list(mem.entities),
                    tags=list(mem.tags),
                    location=mem.location,
                    time_label=mem.time_label,
                    visibility=list(mem.visibility),
                    owner=mem.owner,
                    confidence=mem.confidence,
                    salience=mem.salience,
                    source_scene_id=mem.source_scene_id,
                    source_text_span=list(mem.source_text_span),
                    source_pending_ids=list(mem.source_pending_ids),
                    score=final_score,
                    score_breakdown=breakdown,
                    matched_anchors=sorted(matched_anchors),
                    matched_actions=action_ids,
                )
            )
        return results

    @staticmethod
    def _compute_ranks(scores: dict[str, float]) -> dict[str, int]:
        ranks: dict[str, int] = {}
        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        rank = 0
        prev_score = None
        tie_count = 0

        for memory_id, score in sorted_items:
            if score <= 0:
                break
            if prev_score is None or score < prev_score:
                rank = rank + 1 + tie_count
                tie_count = 0
            else:
                tie_count += 1
            prev_score = score
            ranks[memory_id] = rank

        return ranks

    @staticmethod
    def _compute_match_info(ctx) -> dict:
        info: dict = {}
        for mem in ctx.memories:
            mid = mem.id
            matched_texts: set[str] = set()
            matched_kinds: list[str] = []

            entities = ctx.norm_entities.get(mid, [])
            tags = ctx.norm_tags.get(mid, [])
            norm_subject = ctx.norm_subjects.get(mid, "")
            norm_content = ctx.norm_contents.get(mid, "")

            for i, anchor in enumerate(ctx.anchors):
                expansions = ctx.anchor_expansions[i] if i < len(ctx.anchor_expansions) else [(ctx.norm_anchors[i], 1.0)]
                matched = False

                for term, _term_conf in expansions:
                    for norm_entity in entities:
                        if term == norm_entity or term in norm_entity or norm_entity in term:
                            matched = True
                            break
                    if matched:
                        break

                    for norm_tag in tags:
                        if term == norm_tag or term in norm_tag or norm_tag in term:
                            matched = True
                            break
                    if matched:
                        break

                    if norm_subject:
                        if term == norm_subject or term in norm_subject or norm_subject in term:
                            matched = True
                            break

                    if norm_content:
                        if term in norm_content:
                            matched = True
                            break

                if matched:
                    matched_texts.add(anchor.text)
                    matched_kinds.append(anchor.kind)

            info[mid] = {
                "matched_anchor_texts": matched_texts,
                "matched_anchor_kinds": matched_kinds,
            }

        return info


def _limit_combined_results(
    candidates: list[MemoryCandidate],
    pending_notes: list[PendingMemoryNote],
    *,
    top_k: int,
) -> tuple[list[MemoryCandidate], list[PendingMemoryNote]]:
    selected_candidates: list[MemoryCandidate] = []
    selected_pending: list[PendingMemoryNote] = []
    candidate_index = 0
    pending_index = 0

    while len(selected_candidates) + len(selected_pending) < top_k:
        added = False
        if candidate_index < len(candidates):
            selected_candidates.append(candidates[candidate_index])
            candidate_index += 1
            added = True
        if (
            len(selected_candidates) + len(selected_pending) < top_k
            and pending_index < len(pending_notes)
        ):
            selected_pending.append(pending_notes[pending_index])
            pending_index += 1
            added = True
        if not added:
            break

    return selected_candidates, selected_pending
