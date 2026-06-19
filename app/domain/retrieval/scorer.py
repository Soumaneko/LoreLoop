from app.domain.action.schemas import Anchor, AtomicAction
from app.domain.recall_memory.schemas import MemoryRecord
from app.domain.retrieval.config import (
    ACTION_TYPE_SUBTYPE_INTENT_BOOST,
    KIND_FACTOR,
    LOW_WEIGHT_ANCHOR_KINDS,
    PAST_REF_KEYWORDS,
    PAST_REF_SUBTYPE_BOOST,
    RetrievalConfig,
)
from app.domain.retrieval.normalizer import normalize_text


class MemoryScorer:
    def __init__(self, config: RetrievalConfig | None = None):
        self.config = config or RetrievalConfig()

    def score(
        self,
        memory: MemoryRecord,
        anchors: list[Anchor],
        atomic_actions: list[AtomicAction],
        memory_index: int,
        total_memories: int,
    ) -> tuple[float, dict[str, float], list[str], list[str]]:
        breakdown: dict[str, float] = {}
        matched_anchor_texts: set[str] = set()

        norm_entities = [normalize_text(e) for e in memory.entities]
        norm_tags = [normalize_text(t) for t in memory.tags]
        norm_subject = normalize_text(memory.subject)
        norm_content = normalize_text(memory.content)

        matched_anchor_kinds: list[str] = []

        for anchor in anchors:
            norm_anchor = normalize_text(anchor.normalized)
            weight = KIND_FACTOR.get(anchor.kind, 1.0)
            anchor_matched = False

            for norm_entity in norm_entities:
                if norm_anchor == norm_entity:
                    breakdown["entity_score"] = breakdown.get("entity_score", 0.0) + 3.0 * weight
                    anchor_matched = True
                    break
                elif norm_anchor in norm_entity or norm_entity in norm_anchor:
                    breakdown["entity_score"] = breakdown.get("entity_score", 0.0) + 1.8 * weight
                    anchor_matched = True
                    break

            if not anchor_matched:
                for norm_tag in norm_tags:
                    if norm_anchor == norm_tag:
                        breakdown["tag_score"] = breakdown.get("tag_score", 0.0) + 2.0 * weight
                        anchor_matched = True
                        break
                    elif norm_anchor in norm_tag or norm_tag in norm_anchor:
                        breakdown["tag_score"] = breakdown.get("tag_score", 0.0) + 1.0 * weight
                        anchor_matched = True
                        break

            if not anchor_matched and norm_subject:
                if norm_anchor == norm_subject or norm_anchor in norm_subject or norm_subject in norm_anchor:
                    breakdown["subject_score"] = breakdown.get("subject_score", 0.0) + 2.5 * weight
                    anchor_matched = True

            if not anchor_matched and norm_content:
                if norm_anchor in norm_content:
                    breakdown["content_score"] = breakdown.get("content_score", 0.0) + 1.0 * weight
                    anchor_matched = True

            if anchor_matched:
                matched_anchor_texts.add(anchor.text)
                matched_anchor_kinds.append(anchor.kind)

        breakdown.setdefault("entity_score", 0.0)
        breakdown.setdefault("tag_score", 0.0)
        breakdown.setdefault("subject_score", 0.0)
        breakdown.setdefault("content_score", 0.0)

        has_match = any(breakdown[k] > 0 for k in ["entity_score", "tag_score", "subject_score", "content_score"])

        subtype_boost = 0.0
        if has_match:
            for action in atomic_actions:
                boost_map = ACTION_TYPE_SUBTYPE_INTENT_BOOST.get(action.action_type, {})
                subtype_boost += boost_map.get(memory.subtype, 0.0)
        breakdown["subtype_boost"] = subtype_boost

        past_ref_boost = 0.0
        if has_match:
            past_ref_boost = self._compute_past_ref_boost(
                atomic_actions, anchors, memory, matched_anchor_texts
            )
        breakdown["past_reference_boost"] = past_ref_boost

        if has_match:
            breakdown["salience_boost"] = memory.salience * self.config.salience_weight
            breakdown["confidence_boost"] = memory.confidence * self.config.confidence_weight
            breakdown["recency_boost"] = self._compute_recency_boost(memory_index, total_memories)
        else:
            breakdown["salience_boost"] = 0.0
            breakdown["confidence_boost"] = 0.0
            breakdown["recency_boost"] = 0.0

        penalty = self._compute_penalty(
            breakdown, matched_anchor_kinds, memory
        )
        breakdown["penalty"] = -penalty

        total = sum(breakdown.values())
        return total, breakdown, list(matched_anchor_texts), [a.action_id for a in atomic_actions]

    def _compute_past_ref_boost(
        self,
        atomic_actions: list[AtomicAction],
        anchors: list[Anchor],
        memory: MemoryRecord,
        matched_anchor_texts: set[str],
    ) -> float:
        has_past_ref = False
        for action in atomic_actions:
            for kw in PAST_REF_KEYWORDS:
                if kw in action.raw_text:
                    has_past_ref = True
                    break
            if has_past_ref:
                break
        if not has_past_ref:
            for anchor in anchors:
                for kw in PAST_REF_KEYWORDS:
                    if kw in anchor.text or kw in anchor.normalized:
                        has_past_ref = True
                        break
                if has_past_ref:
                    break

        if not has_past_ref:
            return 0.0

        boost = PAST_REF_SUBTYPE_BOOST.get(memory.subtype, 0.0)

        if boost > 0 and matched_anchor_texts:
            entity_anchor_matched = any(
                a.kind in ("character", "item", "organization")
                and a.text in matched_anchor_texts
                for a in anchors
            )
            if entity_anchor_matched:
                boost += 2.0

        return boost

    def _compute_recency_boost(self, memory_index: int, total_memories: int) -> float:
        if memory_index < 5:
            return 2.0
        elif memory_index < 20:
            return 1.0
        return 0.0

    def _compute_penalty(
        self,
        breakdown: dict[str, float],
        matched_anchor_kinds: list[str],
        memory: MemoryRecord,
    ) -> float:
        penalty = 0.0

        if len(matched_anchor_kinds) == 1 and matched_anchor_kinds[0] in LOW_WEIGHT_ANCHOR_KINDS:
            penalty += self.config.low_overlap_penalty

        content_only = (
            breakdown.get("content_score", 0.0) > 0
            and breakdown.get("entity_score", 0.0) == 0
            and breakdown.get("tag_score", 0.0) == 0
            and breakdown.get("subject_score", 0.0) == 0
        )
        if content_only:
            penalty += self.config.generic_content_penalty

        match_count = len(matched_anchor_kinds)
        if len(memory.content) > 200 and match_count <= 1:
            penalty += self.config.excessive_length_penalty

        return penalty
