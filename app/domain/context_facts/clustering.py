from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


SUPPORTING_EVIDENCE_MAX = 4
NEGATED_FRAGMENT_LENGTH = 4
NEGATION_TOKENS = frozenset({
    "不", "未", "没", "无", "非", "别", "莫",
    "没有", "不是", "不再", "毫无",
})
CHINESE_NUMERAL_VALUES = {
    "零": "0",
    "一": "1",
    "两": "2",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
    "百": "100",
    "千": "1000",
    "万": "10000",
    "半": "0.5",
}
TIME_MARKER_TOKENS = frozenset({
    "今晨", "今午", "今宵", "清晨", "正午", "黄昏", "入夜",
    "子时", "丑时", "寅时", "卯时", "辰时", "巳时",
    "午时", "未时", "申时", "酉时", "戌时", "亥时",
    "今日", "昨日", "明日", "刻",
})
STATE_PREDICATE_TOKENS = frozenset({
    "持有", "交出", "获得", "失去", "存活", "死亡", "拥有",
    "归还", "借出", "落入", "出现", "消失", "复原", "摧毁",
    "修复", "离开", "返回", "拿着", "交给",
})
OWNERSHIP_TOKENS = frozenset({
    "归属", "归于", "属于", "持有", "拥有", "落入",
})
_NUMBER_RE = re.compile(r"\d+")
_NORMALIZE_RE = re.compile(r"[^\w\u4e00-\u9fff]")


def cluster_near_duplicates(candidates: list[Any]) -> list[list[Any]]:
    """Greedily group identical scene paragraphs into conservative clusters.

    Merging is deliberately the most conservative possible to maximise recall:
    two candidates merge only when they share the same turn and scene, are
    directly adjacent paragraphs, have exactly identical normalized text (only
    whitespace, punctuation, and case may differ), match exactly the same
    non-empty entity set, overlap on topics, and show no semantic conflict.
    Candidates are bucketed by (turn, scene) so only paragraph neighbours are
    compared.
    """
    clusters: list[list[Any]] = []
    clusters_by_scene: dict[tuple[int, str], list[list[Any]]] = defaultdict(list)

    ordered = sorted(
        candidates,
        key=lambda candidate: (
            candidate.score,
            len(candidate.matched_intent_ids),
            candidate.document.paragraph_index or 0,
        ),
        reverse=True,
    )
    for candidate in ordered:
        scene_key = (
            candidate.document.turn_index,
            candidate.document.scene_id,
        )
        joined = False
        for cluster in clusters_by_scene.get(scene_key, []):
            if _is_near_duplicate(cluster[0], candidate):
                cluster.append(candidate)
                joined = True
                break
        if not joined:
            cluster = [candidate]
            clusters.append(cluster)
            clusters_by_scene[scene_key].append(cluster)
    return clusters


def merge_conflict(a_text: str, b_text: str) -> bool:
    """Refuse merging when semantic signals clearly differ.

    Fail-safe defense-in-depth: identical normalized text passes every category,
    but any divergence in negation content, quantity, time, state predicate, or
    ownership refuses the merge.
    """
    a_negation = _negation_tokens(a_text)
    b_negation = _negation_tokens(b_text)
    if bool(a_negation) != bool(b_negation):
        return True
    if (
        a_negation
        and b_negation
        and _negated_fragments(a_text) != _negated_fragments(b_text)
    ):
        return True
    a_numbers = _number_tokens(a_text)
    b_numbers = _number_tokens(b_text)
    if bool(a_numbers) != bool(b_numbers):
        return True
    if a_numbers and b_numbers and a_numbers != b_numbers:
        return True
    if _marker_tokens(TIME_MARKER_TOKENS, a_text) != _marker_tokens(
        TIME_MARKER_TOKENS,
        b_text,
    ):
        return True
    a_predicates = _marker_tokens(STATE_PREDICATE_TOKENS, a_text)
    b_predicates = _marker_tokens(STATE_PREDICATE_TOKENS, b_text)
    if a_predicates and b_predicates and a_predicates != b_predicates:
        return True
    a_owned = _marker_tokens(OWNERSHIP_TOKENS, a_text)
    b_owned = _marker_tokens(OWNERSHIP_TOKENS, b_text)
    if a_owned and b_owned and a_owned != b_owned:
        return True
    return False


def _is_near_duplicate(a: Any, b: Any) -> bool:
    if a.document.turn_index != b.document.turn_index:
        return False
    if a.document.scene_id != b.document.scene_id:
        return False
    a_paragraph = a.document.paragraph_index
    b_paragraph = b.document.paragraph_index
    if a_paragraph is None or b_paragraph is None:
        return False
    if abs(a_paragraph - b_paragraph) != 1:
        return False
    if _normalized_text(a.document.text) != _normalized_text(b.document.text):
        return False
    if not _shares_entities(a, b):
        return False
    a_topics = set(a.matched_topics)
    b_topics = set(b.matched_topics)
    if a_topics and b_topics and not (a_topics & b_topics):
        return False
    if merge_conflict(a.document.text, b.document.text):
        return False
    return True


def _shares_entities(a: Any, b: Any) -> bool:
    a_entities = set(a.matched_entities)
    b_entities = set(b.matched_entities)
    if not a_entities or not b_entities:
        return False
    return a_entities == b_entities


def _normalized_text(text: str) -> str:
    return _NORMALIZE_RE.sub("", text.casefold())


def _negation_tokens(text: str) -> set[str]:
    return {token for token in NEGATION_TOKENS if token in text}


def _negated_fragments(text: str) -> list[str]:
    fragments = []
    for token in NEGATION_TOKENS:
        start = 0
        while True:
            index = text.find(token, start)
            if index == -1:
                break
            fragments.append(
                text[index + len(token): index + len(token) + NEGATED_FRAGMENT_LENGTH]
            )
            start = index + 1
    return sorted(set(fragments))


def _number_tokens(text: str) -> set[str]:
    numbers = set(_NUMBER_RE.findall(text))
    for char in text:
        if char in CHINESE_NUMERAL_VALUES:
            numbers.add(CHINESE_NUMERAL_VALUES[char])
    return numbers


def _marker_tokens(tokens: frozenset[str], text: str) -> set[str]:
    return {token for token in tokens if token in text}
