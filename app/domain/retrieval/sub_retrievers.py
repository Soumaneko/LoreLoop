import math
from dataclasses import dataclass, field

from app.domain.action.schemas import Anchor, AtomicAction
from app.domain.recall_memory.schemas import MemoryRecord
from app.domain.retrieval.config import (
    ACTION_TYPE_MEMORY_KIND_BOOST,
    IDF_CLAMP_MAX,
    IDF_CLAMP_MIN,
    KIND_FACTOR,
    PAST_REF_KEYWORDS,
    PAST_REF_MEMORY_KIND_BOOST,
    RetrievalConfig,
)
from app.domain.retrieval.normalizer import normalize_text
from app.domain.retrieval.alias_store import AliasStore


@dataclass
class RetrievalContext:
    memories: list[MemoryRecord]
    total_memories: int

    norm_entities: dict[str, list[str]] = field(default_factory=dict)
    norm_tags: dict[str, list[str]] = field(default_factory=dict)
    norm_subjects: dict[str, str] = field(default_factory=dict)
    norm_contents: dict[str, str] = field(default_factory=dict)

    anchors: list[Anchor] = field(default_factory=list)
    norm_anchors: list[str] = field(default_factory=list)
    anchor_weights: list[float] = field(default_factory=list)
    anchor_kinds: list[str] = field(default_factory=list)
    anchor_expansions: list[list[tuple[str, float]]] = field(default_factory=list)

    atomic_actions: list[AtomicAction] = field(default_factory=list)


def build_context(
    memories: list[MemoryRecord],
    anchors: list[Anchor],
    atomic_actions: list[AtomicAction],
    alias_store: AliasStore | None = None,
) -> RetrievalContext:
    norm_entities: dict[str, list[str]] = {}
    norm_tags: dict[str, list[str]] = {}
    norm_subjects: dict[str, str] = {}
    norm_contents: dict[str, str] = {}

    for mem in memories:
        norm_entities[mem.id] = [normalize_text(e) for e in mem.entities]
        norm_tags[mem.id] = [normalize_text(t) for t in mem.tags]
        norm_subjects[mem.id] = normalize_text(mem.subject)
        norm_contents[mem.id] = normalize_text(mem.content)

    norm_anchors = [normalize_text(a.normalized) for a in anchors]
    anchor_kinds = [a.kind for a in anchors]

    N = len(memories)
    anchor_weights: list[float] = []
    anchor_expansions: list[list[tuple[str, float]]] = []
    for i, anchor in enumerate(anchors):
        na = norm_anchors[i]
        df = _compute_anchor_df(na, norm_entities, norm_tags, norm_subjects, norm_contents)
        idf = math.log((N + 0.5) / (df + 0.5))
        idf = min(max(idf, IDF_CLAMP_MIN), IDF_CLAMP_MAX)
        kind_factor = KIND_FACTOR.get(anchor.kind, 0.5)
        anchor_weights.append(idf * kind_factor)

        if alias_store:
            raw_expansions = alias_store.expand(anchor.normalized or anchor.text)
            seen: set[str] = set()
            expansions = []
            for t, c in raw_expansions:
                nt = normalize_text(t)
                if nt and nt not in seen:
                    seen.add(nt)
                    expansions.append((nt, c))
            if not expansions:
                expansions = [(na, 1.0)]
        else:
            expansions = [(na, 1.0)]
        anchor_expansions.append(expansions)

    return RetrievalContext(
        memories=memories,
        total_memories=N,
        norm_entities=norm_entities,
        norm_tags=norm_tags,
        norm_subjects=norm_subjects,
        norm_contents=norm_contents,
        anchors=anchors,
        norm_anchors=norm_anchors,
        anchor_weights=anchor_weights,
        anchor_kinds=anchor_kinds,
        anchor_expansions=anchor_expansions,
        atomic_actions=atomic_actions,
    )


def _compute_anchor_df(
    norm_anchor: str,
    norm_entities: dict[str, list[str]],
    norm_tags: dict[str, list[str]],
    norm_subjects: dict[str, str],
    norm_contents: dict[str, str],
) -> int:
    df = 0
    memory_ids = norm_entities.keys()
    for mid in memory_ids:
        matched = False

        for entity in norm_entities.get(mid, []):
            if norm_anchor == entity or norm_anchor in entity or entity in norm_anchor:
                matched = True
                break

        if not matched:
            for tag in norm_tags.get(mid, []):
                if norm_anchor == tag or norm_anchor in tag or tag in norm_anchor:
                    matched = True
                    break

        if not matched:
            subject = norm_subjects.get(mid, "")
            if subject and (norm_anchor == subject or norm_anchor in subject or subject in norm_anchor):
                matched = True

        if not matched:
            content = norm_contents.get(mid, "")
            if content and norm_anchor in content:
                matched = True

        if matched:
            df += 1

    return df


def entity_retrieve(ctx: RetrievalContext, config: RetrievalConfig | None = None) -> dict[str, float]:
    scores: dict[str, float] = {}
    for mem in ctx.memories:
        score = 0.0
        entities = ctx.norm_entities.get(mem.id, [])
        for i, expansions in enumerate(ctx.anchor_expansions):
            weight = ctx.anchor_weights[i]
            for term, term_conf in expansions:
                matched = False
                for norm_entity in entities:
                    if term == norm_entity:
                        score += 3.0 * weight * term_conf
                        matched = True
                        break
                    elif term in norm_entity or norm_entity in term:
                        score += 1.8 * weight * term_conf
                        matched = True
                        break
                if matched:
                    break
        scores[mem.id] = score
    return scores


def subject_tag_retrieve(ctx: RetrievalContext, config: RetrievalConfig | None = None) -> dict[str, float]:
    scores: dict[str, float] = {}
    for mem in ctx.memories:
        score = 0.0
        norm_subject = ctx.norm_subjects.get(mem.id, "")
        tags = ctx.norm_tags.get(mem.id, [])

        for i, expansions in enumerate(ctx.anchor_expansions):
            weight = ctx.anchor_weights[i]
            matched = False
            for term, term_conf in expansions:
                if norm_subject:
                    if term == norm_subject or term in norm_subject or norm_subject in term:
                        score += 2.5 * weight * term_conf
                        matched = True
                        break

                for norm_tag in tags:
                    if term == norm_tag:
                        score += 2.0 * weight * term_conf
                        matched = True
                        break
                    elif term in norm_tag or norm_tag in term:
                        score += 1.0 * weight * term_conf
                        matched = True
                        break
                if matched:
                    break
        scores[mem.id] = score
    return scores


def content_keyword_retrieve(ctx: RetrievalContext, config: RetrievalConfig | None = None) -> dict[str, float]:
    scores: dict[str, float] = {}
    for mem in ctx.memories:
        score = 0.0
        norm_content = ctx.norm_contents.get(mem.id, "")

        for i, expansions in enumerate(ctx.anchor_expansions):
            weight = ctx.anchor_weights[i]
            for term, term_conf in expansions:
                if term in norm_content:
                    score += 1.0 * weight * term_conf
                    break
        scores[mem.id] = score
    return scores


def evidence_intent_retrieve(
    ctx: RetrievalContext,
    entity_scores: dict[str, float],
    subject_tag_scores: dict[str, float],
    content_scores: dict[str, float],
    config: RetrievalConfig | None = None,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for mem in ctx.memories:
        has_match = (
            entity_scores.get(mem.id, 0.0) > 0
            or subject_tag_scores.get(mem.id, 0.0) > 0
            or content_scores.get(mem.id, 0.0) > 0
        )
        if not has_match:
            scores[mem.id] = 0.0
            continue

        score = 0.0
        for action in ctx.atomic_actions:
            score += ACTION_TYPE_MEMORY_KIND_BOOST.get(
                action.action_type, {}
            ).get(mem.memory_kind, 0.0)

        salience = getattr(mem, "salience", 0.5)

        score *= 0.75 + float(mem.confidence) * 0.25
        score *= 0.75 + float(salience) * 0.25
        scores[mem.id] = score
    return scores


def memory_kind_intent_retrieve(
    ctx: RetrievalContext,
    entity_scores: dict[str, float],
    subject_tag_scores: dict[str, float],
    content_scores: dict[str, float],
    config: RetrievalConfig | None = None,
) -> dict[str, float]:
    has_past_ref = False
    for action in ctx.atomic_actions:
        for kw in PAST_REF_KEYWORDS:
            if kw in action.raw_text:
                has_past_ref = True
                break
        if has_past_ref:
            break
    if not has_past_ref:
        for anchor in ctx.anchors:
            for kw in PAST_REF_KEYWORDS:
                if kw in anchor.text or kw in anchor.normalized:
                    has_past_ref = True
                    break
            if has_past_ref:
                break

    entity_anchor_kinds = {"character", "item", "organization"}

    scores: dict[str, float] = {}
    for mem in ctx.memories:
        score = 0.0
        has_match = (
            entity_scores.get(mem.id, 0.0) > 0
            or subject_tag_scores.get(mem.id, 0.0) > 0
            or content_scores.get(mem.id, 0.0) > 0
        )
        if not has_match:
            scores[mem.id] = 0.0
            continue

        for action in ctx.atomic_actions:
            boost_map = ACTION_TYPE_MEMORY_KIND_BOOST.get(action.action_type, {})
            score += boost_map.get(mem.memory_kind, 0.0)

        if has_past_ref:
            score += PAST_REF_MEMORY_KIND_BOOST.get(mem.memory_kind, 0.0)
            if entity_scores.get(mem.id, 0.0) > 0 and any(
                a.kind in entity_anchor_kinds for a in ctx.anchors
            ):
                score += 2.0

        scores[mem.id] = score
    return scores


def scene_context_retrieve(
    ctx: RetrievalContext,
    entity_scores: dict[str, float],
    subject_tag_scores: dict[str, float],
    content_scores: dict[str, float],
    config: RetrievalConfig | None = None,
) -> dict[str, float]:
    cfg = config or RetrievalConfig()
    scores: dict[str, float] = {}
    for idx, mem in enumerate(ctx.memories):
        score = 0.0
        has_match = (
            entity_scores.get(mem.id, 0.0) > 0
            or subject_tag_scores.get(mem.id, 0.0) > 0
            or content_scores.get(mem.id, 0.0) > 0
        )
        if not has_match:
            scores[mem.id] = 0.0
            continue

        score += mem.salience * cfg.salience_weight
        score += mem.confidence * cfg.confidence_weight

        if idx < 5:
            score += 2.0
        elif idx < 20:
            score += 1.0

        scores[mem.id] = score
    return scores
