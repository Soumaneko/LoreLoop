from collections import defaultdict
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
    layer_intent_retrieve,
    scene_context_retrieve,
    subject_tag_retrieve,
    type_intent_retrieve,
)

RRF_WEIGHTS = [
    "rrf_entity_weight",
    "rrf_subject_tag_weight",
    "rrf_content_keyword_weight",
    "rrf_type_intent_weight",
    "rrf_scene_context_weight",
    "rrf_layer_intent_weight",
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
        else:
            self.alias_store = self._load_default_alias_store()

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
        all_memories = self.memory_store.list_all()
        pending_notes = self._retrieve_pending_notes(parse_result, top_k=top_k or self.config.top_k)
        total_memories = len(all_memories)
        if top_k is None:
            top_k = self.config.top_k
        top_k = min(max(top_k, 1), 50)

        if total_memories == 0:
            return CandidateRetrievalResult(
                candidates=[],
                pending_notes=pending_notes,
                total_scanned=len(pending_notes),
                total_returned=len(pending_notes),
            )

        actions = parse_result.atomic_actions
        if not actions:
            return CandidateRetrievalResult(
                candidates=[],
                pending_notes=pending_notes,
                total_scanned=total_memories + len(pending_notes),
                total_returned=len(pending_notes),
            )

        action_anchors_map = self._group_anchors_by_action(parse_result.anchors, actions)

        per_action_results: dict[str, dict] = {}
        for action in actions:
            action_anchors = action_anchors_map.get(action.action_id, [])
            if not action_anchors:
                continue
            per_action_results[action.action_id] = self._run_action_pipeline(
                all_memories, action_anchors, [action]
            )

        if not per_action_results:
            return CandidateRetrievalResult(
                candidates=[],
                pending_notes=pending_notes,
                total_scanned=total_memories + len(pending_notes),
                total_returned=len(pending_notes),
            )

        candidates = self._merge_and_build_candidates(
            all_memories, total_memories, per_action_results, top_k
        )

        return CandidateRetrievalResult(
            candidates=candidates,
            pending_notes=pending_notes,
            total_scanned=total_memories + len(pending_notes),
            total_returned=len(candidates) + len(pending_notes),
        )

    def _retrieve_pending_notes(
        self,
        parse_result: ActionParseResult,
        *,
        top_k: int,
    ) -> list[PendingMemoryNote]:
        if self.pending_memory_store is None:
            return []
        actions = parse_result.atomic_actions
        if not actions:
            return []

        pending_items = self.pending_memory_store.list_recent_unclassified(limit=50)
        if not pending_items:
            return []

        anchors = parse_result.anchors
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
            matched_actions: set[str] = set()
            score = 0.0

            for anchor in anchors:
                term = normalize_text(anchor.normalized or anchor.text)
                if not term:
                    continue
                if term in norm_text:
                    matched_anchors.add(anchor.text)
                    matched_actions.update(anchor.action_ids)
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
                        extractor_hint=item.extractor_hint,
                        importance_hint=item.importance_hint,
                        score=score,
                        matched_anchors=sorted(matched_anchors),
                        matched_actions=sorted(
                            aid
                            for aid in matched_actions
                            if any(action.action_id == aid for action in actions)
                        ),
                    ),
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        return [note for _score, note in scored[: min(max(top_k, 1), 20)]]

    def _run_action_pipeline(self, all_memories, anchors, actions) -> dict:
        ctx = build_context(all_memories, anchors, actions, self.alias_store)

        entity_scores = entity_retrieve(ctx, self.config)
        subject_tag_scores = subject_tag_retrieve(ctx, self.config)
        content_scores = content_keyword_retrieve(ctx, self.config)
        type_intent_scores = type_intent_retrieve(
            ctx, entity_scores, subject_tag_scores, content_scores, self.config
        )
        scene_context_scores = scene_context_retrieve(
            ctx, entity_scores, subject_tag_scores, content_scores, self.config
        )
        layer_intent_scores = layer_intent_retrieve(
            ctx, entity_scores, subject_tag_scores, content_scores, self.config
        )

        rrf_scores = rrf_fusion(
            [
                entity_scores,
                subject_tag_scores,
                content_scores,
                type_intent_scores,
                scene_context_scores,
                layer_intent_scores,
            ],
            [
                self.config.rrf_entity_weight,
                self.config.rrf_subject_tag_weight,
                self.config.rrf_content_keyword_weight,
                self.config.rrf_type_intent_weight,
                self.config.rrf_scene_context_weight,
                self.config.rrf_layer_intent_weight,
            ],
            k=self.config.rrf_k,
        )

        return {
            "entity_scores": entity_scores,
            "subject_tag_scores": subject_tag_scores,
            "content_scores": content_scores,
            "type_intent_scores": type_intent_scores,
            "scene_context_scores": scene_context_scores,
            "layer_intent_scores": layer_intent_scores,
            "rrf_scores": rrf_scores,
            "entity_ranks": self._compute_ranks(entity_scores),
            "subject_tag_ranks": self._compute_ranks(subject_tag_scores),
            "content_ranks": self._compute_ranks(content_scores),
            "type_intent_ranks": self._compute_ranks(type_intent_scores),
            "scene_context_ranks": self._compute_ranks(scene_context_scores),
            "layer_intent_ranks": self._compute_ranks(layer_intent_scores),
            "match_info": self._compute_match_info(ctx),
        }

    def _merge_and_build_candidates(
        self, all_memories, total_memories, per_action_results, top_k
    ) -> list[MemoryCandidate]:
        mid_action_hits: dict[str, list[str]] = defaultdict(list)
        mid_scores: dict[str, dict] = {}

        for action_id, result in per_action_results.items():
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

                mid_action_hits[mid].append(action_id)

                if mid not in mid_scores:
                    mid_scores[mid] = {
                        "max_entity_score": 0.0, "max_entity_rank": 0,
                        "max_subject_tag_score": 0.0, "max_subject_tag_rank": 0,
                        "max_content_score": 0.0, "max_content_rank": 0,
                        "max_type_intent_score": 0.0, "max_type_intent_rank": 0,
                        "max_scene_context_score": 0.0, "max_scene_context_rank": 0,
                        "max_layer_intent_score": 0.0, "max_layer_intent_rank": 0,
                        "max_rrf": 0.0,
                        "all_matched_anchors": set(),
                        "all_matched_actions": set(),
                    }

                ms = mid_scores[mid]
                ms["max_rrf"] = max(ms["max_rrf"], rrf)
                ms["all_matched_anchors"].update(matched_anchors)
                ms["all_matched_actions"].add(action_id)

                for field, score_key, rank_key in [
                    ("max_entity_score", "entity_scores", "entity_ranks"),
                    ("max_subject_tag_score", "subject_tag_scores", "subject_tag_ranks"),
                    ("max_content_score", "content_scores", "content_ranks"),
                    ("max_type_intent_score", "type_intent_scores", "type_intent_ranks"),
                    ("max_scene_context_score", "scene_context_scores", "scene_context_ranks"),
                    ("max_layer_intent_score", "layer_intent_scores", "layer_intent_ranks"),
                ]:
                    s = result[score_key].get(mid, 0.0)
                    r = result[rank_key].get(mid, 0)
                    if s > ms[field]:
                        ms[field] = s
                        ms[field.replace("_score", "_rank")] = r

        candidates: list = []
        for mem in all_memories:
            mid = mem.id
            if mid not in mid_action_hits:
                continue

            ms = mid_scores[mid]
            n_actions = len(mid_action_hits[mid])
            bonus = (n_actions - 1) * self.config.multi_action_bonus
            final_score = ms["max_rrf"] + bonus

            candidates.append((final_score, mem, ms, n_actions, mid_action_hits[mid]))

        candidates.sort(key=lambda x: x[0], reverse=True)
        if len(candidates) > top_k:
            candidates = candidates[:top_k]

        results: list[MemoryCandidate] = []
        for final_score, mem, ms, n_actions, action_ids in candidates:
            breakdown = {
                "entity_score": ms["max_entity_score"],
                "entity_rank": ms["max_entity_rank"],
                "subject_tag_score": ms["max_subject_tag_score"],
                "subject_tag_rank": ms["max_subject_tag_rank"],
                "content_score": ms["max_content_score"],
                "content_rank": ms["max_content_rank"],
                "type_intent_score": ms["max_type_intent_score"],
                "type_intent_rank": ms["max_type_intent_rank"],
                "scene_context_score": ms["max_scene_context_score"],
                "scene_context_rank": ms["max_scene_context_rank"],
                "layer_intent_score": ms["max_layer_intent_score"],
                "layer_intent_rank": ms["max_layer_intent_rank"],
                "rrf_score": ms["max_rrf"],
                "max_rrf": ms["max_rrf"],
                "multi_action_bonus": final_score - ms["max_rrf"],
                "num_actions_hit": n_actions,
                "total_memories": total_memories,
            }
            results.append(
                MemoryCandidate(
                    memory_id=mem.id,
                    memory_type=mem.type,
                    layer=mem.layer,
                    subtype=mem.subtype,
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
                    certainty=mem.certainty,
                    source_scene_id=mem.source_scene_id,
                    source_text_span=list(mem.source_text_span),
                    source_pending_ids=list(mem.source_pending_ids),
                    score=final_score,
                    score_breakdown=breakdown,
                    matched_anchors=sorted(ms["all_matched_anchors"]),
                    matched_actions=sorted(ms["all_matched_actions"]),
                )
            )
        return results

    @staticmethod
    def _group_anchors_by_action(anchors: list[Anchor], actions: list[AtomicAction]) -> dict[str, list[Anchor]]:
        action_ids = {a.action_id for a in actions}
        grouped: dict[str, list[Anchor]] = defaultdict(list)
        for anchor in anchors:
            for aid in anchor.action_ids:
                if aid in action_ids:
                    grouped[aid].append(anchor)
        return dict(grouped)

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
            matched_actions: set[str] = set()
            per_action_hits: dict[str, int] = {}

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
                    for aid in anchor.action_ids:
                        if aid in ctx.action_ids_set:
                            matched_actions.add(aid)
                            per_action_hits[aid] = per_action_hits.get(aid, 0) + 1

            info[mid] = {
                "matched_anchor_texts": matched_texts,
                "matched_anchor_kinds": matched_kinds,
                "matched_action_ids": matched_actions,
                "per_action_hits": per_action_hits,
            }

        return info
