"""Narrative Quality Benchmark and LLM-as-a-Judge Evaluation Suite.

100% matches production narrative composition pipeline:
- Input models: CoreMemory, CurrentState, TurnResolutionResult, ContextFactSet,
  NPCProfileDirectorView, DirectorStageResult, ShortTermScene, DirectorEvent.
- Execution: calls production `run_narrative_composition` using `LLMGateway`.
- Dual Scoring:
  1. Deterministic safety & invariant checks (Schema, outcome linking, stopping point).
  2. LLM-as-a-Judge evaluation (NPC agency, action affordance, information density, safety).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core.ids import new_hex_id
from app.core.llm import LLMGateway
from app.core.llm.config import create_gateway
from app.domain.context_facts.schemas import ContextFact, ContextFactSet
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import (
    CurrentState,
    InventoryItem,
    ParticipantState,
    PlayerState,
    SceneState,
    WorldClock,
)
from app.domain.director.schemas import (
    DirectorResourceRef,
    DirectorStageMaterial,
    DirectorStageResult,
)
from app.domain.director_event.schemas import DirectorEvent
from app.domain.narrative_composer.runner import run_narrative_composition
from app.domain.narrative_composer.schemas import (
    NarrativeComposerContext,
    NarrativeComposerResult,
)
from app.domain.npc_profile.schemas import (
    KnownAbility,
    NPCProfileDirectorView,
)
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.turn_resolution.schemas import (
    ActionResolutionFrame,
    TurnResolutionResult,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "benchmarks" / "narrative_quality" / "fixtures.json"
DEFAULT_RECORDINGS = (
    ROOT / "benchmarks" / "narrative_quality" / "recordings" / "baseline.json"
)
DEFAULT_BASELINE = (
    ROOT / "benchmarks" / "narrative_quality" / "baseline.json"
)


class NarrativeTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    turn_index: int = Field(ge=1)
    user_input: str = Field(min_length=1)
    core_memory: CoreMemory
    current_state: CurrentState
    turn_resolution: TurnResolutionResult
    context_facts: ContextFactSet = Field(
        default_factory=lambda: ContextFactSet(
            turn_focus="No Context Facts", facts=[]
        )
    )
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)
    director_stage: DirectorStageResult | None = None
    engaged_events: list[DirectorEvent] = Field(default_factory=list)
    recent_scene: ShortTermScene | None = None


_CASES_ADAPTER = TypeAdapter(list[NarrativeTestCase])


# ---------------------------------------------------------------------------
# Invariant Check Results
# ---------------------------------------------------------------------------


class InvariantCheckResult(BaseModel):
    passed: bool
    schema_valid: bool = True
    all_outcomes_linked: bool = True
    stopping_point_respected: bool = True
    no_forbidden_phrases: bool = True
    details: list[str] = Field(default_factory=list)


def check_invariants(
    case: NarrativeTestCase, raw_output: dict[str, Any]
) -> InvariantCheckResult:
    details: list[str] = []
    schema_valid = True
    all_outcomes_linked = True
    stopping_point_respected = True
    no_forbidden_phrases = True

    try:
        parsed = NarrativeComposerResult.model_validate(raw_output)
    except Exception as exc:
        return InvariantCheckResult(
            passed=False,
            schema_valid=False,
            all_outcomes_linked=False,
            stopping_point_respected=False,
            no_forbidden_phrases=False,
            details=[f"Schema validation failed: {exc}"],
        )

    # Check turn resolution outcomes linked
    expected_ids = {
        frame.action_id for frame in case.turn_resolution.action_resolutions
    }
    linked_ids = set()
    for event in parsed.planned_events:
        for oid in event.linked_outcome_ids:
            linked_ids.add(oid)

    missing_outcomes = expected_ids - linked_ids
    if missing_outcomes:
        all_outcomes_linked = False
        details.append(f"Missing linked outcomes in planned_events: {missing_outcomes}")

    # Check stopping point and scene text non-empty
    if not parsed.scene_text.strip():
        stopping_point_respected = False
        details.append("scene_text is empty")

    if not parsed.scene_stopping_point.strip():
        stopping_point_respected = False
        details.append("scene_stopping_point is empty")

    # Forbidden leak phrases
    forbidden_terms = [
        "CoreMemory",
        "CurrentState",
        "TurnResolutionResult",
        "Director Agent",
        "ActionResolutionFrame",
        "scene_stopping_point",
        "planned_events",
    ]
    for term in forbidden_terms:
        if term in parsed.scene_text:
            no_forbidden_phrases = False
            details.append(f"System term '{term}' leaked into scene_text")

    passed = (
        schema_valid
        and all_outcomes_linked
        and stopping_point_respected
        and no_forbidden_phrases
    )
    return InvariantCheckResult(
        passed=passed,
        schema_valid=schema_valid,
        all_outcomes_linked=all_outcomes_linked,
        stopping_point_respected=stopping_point_respected,
        no_forbidden_phrases=no_forbidden_phrases,
        details=details,
    )


# ---------------------------------------------------------------------------
# LLM Judge Protocol
# ---------------------------------------------------------------------------


class JudgeScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_agency_and_stakes: int = Field(ge=1, le=5)
    action_affordance_and_hook: int = Field(ge=1, le=5)
    concrete_information_density: int = Field(ge=1, le=5)
    feedback: str = Field(min_length=1)

    @property
    def total_score(self) -> float:
        return (
            self.npc_agency_and_stakes
            + self.action_affordance_and_hook
            + self.concrete_information_density
        ) / 3.0


JUDGE_PROMPT_TEMPLATE = """你是一个极其严苛挑剔的文字冒险游戏（TTRPG / 交互叙事）专家评委。
你的任务是仅凭生成的【回合正文 (scene_text)】，评估其戏剧质量、NPC鲜活性与行动启发力。

## 严格评分标准（1 - 5 分，拒绝通胀，默认基准为 2~3 分）

1. **npc_agency_and_stakes（NPC 鲜活性与自主诉求）**：
   - 1分（工具人）：NPC 像机器读公告/读简历，毫无自身立场与情绪，问什么答什么，极度温顺平淡。（若本回合场景无NPC，按3分中立基准）
   - 2分（功能性交代）：有礼貌的对白，但缺乏自身的小算盘、微观门槛、顾虑或性格棱角。
   - 3分（合格）：有自然的市井/仙侠语气，符合基本人设，但没有对主角形成任何社交阻力或反向要求。
   - 4分（生动）：NPC 在配合主角的同时，带出了鲜明的性格细节、具体顾虑或微观利益交换。
   - 5分（极具张力）：NPC 展现出极强的自主性，有自己的算盘与反向试探，言语间暗流涌动。

2. **action_affordance_and_hook（行动抓手与决策启发 —— 仅根据正文阅读体感）**：
   - 1分（死寂冷场）：事情做完，正文以风景/沉默收尾，玩家读完毫无紧迫感与具体想法，只能原地发呆。
   - 2分（平庸闭环）：行动顺利结束（如“银货两讫，各自站定”），只有常规的“离开/继续问”等平淡选项。
   - 3分（有基础方向）：给出了一个通常的下一步，但缺乏戏剧冲突与吸引力。
   - 4分（明确抓手）：正文留下了具体的分歧点、新线索或利益抉择，玩家有清晰的应对思路。
   - 5分（强烈驱动）：正文收尾处将矛盾、悬念或抉择精准抛到玩家脸上，产生极强的下一步输入欲望。

3. **concrete_information_density（信息具体度与线索质感）**：
   - 1分（空洞模糊）：“告诉你了一些内情”、“商量了一阵”。
   - 2分（泛泛而谈）：有信息方向，但缺少具体人名、地名、数字等硬锚点。
   - 3分（基本具体）：包含 1~2 个基本名称或地点。
   - 4~5分（质感丰富）：包含极具画面感的具体地点（如“枯骨岭”、“西市口赵铁匠”）、门槛金额、派系利益或矛盾细节。

---

## 背景上下文

### 玩家行动输入
```text
{user_input}
```

### 回合判定（Turn Resolution）
```json
{turn_resolution_json}
```

### 场景与在场角色
```json
{current_state_json}
```

### 在场 NPC 设定参考
```json
{npc_profiles_json}
```

---

## 待评估的生成正文 (scene_text)
（请注意：你只能基于以下【正文】本身的描写进行评审，不要脑补正文未写出的内容！）

```text
{scene_text}
```

---

## 输出规范
只输出符合以下 JSON Schema 的纯 JSON：
```json
{{
  "npc_agency_and_stakes": 1-5,
  "action_affordance_and_hook": 1-5,
  "concrete_information_density": 1-5,
  "feedback": "简明严厉的裁判评语（指出正文平淡/生动的具体段落与扣分依据）"
}}
```
"""


def evaluate_with_judge(
    judge_gateway: LLMGateway,
    case: NarrativeTestCase,
    result: NarrativeComposerResult,
) -> JudgeScore:
    prompt_text = JUDGE_PROMPT_TEMPLATE.format(
        user_input=case.user_input,
        turn_resolution_json=case.turn_resolution.model_dump_json(indent=2),
        current_state_json=case.current_state.model_dump_json(indent=2),
        npc_profiles_json=json.dumps(
            [p.model_dump(mode="json") for p in case.npc_profiles],
            ensure_ascii=False,
            indent=2,
        ),
        scene_text=result.scene_text,
    )
    res = judge_gateway.provider.generate(
        prompt=prompt_text,
        model=judge_gateway._provider._default_model if hasattr(judge_gateway, "_provider") else "",
        temperature=0.2,
    )
    raw_content = res.content.strip()
    if raw_content.startswith("```"):
        lines = raw_content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw_content = "\n".join(lines).strip()
    data = json.loads(raw_content)
    return JudgeScore.model_validate(data)


# ---------------------------------------------------------------------------
# Benchmark Runner & Storage
# ---------------------------------------------------------------------------


class CaseRecording(BaseModel):
    case_id: str
    category: str
    title: str
    user_input: str
    output: dict[str, Any]
    token_usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: float = 0.0
    invariant_check: InvariantCheckResult | None = None
    judge_score: JudgeScore | None = None


class BenchmarkReport(BaseModel):
    version: int = 1
    timestamp: str
    total_cases: int
    invariant_pass_rate: float
    avg_npc_agency: float = 0.0
    avg_action_affordance: float = 0.0
    avg_information_density: float = 0.0
    avg_total_score: float = 0.0
    recordings: list[CaseRecording] = Field(default_factory=list)


def load_fixtures(path: Path) -> list[NarrativeTestCase]:
    if not path.exists():
        raise FileNotFoundError(f"Fixtures file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _CASES_ADAPTER.validate_python(raw)


def run_recording(
    fixtures: list[NarrativeTestCase],
    gateway: LLMGateway,
    only_case_id: str | None = None,
) -> list[CaseRecording]:
    recordings: list[CaseRecording] = []
    run_id = new_hex_id(prefix="bench_narrative", length=8)

    for idx, case in enumerate(fixtures, start=1):
        if only_case_id and case.case_id != only_case_id:
            continue

        print(f"[{idx}/{len(fixtures)}] Generating for {case.case_id} ({case.title})...")
        context = NarrativeComposerContext(
            user_input=case.user_input,
            current_state=case.current_state,
            turn_resolution=case.turn_resolution,
            core_memory=case.core_memory,
            context_facts=case.context_facts,
            npc_profiles=case.npc_profiles,
            director_stage=case.director_stage,
            engaged_events=case.engaged_events,
        )

        trace_id = f"{run_id}-{case.case_id}"
        start_t = time.perf_counter()
        try:
            result = run_narrative_composition(
                gateway,
                task="narrative_compose",
                context=context,
                recent_scene=case.recent_scene,
                turn_index=case.turn_index,
                trace_id=trace_id,
            )
            latency_ms = (time.perf_counter() - start_t) * 1000.0
            output_data = result.model_dump()
            inv_check = check_invariants(case, output_data)
        except Exception as exc:
            latency_ms = (time.perf_counter() - start_t) * 1000.0
            print(f"  [ERROR] Case {case.case_id} failed during generation: {exc}")
            output_data = {
                "planning_reasoning": f"Generation failed: {exc}",
                "must_establish_facts": [],
                "must_not_establish_facts": [],
                "information_boundaries": [],
                "scene_stopping_point": "生成异常",
                "planned_events": [],
                "scene_reasoning": f"Generation failed: {exc}",
                "scene_text": f"【生成异常: {exc}】",
                "closing_prompt": "。",
                "next_action_directions": [],
            }
            inv_check = InvariantCheckResult(
                passed=False,
                schema_valid=False,
                all_outcomes_linked=False,
                stopping_point_respected=False,
                no_forbidden_phrases=True,
                details=[f"Generation exception: {exc}"],
            )

        # Retrieve token usage if available in tracer
        tokens: dict[str, int] = {}
        if hasattr(gateway, "tracer"):
            traces = gateway.tracer.read_traces(trace_id)
            if traces:
                usage = traces[-1].get("token_usage") or {}
                tokens = {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }

        recordings.append(
            CaseRecording(
                case_id=case.case_id,
                category=case.category,
                title=case.title,
                user_input=case.user_input,
                output=output_data,
                token_usage=tokens,
                latency_ms=round(latency_ms, 2),
                invariant_check=inv_check,
            )
        )

    return recordings


def run_judge(
    fixtures: list[NarrativeTestCase],
    recordings: list[CaseRecording],
    judge_gateway: LLMGateway,
) -> BenchmarkReport:
    case_map = {case.case_id: case for case in fixtures}
    scored_recordings: list[CaseRecording] = []

    sum_agency = 0.0
    sum_affordance = 0.0
    sum_density = 0.0
    sum_total = 0.0
    passed_count = 0

    for idx, rec in enumerate(recordings, start=1):
        case = case_map.get(rec.case_id)
        if not case:
            print(f"Warning: Case {rec.case_id} not found in fixtures, skipping judge.")
            continue

        print(f"[{idx}/{len(recordings)}] Judging {rec.case_id}...")
        try:
            parsed_result = NarrativeComposerResult.model_validate(rec.output)
            inv_check = check_invariants(case, rec.output)
            rec.invariant_check = inv_check
            if inv_check.passed:
                passed_count += 1

            judge_score = evaluate_with_judge(judge_gateway, case, parsed_result)
            rec.judge_score = judge_score

            sum_agency += judge_score.npc_agency_and_stakes
            sum_affordance += judge_score.action_affordance_and_hook
            sum_density += judge_score.concrete_information_density
            sum_total += judge_score.total_score
        except Exception as exc:
            print(f"  [ERROR] Case {rec.case_id} failed during judging: {exc}")
            rec.judge_score = JudgeScore(
                npc_agency_and_stakes=1,
                action_affordance_and_hook=1,
                concrete_information_density=1,
                feedback=f"Judge exception: {exc}",
            )
            sum_agency += 1
            sum_affordance += 1
            sum_density += 1
            sum_total += 1.0

        scored_recordings.append(rec)

    total = len(scored_recordings) or 1
    report = BenchmarkReport(
        version=1,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        total_cases=len(scored_recordings),
        invariant_pass_rate=round(passed_count / total, 4),
        avg_npc_agency=round(sum_agency / total, 2),
        avg_action_affordance=round(sum_affordance / total, 2),
        avg_information_density=round(sum_density / total, 2),
        avg_total_score=round(sum_total / total, 2),
        recordings=scored_recordings,
    )
    return report


def print_summary_table(report: BenchmarkReport) -> None:
    print("\n" + "=" * 80)
    print("           NARRATIVE QUALITY BENCHMARK REPORT")
    print("=" * 80)
    print(f"Timestamp:              {report.timestamp}")
    print(f"Total Cases Evaluated:  {report.total_cases}")
    print(f"Invariant Pass Rate:    {report.invariant_pass_rate * 100:.1f}% (Hard Safety Gate)")
    print(f"Avg NPC Agency (1-5):   {report.avg_npc_agency} / 5.0 (NPC鲜活与自主诉求)")
    print(f"Avg Action Affordance:  {report.avg_action_affordance} / 5.0 (仅凭正文的行动抓手)")
    print(f"Avg Info Density (1-5): {report.avg_information_density} / 5.0 (信息具体度与质感)")
    print(f"OVERALL QUALITY SCORE:  {report.avg_total_score} / 5.0")
    print("-" * 80)
    print(f"{'Case ID':<32} | {'Gate':<5} | {'Agency':<7} | {'Afford':<7} | {'Density':<7} | {'Total':<5}")
    print("-" * 80)
    for rec in report.recordings:
        p_str = "PASS" if rec.invariant_check and rec.invariant_check.passed else "FAIL"
        if rec.judge_score:
            print(
                f"{rec.case_id:<32} | {p_str:<5} | {rec.judge_score.npc_agency_and_stakes:<7} | "
                f"{rec.judge_score.action_affordance_and_hook:<7} | "
                f"{rec.judge_score.concrete_information_density:<7} | "
                f"{rec.judge_score.total_score:<5.2f}"
            )
        else:
            print(f"{rec.case_id:<32} | {p_str:<5} | N/A")
    print("=" * 80 + "\n")


def compare_reports(baseline_path: Path, candidate_path: Path) -> None:
    base_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    cand_data = json.loads(candidate_path.read_text(encoding="utf-8"))

    base_report = BenchmarkReport.model_validate(base_data)
    cand_report = BenchmarkReport.model_validate(cand_data)

    print("\n" + "=" * 85)
    print("              A/B BENCHMARK COMPARISON REPORT")
    print("=" * 85)
    print(f"{'Metric':<30} | {'Baseline':<12} | {'Candidate':<12} | {'Delta':<10}")
    print("-" * 85)

    def diff_row(name: str, base_val: float, cand_val: float) -> None:
        delta = cand_val - base_val
        delta_str = f"{delta:+.2f}" if delta != 0 else "0.00"
        print(f"{name:<30} | {base_val:<12.2f} | {cand_val:<12.2f} | {delta_str:<10}")

    diff_row("Invariant Pass Rate (%)", base_report.invariant_pass_rate * 100, cand_report.invariant_pass_rate * 100)
    diff_row("NPC Agency & Stakes", base_report.avg_npc_agency, cand_report.avg_npc_agency)
    diff_row("Action Affordance & Hook", base_report.avg_action_affordance, cand_report.avg_action_affordance)
    diff_row("Concrete Info Density", base_report.avg_information_density, cand_report.avg_information_density)
    diff_row("Overall Quality Score", base_report.avg_total_score, cand_report.avg_total_score)
    print("=" * 85 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Narrative Quality Benchmark Suite"
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES,
        help="Path to fixture cases JSON",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Run real generation and record outputs",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Run LLM-as-a-Judge on recorded outputs",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Offline invariant check on recordings",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        help="Compare candidate report JSON against baseline",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Baseline report path for comparison",
    )
    parser.add_argument(
        "--recordings",
        type=Path,
        default=DEFAULT_RECORDINGS,
        help="Path to recordings JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path for recorded/judged report",
    )
    parser.add_argument(
        "--only",
        type=str,
        help="Only run specific case_id",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model override for generation/judge",
    )

    args = parser.parse_args()

    if args.compare:
        compare_reports(args.baseline, args.compare)
        return

    fixtures = load_fixtures(args.fixtures)

    if args.record:
        gateway = create_gateway(model=args.model)
        recordings = run_recording(fixtures, gateway, only_case_id=args.only)
        out_path = args.output or args.recordings
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                [rec.model_dump() for rec in recordings],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Recorded {len(recordings)} cases to {out_path}")
        return

    if args.judge:
        rec_path = args.recordings
        if not rec_path.exists():
            print(f"Recordings file not found: {rec_path}. Please run --record first.")
            sys.exit(1)
        raw_recs = json.loads(rec_path.read_text(encoding="utf-8"))
        recs = [CaseRecording.model_validate(r) for r in raw_recs]

        judge_gateway = create_gateway(model=args.model)
        report = run_judge(fixtures, recs, judge_gateway)
        print_summary_table(report)

        out_path = args.output or DEFAULT_BASELINE
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print(f"Saved benchmark report to {out_path}")
        return

    if args.check:
        rec_path = args.recordings
        if not rec_path.exists():
            print(f"Recordings file not found: {rec_path}.")
            sys.exit(1)
        raw_recs = json.loads(rec_path.read_text(encoding="utf-8"))
        recs = [CaseRecording.model_validate(r) for r in raw_recs]
        case_map = {c.case_id: c for c in fixtures}

        passed_all = True
        for rec in recs:
            case = case_map.get(rec.case_id)
            if not case:
                continue
            inv = check_invariants(case, rec.output)
            if not inv.passed:
                passed_all = False
                print(f"[FAIL] {rec.case_id}: {inv.details}")
            else:
                print(f"[PASS] {rec.case_id}")

        if not passed_all:
            sys.exit(1)
        print("All invariant checks passed.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
