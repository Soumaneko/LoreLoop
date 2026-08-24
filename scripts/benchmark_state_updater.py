#!/usr/bin/env python3
"""State Updater Benchmark Runner.

Evaluates StateUpdater accuracy across spatio-temporal transit, NPC presence
eviction, clock advancement, and resource settlement cases.

Usage:
    # Run benchmark against live LLM and print results:
    PYTHONPATH=. uv run python scripts/benchmark_state_updater.py

    # Record current execution as baseline:
    PYTHONPATH=. uv run python scripts/benchmark_state_updater.py --record benchmarks/state_updater/baseline_legacy.json

    # Compare current execution against an existing baseline:
    PYTHONPATH=. uv run python scripts/benchmark_state_updater.py --compare benchmarks/state_updater/baseline_legacy.json
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.core.llm.config import create_gateway
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import (
    CurrentState,
    InventoryItem,
    ParticipantState,
    PlayerState,
    ResourcesState,
    SceneState,
    WorldClock,
)
from app.domain.current_state.updater import StateUpdater
from app.domain.scene_facts import (
    SceneFactExtractInput,
    SceneFactExtractor,
    SceneParagraphInput,
)
from app.domain.views import project_scene_facts


DEFAULT_CASES_PATH = ROOT_DIR / "benchmarks" / "state_updater" / "cases.json"
DEFAULT_BASELINE_PATH = ROOT_DIR / "benchmarks" / "state_updater" / "baseline_legacy.json"


def _make_default_core_memory() -> CoreMemory:
    return CoreMemory(items=[
        CoreMemoryItem(
            id="world_1",
            kind="world_invariant",
            strength="hard_constraint",
            source="campaign_setup",
            content="剑与魔法的奇幻大陆，城邦与荒野并存。",
        ),
        CoreMemoryItem(
            id="protagonist_1",
            kind="protagonist_invariant",
            strength="hard_constraint",
            source="player",
            content="莱尔，行事谨慎的游侠冒险者。",
        ),
    ])


def _load_current_state(raw: dict[str, Any]) -> CurrentState:
    scene_raw = raw["scene"]
    clock_raw = raw.get("world_clock", {})
    player_raw = raw["player"]
    res_raw = raw.get("resources", {})
    return CurrentState(
        scene=SceneState(
            scene_id=scene_raw.get("scene_id", "scene_test"),
            location=scene_raw["location"],
            participants=[
                ParticipantState(
                    name=p["name"],
                    attitude_to_player=p.get("attitude_to_player"),
                )
                for p in scene_raw.get("participants", [])
            ],
        ),
        world_clock=WorldClock(
            day=clock_raw.get("day", 1),
            phase=clock_raw.get("phase", "清晨"),
        ),
        player=PlayerState(
            name=player_raw["name"],
            visible_status=player_raw.get("visible_status"),
        ),
        resources=ResourcesState(
            inventory=[
                InventoryItem(name=i["name"], quantity=i.get("quantity", 1))
                for i in res_raw.get("inventory", [])
            ],
            money=res_raw.get("money"),
            money_unit=res_raw.get("money_unit", "金币"),
            fixed_assets=res_raw.get("fixed_assets", []),
            temporary_assets=res_raw.get("temporary_assets", []),
        ),
    )


@dataclass
class CaseEvalResult:
    case_id: str
    name: str
    passed: bool
    location_matched: bool
    clock_matched: bool
    participants_matched: bool
    money_matched: bool
    inventory_matched: bool
    attitudes_matched: bool
    actual_location: str
    expected_location: str
    actual_clock: str
    expected_clock: str
    actual_participants: list[str]
    expected_participants: list[str]
    actual_money: int
    expected_money: int
    actual_inventory: dict[str, int]
    expected_inventory: dict[str, int]
    undeclared_attitude_changes: list[str]
    operations_count: int
    llm_call_count: int
    operation_dropped_count: int
    warnings_count: int
    warnings: list[str]
    decision: str


class _CallCountingProvider:
    """Counts provider calls routed through the gateway for benchmarking."""

    def __init__(self, provider: Any):
        self._provider = provider
        self.calls = 0

    def generate(self, *args: Any, **kwargs: Any):
        self.calls += 1
        return self._provider.generate(*args, **kwargs)

    def stream_generate(self, *args: Any, **kwargs: Any):
        self.calls += 1
        return self._provider.stream_generate(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._provider, name)


#: Warnings that represent a genuinely dropped item (strict accounting).
DROPPED_WARNING_CODES = frozenset({
    "state_update_operation_dropped",
    "frame_ignored_no_evidence",
})


def evaluate_single_case(
    case: dict[str, Any],
    updater: StateUpdater,
    extractor: SceneFactExtractor | None = None,
    call_counter: _CallCountingProvider | None = None,
) -> CaseEvalResult:
    case_id = case["case_id"]
    name = case["name"]
    initial_state = _load_current_state(case["initial_state"])
    core_memory = _make_default_core_memory()
    expected = case["expected_state"]

    paragraph_texts = {
        "p1": case["scene_text"]
    }

    # 动态通过 SceneFactExtractor 抽取事实（完全模拟真实确认管线中的两阶段流转）
    if extractor is not None:
        paragraphs_input = [
            SceneParagraphInput(document_id="p1", text=case["scene_text"])
        ]
        extracted_facts = extractor.extract(
            SceneFactExtractInput(
                scene_text=case["scene_text"],
                current_state=initial_state,
                core_memory=core_memory,
                scene_paragraphs=paragraphs_input,
            ),
            trace_id=f"fact_{case_id}",
        )
        scene_facts_json = project_scene_facts(extracted_facts)
    else:
        scene_facts_json = case["scene_facts_json"]

    # 执行状态更新
    if call_counter is not None:
        call_counter.calls = 0
    start_time = time.perf_counter()
    updated_state = updater.update(
        initial_state,
        scene_facts_json,
        core_memory=core_memory,
        paragraph_texts=paragraph_texts,
        trace_id=f"bench_{case_id}",
    )
    _ = time.perf_counter() - start_time

    # 检查各字段
    act_loc = updated_state.scene.location
    exp_loc = expected["location"]
    loc_matched = (act_loc == exp_loc)

    act_day = updated_state.world_clock.day
    act_phase = updated_state.world_clock.phase
    exp_day = expected["day"]
    exp_phase = expected["phase"]
    clock_matched = (act_day == exp_day and act_phase == exp_phase)

    act_parts = sorted([p.name for p in updated_state.scene.participants])
    exp_parts = sorted(expected["participants"])
    parts_matched = (act_parts == exp_parts)

    act_money = updated_state.resources.money
    exp_money = expected["money"]
    money_matched = (act_money == exp_money)

    act_inv = {i.name: i.quantity for i in updated_state.resources.inventory}
    exp_inv = expected["inventory"]
    inv_matched = (act_inv == exp_inv)

    exp_attitudes = expected.get("attitudes", {})
    act_attitudes = {
        p.name: p.attitude_to_player
        for p in updated_state.scene.participants
        if p.attitude_to_player is not None
    }
    initial_attitudes = {
        p.name: p.attitude_to_player
        for p in initial_state.scene.participants
        if p.attitude_to_player is not None
    }
    declared_names = set(exp_attitudes)
    # Bidirectional check: every declared attitude must match, and the model
    # must not change any undeclared NPC's attitude relative to the initial
    # state (otherwise corrupted cases would still pass).
    undeclared_changes = {
        name: value
        for name, value in act_attitudes.items()
        if name not in declared_names and initial_attitudes.get(name) != value
    }
    attitudes_matched = (
        all(act_attitudes.get(k) == v for k, v in exp_attitudes.items())
        and not undeclared_changes
    )

    passed = (
        loc_matched
        and clock_matched
        and parts_matched
        and money_matched
        and inv_matched
        and attitudes_matched
    )

    last_change_set = getattr(updater, "last_change_set", None)
    ops_count = len(last_change_set.operations) if last_change_set else 0
    decision = last_change_set.decision if last_change_set else ""
    all_warnings = list(getattr(updater, "last_warnings", []))
    warnings = [f"{w.code}: {w.message}" for w in all_warnings]
    dropped_count = sum(
        1 for w in all_warnings if w.code in DROPPED_WARNING_CODES
    )

    return CaseEvalResult(
        case_id=case_id,
        name=name,
        passed=passed,
        location_matched=loc_matched,
        clock_matched=clock_matched,
        participants_matched=parts_matched,
        money_matched=money_matched,
        inventory_matched=inv_matched,
        actual_location=act_loc,
        expected_location=exp_loc,
        actual_clock=f"Day {act_day} {act_phase}",
        expected_clock=f"Day {exp_day} {exp_phase}",
        actual_participants=act_parts,
        expected_participants=exp_parts,
        actual_money=act_money,
        expected_money=exp_money,
        actual_inventory=act_inv,
        expected_inventory=exp_inv,
        undeclared_attitude_changes=sorted(undeclared_changes),
        operations_count=ops_count,
        llm_call_count=(
            call_counter.calls if call_counter is not None else 0
        ),
        operation_dropped_count=dropped_count,
        warnings_count=len(warnings),
        warnings=warnings,
        decision=decision,
        attitudes_matched=attitudes_matched,
    )


def run_benchmark(cases_path: Path) -> dict[str, Any]:
    with open(cases_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    gateway = create_gateway()
    call_counter = _CallCountingProvider(gateway.provider)
    gateway._provider = call_counter  # type: ignore[assignment]
    extractor = SceneFactExtractor(gateway)
    updater = StateUpdater(gateway)

    results: list[CaseEvalResult] = []
    print(f"\n🚀 开始执行 State Updater 真实全链路基准测试 (共 {len(cases)} 个用例)...")
    print("=" * 70)

    for idx, case in enumerate(cases, 1):
        print(f"[{idx}/{len(cases)}] 测试用例: {case['case_id']} ({case['name']})...", end=" ", flush=True)
        res = evaluate_single_case(
            case, updater, extractor=extractor, call_counter=call_counter
        )
        status_str = "✅ PASS" if res.passed else "❌ FAIL"
        print(status_str)
        if not res.passed:
            if not res.location_matched:
                print(f"    - 地点不匹配: 期望='{res.expected_location}', 实际='{res.actual_location}'")
            if not res.clock_matched:
                print(f"    - 时间不匹配: 期望='{res.expected_clock}', 实际='{res.actual_clock}'")
            if not res.participants_matched:
                print(f"    - 在场人物不匹配: 期望={res.expected_participants}, 实际={res.actual_participants}")
            if not res.money_matched:
                print(f"    - 金钱不匹配: 期望={res.expected_money}, 实际={res.actual_money}")
            if not res.inventory_matched:
                print(f"    - 背包不匹配: 期望={res.expected_inventory}, 实际={res.actual_inventory}")
            if not res.attitudes_matched:
                print(f"    - 态度不匹配: 未声明变更={res.undeclared_attitude_changes}")
        results.append(res)

    total = len(results)
    passed_count = sum(1 for r in results if r.passed)
    loc_acc = sum(1 for r in results if r.location_matched) / total
    clock_acc = sum(1 for r in results if r.clock_matched) / total
    parts_acc = sum(1 for r in results if r.participants_matched) / total
    money_acc = sum(1 for r in results if r.money_matched) / total
    inv_acc = sum(1 for r in results if r.inventory_matched) / total
    att_acc = sum(1 for r in results if r.attitudes_matched) / total
    total_acc = passed_count / total
    total_llm_calls = sum(r.llm_call_count for r in results)
    total_dropped = sum(r.operation_dropped_count for r in results)

    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_cases": total,
        "passed_cases": passed_count,
        "total_accuracy": round(total_acc, 4),
        "location_accuracy": round(loc_acc, 4),
        "clock_accuracy": round(clock_acc, 4),
        "participants_accuracy": round(parts_acc, 4),
        "money_accuracy": round(money_acc, 4),
        "inventory_accuracy": round(inv_acc, 4),
        "attitude_accuracy": round(att_acc, 4),
        "total_llm_calls": total_llm_calls,
        "avg_llm_calls_per_case": round(total_llm_calls / total, 2),
        "operation_dropped_count": total_dropped,
        "cases": [asdict(r) for r in results],
    }

    print("\n" + "=" * 70)
    print("📊 【基准测试结果汇总】")
    print(f"  • 总通过率: {passed_count}/{total} ({total_acc * 100:.1f}%)")
    print(f"  • 地点准确率 (Location):     {loc_acc * 100:.1f}%")
    print(f"  • 时间准确率 (WorldClock):   {clock_acc * 100:.1f}%")
    print(f"  • 在场人物准确率 (Participants): {parts_acc * 100:.1f}%")
    print(f"  • 金钱准确率 (Money):         {money_acc * 100:.1f}%")
    print(f"  • 物品准确率 (Inventory):     {inv_acc * 100:.1f}%")
    print(f"  • 态度准确率 (Attitude):      {att_acc * 100:.1f}%")
    print(f"  • LLM 调用次数:               {total_llm_calls} "
          f"(平均 {total_llm_calls / total:.2f}/用例)")
    print(f"  • 操作丢弃数:                 {total_dropped}")
    print("=" * 70 + "\n")

    return summary


def main():
    parser = argparse.ArgumentParser(description="State Updater Benchmark")
    parser.add_argument("--cases", type=str, default=str(DEFAULT_CASES_PATH), help="Path to cases.json")
    parser.add_argument("--record", type=str, default=None, help="Save execution results to baseline JSON file")
    parser.add_argument("--compare", type=str, default=None, help="Compare against an existing baseline JSON file")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.exists():
        print(f"Error: Cases file not found: {cases_path}")
        sys.exit(1)

    summary = run_benchmark(cases_path)

    if args.record:
        out_path = Path(args.record)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"💾 基准数据已成功保存至: {out_path}")

    if args.compare:
        cmp_path = Path(args.compare)
        if not cmp_path.exists():
            print(f"Warning: Compare file not found: {cmp_path}")
        else:
            with open(cmp_path, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            print("🔍 【重构前后效果对比】")
            print(f"  • 综合通过率:   {legacy.get('total_accuracy', 0)*100:.1f}%  ->  {summary['total_accuracy']*100:.1f}%")
            print(f"  • 地点匹配率:   {legacy.get('location_accuracy', 0)*100:.1f}%  ->  {summary['location_accuracy']*100:.1f}%")
            print(f"  • 时间匹配率:   {legacy.get('clock_accuracy', 0)*100:.1f}%  ->  {summary['clock_accuracy']*100:.1f}%")
            print(f"  • 人物在场匹配率: {legacy.get('participants_accuracy', 0)*100:.1f}%  ->  {summary['participants_accuracy']*100:.1f}%")

            legacy_cases = legacy.get("cases", [])
            legacy_calls = sum(
                c.get("llm_call_count", 0) for c in legacy_cases
            )
            legacy_dropped = sum(
                c.get("operation_dropped_count", 0) for c in legacy_cases
            )
            if legacy_calls or summary["total_llm_calls"]:
                print(
                    f"  • LLM 调用次数: {legacy_calls}  ->  "
                    f"{summary['total_llm_calls']}"
                )
            print(
                f"  • 操作丢弃数:   {legacy_dropped}  ->  "
                f"{summary['operation_dropped_count']}"
            )


if __name__ == "__main__":
    main()
