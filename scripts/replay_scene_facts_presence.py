"""Phase 1 — 真实 SceneFactExtractor presence focused replay（只读，不建 accepted baseline）。

用真实 gateway 对 pending last-scene 契约的 scene_text 逐条运行 ``SceneFactExtractor``，
检查 presence 分类是否命中 ``presence_expectations``；另外跑一条临时的「先提及后出场」
场景。产物写入 ``.scratch/npc-turn-processor/phase1-scene-facts-presence-report.json``，
供人工审查 LLM 的 presence 分类质量，不参与任何 ``--check`` / accepted baseline。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from app.core.llm.config import SharedGatewayFactory
from app.core.text import normalize_compact_key
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import (
    CurrentState,
    ParticipantState,
    PlayerState,
    SceneState,
)
from app.domain.scene_facts import SceneFactExtractor
from app.domain.scene_facts.schemas import (
    SceneFactExtractInput,
    SceneParagraphInput,
)
from app.runtime.model_config import ModelConfigStore
from scripts.benchmark_npc_baseline import load_fixtures

ROOT = Path(__file__).resolve().parents[1]
PENDING_FIXTURES = (
    ROOT / "benchmarks" / "npc_baseline" / "cases_last_scene_pending.json"
)
REPORT_PATH = (
    ROOT / ".scratch" / "npc-turn-processor"
    / "phase1-scene-facts-presence-report.json"
)
TRACE_PATH = (
    ROOT / ".scratch" / "npc-turn-processor"
    / "phase1-scene-facts-presence-trace.jsonl"
)

#: 临时「先提及后出场」场景：正文先被谈论、后实际出场，presence 合并应取 appeared。
ADHOC_SCENE = {
    "case_id": "adhoc_mentioned_then_appeared",
    "scene_id": "scene_adhoc",
    "turn_index": 40,
    "player_name": "萧欢",
    "scene_location": "议事厅",
    "participants": ["林舟"],
    "scene_text": (
        "众人都在说林舟今日会来，还说他已经应下此事。\n\n"
        "不久林舟推门而入，拱手道：「诸位久候。」"
    ),
    "expected": {"林舟": "appeared"},
    "description": "先提及后出场：正文先被谈论、后实际出现并发言，应合并为 appeared。",
}


def _split_paragraphs(scene_text: str) -> list[str]:
    return [
        paragraph
        for part in re.split(r"\n{2,}", scene_text.strip())
        if (paragraph := part.strip())
    ]


def _scene_paragraphs(scene_text: str) -> list[SceneParagraphInput]:
    return [
        SceneParagraphInput(
            document_id=f"turn_p{index:03d}",
            text=paragraph,
        )
        for index, paragraph in enumerate(_split_paragraphs(scene_text), start=1)
    ]


def _current_state(case: dict) -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id=case["scene_id"],
            location=case["scene_location"],
            participants=[
                ParticipantState(name=name) for name in case["participants"]
            ],
        ),
        player=PlayerState(name=case["player_name"]),
    )


def _core_memory(case: dict) -> CoreMemory:
    items = [
        item if isinstance(item, CoreMemoryItem) else CoreMemoryItem(**item)
        for item in case.get("core_memory_items", [])
    ]
    return CoreMemory(items=items)


def _input(case: dict) -> SceneFactExtractInput:
    return SceneFactExtractInput(
        scene_text=case["scene_text"],
        core_memory=_core_memory(case),
        current_state=_current_state(case),
        scene_paragraphs=_scene_paragraphs(case["scene_text"]),
    )


def _expected_groups(
    presence_expectations: dict[str, str],
    scene_facts: dict | None,
) -> dict[str, tuple[str, set[str]]]:
    """把 expected 组织为「人物组」：canonical + 合法 alias 视为同一人物同一 presence。

    一个组内任意 surface 命中即满足；避免模型选合法 alias 作 primary 时被误判缺失。
    """
    groups: dict[str, tuple[str, set[str]]] = {}
    for surface, presence in presence_expectations.items():
        key = normalize_compact_key(surface)
        if key:
            groups[key] = (presence, {key})
    for mention in (scene_facts or {}).get("npc_mentions", []):
        primary = normalize_compact_key(mention.get("primary_surface", ""))
        group = groups.get(primary)
        if group is None:
            continue
        presence, keys = group
        for relation in mention.get("alias_relations", []):
            alias_key = normalize_compact_key(relation.get("surface", ""))
            if alias_key:
                keys.add(alias_key)
    return groups


def _evaluate_presence(
    expected_groups: dict[str, tuple[str, set[str]]],
    mentions: list[dict[str, str]],
) -> dict[str, Any]:
    """归一化比对 presence；人物组内任一 surface 命中即算该人物匹配，意外 mention 记失败。"""
    got_by_key: dict[str, str] = {}
    for mention in mentions:
        key = normalize_compact_key(mention.get("primary_surface", ""))
        if not key:
            continue
        presence = mention.get("presence", "")
        existing = got_by_key.get(key)
        if existing is None or presence == "appeared":
            got_by_key[key] = presence

    all_expected_keys = set().union(
        *(keys for _, keys in expected_groups.values())
    ) if expected_groups else set()

    mismatches = []
    for canonical, (expected_presence, keys) in expected_groups.items():
        if not any(got_by_key.get(key) == expected_presence for key in keys):
            mismatches.append({
                "surface": canonical,
                "expected": expected_presence,
                "got": got_by_key.get(canonical),
            })
    unexpected = [
        {"primary_surface": key, "presence": presence}
        for key, presence in got_by_key.items()
        if key not in all_expected_keys
    ]
    return {
        "presence_matches": len(expected_groups) - len(mismatches),
        "presence_total": len(expected_groups),
        "presence_mismatches": mismatches,
        "unexpected_mentions": unexpected,
    }


def _run_case(
    extractor: SceneFactExtractor,
    case: dict,
    *,
    expected: dict[str, str],
    trace_id: str,
) -> dict[str, Any]:
    try:
        facts = extractor.extract(_input(case), trace_id=trace_id)
        schema_success = True
        error = None
    except Exception as exc:  # noqa: BLE001 - 逐条容错
        schema_success = False
        error = f"{type(exc).__name__}: {exc}"
        facts = None

    mentions = (
        [
            {"primary_surface": m.primary_surface, "presence": m.presence}
            for m in facts.npc_mentions
        ]
        if facts is not None
        else []
    )
    expected_groups = _expected_groups(
        expected,
        json.loads(case["scene_facts"]) if case.get("scene_facts") else None,
    )
    evaluation = _evaluate_presence(expected_groups, mentions)
    passed = (
        schema_success
        and evaluation["presence_mismatches"] == []
        and evaluation["unexpected_mentions"] == []
    )
    return {
        "case_id": case["case_id"],
        "schema_success": schema_success,
        "error": error,
        "extracted_mentions": mentions,
        "passed": passed,
        **evaluation,
        "trace_id": trace_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="真实 SceneFactExtractor presence focused replay（只读）。"
    )
    parser.parse_args(argv)

    config = ModelConfigStore().load_effective()
    if not config.configured:
        raise RuntimeError(
            "model is not configured; configure it in the app or .env"
        )
    gateway = SharedGatewayFactory(
        config=config.gateway_config(),
        log_path=str(TRACE_PATH),
    )()
    extractor = SceneFactExtractor(gateway)  # type: ignore[arg-type]

    fixtures = load_fixtures(PENDING_FIXTURES)
    cases: list[dict[str, Any]] = []
    for fixture in fixtures.cases:
        contract = fixture.last_scene_contract
        if contract is None:
            continue
        expected = {
            item.surface: item.presence
            for item in contract.presence_expectations
        }
        case = {
            "case_id": fixture.case_id,
            "scene_id": fixture.scene_id,
            "turn_index": fixture.turn_index,
            "player_name": fixture.player_name,
            "scene_location": fixture.scene_location,
            "participants": fixture.participants,
            "scene_text": fixture.scene_text,
            "scene_facts": fixture.scene_facts,
            "core_memory_items": [
                item.model_dump() for item in fixture.core_memory_items
            ],
        }
        cases.append(
            _run_case(
                extractor,
                case,
                expected=expected,
                trace_id=f"scene-presence-{fixture.case_id}",
            )
        )

    adhoc = dict(ADHOC_SCENE)
    cases.append(
        _run_case(
            extractor,
            adhoc,
            expected=adhoc["expected"],
            trace_id="scene-presence-adhoc-mentioned-then-appeared",
        )
    )

    total_matches = sum(case["presence_matches"] for case in cases)
    total_expected = sum(case["presence_total"] for case in cases)
    failed_cases = [
        case["case_id"] for case in cases if not case["passed"]
    ]
    unexpected_count = sum(
        len(case["unexpected_mentions"]) for case in cases
    )
    report = {
        "n": len(cases),
        "passed_cases": len(cases) - len(failed_cases),
        "failed_cases": failed_cases,
        "unexpected_mentions": unexpected_count,
        "presence_matches": total_matches,
        "presence_total": total_expected,
        "cases": cases,
        "review_notes": (
            "本报告不参与 accepted baseline；Phase 6 再对真实存档做完整 replay。"
            "失败条件：schema failure、presence mismatch 或意外/额外 mention。"
        ),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"presence focused replay: {total_matches}/{total_expected} matched, "
        f"passed={len(cases) - len(failed_cases)}/{len(cases)} "
        f"unexpected={unexpected_count} schema_failures="
        f"{len([c for c in cases if not c['schema_success']])} "
        f"report={REPORT_PATH}"
    )
    for case in cases:
        problems = case["presence_mismatches"] + case["unexpected_mentions"]
        print(
            f"  {case['case_id']:<42} {'PASS' if case['passed'] else 'FAIL'} "
            f"{case['presence_matches']}/{case['presence_total']} "
            f"{problems if problems else ''}"
        )
    if failed_cases:
        print(f"presence focused replay FAILED: {sorted(failed_cases)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
