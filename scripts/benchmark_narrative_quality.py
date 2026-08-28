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
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.core.ids import new_hex_id
from app.core.llm import LLMGateway
from app.core.llm.config import create_gateway
from app.core.llm.prompts import PromptLoader
from app.domain.context_facts.schemas import ContextFact, ContextFactSet
from app.domain.context_facts.views import project_context_facts
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
from app.domain.plot_outline.views import project_turn_resolution
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.turn_resolution.schemas import (
    ActionResolutionFrame,
    TurnResolutionResult,
)
from app.domain.views import (
    project_current_state,
    project_npc_profiles,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "benchmarks" / "narrative_quality" / "fixtures.json"
DEFAULT_RECORDINGS = (
    ROOT / "benchmarks" / "narrative_quality" / "recordings" / "baseline.json"
)
DEFAULT_BASELINE = (
    ROOT / "benchmarks" / "narrative_quality" / "baseline.json"
)
DEFAULT_AB_RECORDINGS = (
    ROOT / "benchmarks" / "narrative_quality" / "recordings" / "ab_experiment.json"
)
DEFAULT_EVAL_PROMPTS_DIR = (
    ROOT / "benchmarks" / "narrative_quality" / "eval_prompts"
)
DEFAULT_MAPPING_PATH = (
    ROOT / "benchmarks" / "narrative_quality" / "eval_prompts" / "mapping.json"
)
DEFAULT_JUDGMENTS_DIR = (
    ROOT / "benchmarks" / "narrative_quality" / "judgments"
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


def check_recordings(
    fixtures: list[NarrativeTestCase],
    recordings: list[CaseRecording],
) -> tuple[bool, list[str]]:
    """Validate recordings against fixtures for completeness, matching inputs, and invariant gates."""
    case_map = {c.case_id: c for c in fixtures}
    rec_case_ids = {r.case_id for r in recordings}
    fixture_case_ids = set(case_map.keys())

    passed_all = True
    messages: list[str] = []

    unknown_ids = rec_case_ids - fixture_case_ids
    if unknown_ids:
        passed_all = False
        for uid in sorted(unknown_ids):
            messages.append(
                f"[FAIL] {uid}: Unknown case ID in recordings (not in current fixtures)."
            )

    missing_ids = fixture_case_ids - rec_case_ids
    if missing_ids:
        passed_all = False
        for mid in sorted(missing_ids):
            messages.append(f"[FAIL] {mid}: Case missing from recordings.")

    for rec in recordings:
        case = case_map.get(rec.case_id)
        if not case:
            continue
        if rec.user_input.strip() != case.user_input.strip():
            passed_all = False
            messages.append(
                f"[FAIL] {rec.case_id}: user_input mismatch between recording and current fixture."
            )
            continue

        inv = check_invariants(case, rec.output)
        if not inv.passed:
            passed_all = False
            messages.append(f"[FAIL] {rec.case_id}: {inv.details}")
        else:
            messages.append(f"[PASS] {rec.case_id}")

    return passed_all, messages


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


# ---------------------------------------------------------------------------
# A/B Paired Testing Models & Protocols
# ---------------------------------------------------------------------------


class ABCaseRecording(BaseModel):
    variant: Literal["A", "B"]
    case_id: str
    category: str
    title: str
    repetition: int
    user_input: str
    output: dict[str, Any]
    token_usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: float = 0.0
    char_count: int = 0
    error: str | None = None
    invariant_check: InvariantCheckResult | None = None


def compute_file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def compute_prompt_fingerprint(prompt_loader: PromptLoader, task: str) -> str:
    prompt_template, _ = prompt_loader.load(task)
    return hashlib.sha256(prompt_template.encode("utf-8")).hexdigest()[:16]


class ABExperimentRecordings(BaseModel):
    version: int = 1
    timestamp: str
    model: str = ""
    task_a: str = "narrative_compose"
    task_b: str = "narrative_compose_open_affordance"
    fixtures_hash: str = ""
    prompt_a_fingerprint: str = ""
    prompt_b_fingerprint: str = ""
    case_ids: list[str] = Field(default_factory=list)
    repetitions: int = 3
    temperature: float = 0.8
    total_calls: int
    recordings: list[ABCaseRecording] = Field(default_factory=list)


class RawPairJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repetition: Literal[1, 2, 3]
    overall_preference: Literal["left", "right", "tie"]
    continue_desire: Literal["left", "right", "tie"]
    open_affordance: Literal["left", "right", "tie"]
    surprising_but_coherent: Literal["left", "right", "tie"]
    npc_world_agency: Literal["left", "right", "tie"]
    specificity: Literal["left", "right", "tie"]
    left_evidence: str = Field(min_length=2)
    right_evidence: str = Field(min_length=2)
    left_violations: list[str]
    right_violations: list[str]
    reason: str = Field(min_length=1)


class RawCaseJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    pairs: list[RawPairJudgment] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def _validate_reps(self):
        reps = [p.repetition for p in self.pairs]
        if set(reps) != {1, 2, 3} or len(reps) != 3:
            raise ValueError(
                f"Judgment pairs must contain exactly repetitions 1, 2, 3 without duplicates, got {reps}"
            )
        return self


class PairSummary(BaseModel):
    repetition: int
    left_variant: str
    right_variant: str
    winner: Literal["A", "B", "tie", "void"]
    overall_preference: Literal["A", "B", "tie"]
    continue_desire: Literal["A", "B", "tie"]
    open_affordance: Literal["A", "B", "tie"]
    surprising_but_coherent: Literal["A", "B", "tie"]
    npc_world_agency: Literal["A", "B", "tie"]
    specificity: Literal["A", "B", "tie"]
    left_evidence: str = ""
    right_evidence: str = ""
    a_violations: list[str] = Field(default_factory=list)
    b_violations: list[str] = Field(default_factory=list)
    reason: str = ""


class CaseSummary(BaseModel):
    case_id: str
    winner: Literal["A", "B", "tie"]
    a_pair_wins: int = 0
    b_pair_wins: int = 0
    tie_pairs: int = 0
    void_pairs: int = 0
    pairs: list[PairSummary] = Field(default_factory=list)


class ABSummaryReport(BaseModel):
    version: int = 1
    timestamp: str
    total_cases: int
    a_case_wins: int
    b_case_wins: int
    tie_cases: int
    b_case_win_rate: float
    total_pairs: int
    a_pair_wins: int
    b_pair_wins: int
    tie_pairs: int
    void_pairs: int
    dimension_wins: dict[str, dict[str, int]]
    resource_stats: dict[str, Any]
    cases: list[CaseSummary] = Field(default_factory=list)


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


def run_ab_recording(
    fixtures: list[NarrativeTestCase],
    gateway: LLMGateway,
    *,
    task_a: str = "narrative_compose",
    task_b: str = "narrative_compose_open_affordance",
    repetitions: int = 3,
    only_case_id: str | None = None,
    reuse_a_path: Path | None = None,
) -> ABExperimentRecordings:
    recordings: list[ABCaseRecording] = []
    run_id = new_hex_id(prefix="bench_ab", length=8)

    filtered_cases = (
        [c for c in fixtures if c.case_id == only_case_id]
        if only_case_id
        else fixtures
    )

    existing_a_map: dict[tuple[str, int], ABCaseRecording] = {}
    if reuse_a_path and reuse_a_path.exists():
        old_data = json.loads(reuse_a_path.read_text(encoding="utf-8"))
        old_exp = ABExperimentRecordings.model_validate(old_data)
        for r in old_exp.recordings:
            if r.variant == "A":
                existing_a_map[(r.case_id, r.repetition)] = r
        print(f"Loaded {len(existing_a_map)} existing Variant A recordings from {reuse_a_path}")

    variants_to_run = ["B"] if existing_a_map else ["A", "B"]
    total_calls = len(filtered_cases) * len(variants_to_run) * repetitions
    call_idx = 0

    for rep in range(1, repetitions + 1):
        for case in filtered_cases:
            if existing_a_map and (case.case_id, rep) in existing_a_map:
                recordings.append(existing_a_map[(case.case_id, rep)])
            for variant in variants_to_run:
                task = task_a if variant == "A" else task_b
                call_idx += 1
                print(
                    f"[{call_idx}/{total_calls}] Variant {variant} (Rep {rep}) for {case.case_id} ({case.title})..."
                )
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
                trace_id = f"{run_id}-{case.case_id}-var{variant}-rep{rep}"
                start_t = time.perf_counter()
                err_msg = None
                try:
                    result = run_narrative_composition(
                        gateway,
                        task=task,
                        context=context,
                        recent_scene=case.recent_scene,
                        turn_index=case.turn_index,
                        trace_id=trace_id,
                    )
                    latency_ms = (time.perf_counter() - start_t) * 1000.0
                    output_data = result.model_dump()
                    char_count = len(result.scene_text)
                    inv_check = check_invariants(case, output_data)
                except Exception as exc:
                    latency_ms = (time.perf_counter() - start_t) * 1000.0
                    err_msg = str(exc)
                    print(
                        f"  [ERROR] Variant {variant} Rep {rep} Case {case.case_id} failed: {exc}"
                    )
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
                    char_count = 0
                    inv_check = InvariantCheckResult(
                        passed=False,
                        schema_valid=False,
                        all_outcomes_linked=False,
                        stopping_point_respected=False,
                        no_forbidden_phrases=True,
                        details=[f"Generation exception: {exc}"],
                    )

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
                    ABCaseRecording(
                        variant=variant,
                        case_id=case.case_id,
                        category=case.category,
                        title=case.title,
                        repetition=rep,
                        user_input=case.user_input,
                        output=output_data,
                        token_usage=tokens,
                        latency_ms=round(latency_ms, 2),
                        char_count=char_count,
                        error=err_msg,
                        invariant_check=inv_check,
                    )
                )

    if only_case_id and reuse_a_path and reuse_a_path.exists():
        old_data = json.loads(reuse_a_path.read_text(encoding="utf-8"))
        old_exp = ABExperimentRecordings.model_validate(old_data)
        merged = [r for r in old_exp.recordings if r.case_id != only_case_id]
        merged.extend(recordings)
        recordings = merged
        print(f"Merged updated recordings for {only_case_id} with remaining cases (Total: {len(recordings)})")

    model_name = getattr(gateway, "model", None) or (
        gateway.provider._default_model if hasattr(gateway, "provider") and hasattr(gateway.provider, "_default_model") else ""
    )
    fixtures_hash = compute_file_hash(DEFAULT_FIXTURES)
    prompt_a_fp = compute_prompt_fingerprint(gateway.prompt_loader, task_a)
    prompt_b_fp = compute_prompt_fingerprint(gateway.prompt_loader, task_b)

    return ABExperimentRecordings(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        model=str(model_name),
        task_a=task_a,
        task_b=task_b,
        fixtures_hash=fixtures_hash,
        prompt_a_fingerprint=prompt_a_fp,
        prompt_b_fingerprint=prompt_b_fp,
        case_ids=[case.case_id for case in filtered_cases],
        repetitions=repetitions,
        temperature=0.8,
        total_calls=len(recordings),
        recordings=recordings,
    )


def generate_blind_eval_package(
    fixtures: list[NarrativeTestCase],
    recordings_data: ABExperimentRecordings,
    output_dir: Path,
    mapping_path: Path,
    seed: int = 42,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)

    failed = [
        f"{recording.case_id}/{recording.variant}/rep{recording.repetition}"
        for recording in recordings_data.recordings
        if recording.error
        or recording.invariant_check is None
        or not recording.invariant_check.passed
    ]
    if failed:
        raise ValueError(
            "Blind evaluation package cannot include failed recordings: "
            + ", ".join(failed)
        )

    rec_index: dict[tuple[str, str, int], ABCaseRecording] = {
        (r.case_id, r.variant, r.repetition): r for r in recordings_data.recordings
    }

    total_pairs = len(fixtures) * 3
    num_left_a = total_pairs // 2
    num_left_b = total_pairs - num_left_a
    left_a_flags = [True] * num_left_a + [False] * num_left_b
    rng = random.Random(seed)
    rng.shuffle(left_a_flags)

    pair_cursor = 0
    mappings: dict[str, list[dict[str, Any]]] = {}

    for case in fixtures:
        case_pairs_mapping: list[dict[str, Any]] = []
        pairs_markdown_blocks: list[str] = []

        for rep in range(1, 4):
            rec_a = rec_index.get((case.case_id, "A", rep))
            rec_b = rec_index.get((case.case_id, "B", rep))
            if not rec_a or not rec_b:
                raise ValueError(
                    f"Missing recording for {case.case_id} rep {rep} (A: {bool(rec_a)}, B: {bool(rec_b)})"
                )

            is_a_left = left_a_flags[pair_cursor]
            pair_cursor += 1

            left_var = "A" if is_a_left else "B"
            right_var = "B" if is_a_left else "A"
            left_rec = rec_a if is_a_left else rec_b
            right_rec = rec_b if is_a_left else rec_a

            case_pairs_mapping.append(
                {
                    "repetition": rep,
                    "left": left_var,
                    "right": right_var,
                }
            )

            left_scene = left_rec.output.get("scene_text", "").strip()
            left_closing = left_rec.output.get("closing_prompt", "").strip()
            right_scene = right_rec.output.get("scene_text", "").strip()
            right_closing = right_rec.output.get("closing_prompt", "").strip()

            pair_block = f"""### Pair {rep}

#### [Left Output]
{left_scene}

**Closing Prompt**: {left_closing}

#### [Right Output]
{right_scene}

**Closing Prompt**: {right_closing}"""
            pairs_markdown_blocks.append(pair_block)

        mappings[case.case_id] = case_pairs_mapping

        recent_text = "无"
        if case.recent_scene:
            recent_text = case.recent_scene.scene_text

        md_content = f"""# 叙事质量成对盲评任务 ({case.case_id} - {case.title})

你是一位极其严苛挑剔的文字冒险游戏（TTRPG / 交互叙事）专家评委。
你的任务是在完全双盲的情况下，对比评测同一下上文中生成的两组叙事正文（Left 与 Right），判断哪一组在戏剧张力、行动启发力与叙事沉浸感上表现更优。

---

## 背景上下文（权威事实）

### 1. 核心记忆与世界设定 (Core Memory)
```json
{case.core_memory.model_dump_json(indent=2)}
```

### 2. 本回合上下文事实 (Context Facts)
```json
{project_context_facts(case.context_facts)}
```

### 3. 玩家行动输入 (Player Input)
```text
{case.user_input}
```

### 4. 回合判定结果 (Turn Resolution)
```json
{case.turn_resolution.model_dump_json(indent=2)}
```

### 5. 当前场景与在场角色 (Current State)
```json
{project_current_state(case.current_state)}
```

### 6. 在场角色认知视图 (NPC Profiles)
```json
{json.dumps([p.model_dump(mode="json") for p in case.npc_profiles], ensure_ascii=False, indent=2)}
```

### 7. 前一确认场景 (Previous Confirmed Scene)
```text
{recent_text}
```

---

## 创作与事实边界

正文允许自由创造未在上下文中逐字列出的新对白、环境感官、NPC反应、人际动态、地点与线索细节。
判断违规的唯一依据是下方的「硬违规定义」；只要不与已给出的权威事实冲突、不改写判定成败、不替玩家作出新决定，均属合法创作。

---

## 盲评输出协议与评分标准

### 1. 评分维度（各项取值严格为 `"left"` | `"right"` | `"tie"`）
六个偏好字段独立依据正文的叙事质量、戏剧张力与游戏体验评判：
- `overall_preference`：作为玩家总体更愿意读哪一篇；
- `continue_desire`：哪一篇更让玩家想立即输入下一步行动；
- `open_affordance`：哪一篇在完成本轮既定结果后，更自然地支持多种策略不同的后续行动，而非狭隘闭环或菜单式是非题；
- `surprising_but_coherent`：意外性是否自然且符合情理逻辑；
- `npc_world_agency`：NPC 与世界是否展现出自主诉求与真实鲜活度；
- `specificity`：是否提供可具体利用的人物、地点、筹码、代价或物证线索。

### 2. 独立评判与实证引用 (`left_evidence` / `right_evidence` / `reason`)
- 每个 Pair 必须完全独立对比评判，严禁跨 Pair 混淆或引用其他 Pair 的内容；
- `left_evidence`：必须从本 Pair 的 [Left Output] 正文中精确复制一段代表性短句（5-30字）；
- `right_evidence`：必须从本 Pair 的 [Right Output] 正文中精确复制一段代表性短句（5-30字）；
- `reason`：结合上述细节，直接对比两篇在人物鲜活性、场景氛围与行动启发空间上的优劣。

### 3. 硬违规定义 (`left_violations` / `right_violations`)
仅在出现以下 6 类破坏性硬伤时记录字符串描述，无违规填 `[]`：
1. 违背已提供的既定事实（Core Memory、Context Facts、世界时钟等）；
2. 改写 TR 成败或 resolved fact；
3. 替玩家作出 Player Input 之外新的、具有实质后果的决定；
4. 消耗或赋予与 Current State 冲突的玩家资源；
5. 无授权地把明确标记为隐藏/幕后的信息写成角色已知或客观事实；
6. 违反地点、时间、人物在场等物理与空间连续性。

请严格返回如下格式的 JSON（不得包含 Markdown 代码块外的任何文字）：

```json
{{
  "case_id": "{case.case_id}",
  "pairs": [
    {{
      "repetition": 1,
      "overall_preference": "<left | right | tie>",
      "continue_desire": "<left | right | tie>",
      "open_affordance": "<left | right | tie>",
      "surprising_but_coherent": "<left | right | tie>",
      "npc_world_agency": "<left | right | tie>",
      "specificity": "<left | right | tie>",
      "left_evidence": "<精确复制本Pair的Left正文短句(5-30字)>",
      "right_evidence": "<精确复制本Pair的Right正文短句(5-30字)>",
      "left_violations": [],
      "right_violations": [],
      "reason": "<引用两篇具体文本差异对比人物鲜活性、场景氛围与行动启发空间>"
    }},
    {{
      "repetition": 2,
      "overall_preference": "<left | right | tie>",
      "continue_desire": "<left | right | tie>",
      "open_affordance": "<left | right | tie>",
      "surprising_but_coherent": "<left | right | tie>",
      "npc_world_agency": "<left | right | tie>",
      "specificity": "<left | right | tie>",
      "left_evidence": "<精确复制本Pair的Left正文短句(5-30字)>",
      "right_evidence": "<精确复制本Pair的Right正文短句(5-30字)>",
      "left_violations": [],
      "right_violations": [],
      "reason": "<引用两篇具体文本差异对比人物鲜活性、场景氛围与行动启发空间>"
    }},
    {{
      "repetition": 3,
      "overall_preference": "<left | right | tie>",
      "continue_desire": "<left | right | tie>",
      "open_affordance": "<left | right | tie>",
      "surprising_but_coherent": "<left | right | tie>",
      "npc_world_agency": "<left | right | tie>",
      "specificity": "<left | right | tie>",
      "left_evidence": "<精确复制本Pair的Left正文短句(5-30字)>",
      "right_evidence": "<精确复制本Pair的Right正文短句(5-30字)>",
      "left_violations": [],
      "right_violations": [],
      "reason": "<引用两篇具体文本差异对比人物鲜活性、场景氛围与行动启发空间>"
    }}
  ]
}}
```

---

## 待评测正文对

""" + "\n\n---\n\n".join(pairs_markdown_blocks) + "\n"

        prompt_file = output_dir / f"{case.case_id}.md"
        prompt_file.write_text(md_content, encoding="utf-8")

    mapping_payload = {
        "version": 1,
        "seed": seed,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_cases": len(fixtures),
        "total_pairs": total_pairs,
        "mappings": mappings,
    }
    mapping_path.write_text(
        json.dumps(mapping_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return mapping_payload


def _clean_json_str(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def summarize_ab_judgments(
    mapping_path: Path,
    judgments_dir: Path,
    recordings_path: Path | None = None,
) -> ABSummaryReport:
    mapping_data = json.loads(mapping_path.read_text(encoding="utf-8"))
    mappings: dict[str, list[dict[str, Any]]] = mapping_data["mappings"]

    recordings_data: ABExperimentRecordings | None = None
    if recordings_path and recordings_path.exists():
        recordings_data = ABExperimentRecordings.model_validate_json(
            recordings_path.read_text(encoding="utf-8")
        )
    elif DEFAULT_AB_RECORDINGS.exists():
        recordings_data = ABExperimentRecordings.model_validate_json(
            DEFAULT_AB_RECORDINGS.read_text(encoding="utf-8")
        )

    rec_index: dict[tuple[str, str, int], ABCaseRecording] = {}
    if recordings_data:
        rec_index = {
            (r.case_id, r.variant, r.repetition): r for r in recordings_data.recordings
        }

    cases_summary: list[CaseSummary] = []
    dim_counts: dict[str, dict[str, int]] = {
        "overall_preference": {"A": 0, "B": 0, "tie": 0},
        "continue_desire": {"A": 0, "B": 0, "tie": 0},
        "open_affordance": {"A": 0, "B": 0, "tie": 0},
        "surprising_but_coherent": {"A": 0, "B": 0, "tie": 0},
        "npc_world_agency": {"A": 0, "B": 0, "tie": 0},
        "specificity": {"A": 0, "B": 0, "tie": 0},
    }

    total_pairs = 0
    total_a_pair_wins = 0
    total_b_pair_wins = 0
    total_tie_pairs = 0
    total_void_pairs = 0

    a_case_wins = 0
    b_case_wins = 0
    tie_cases = 0

    for case_id, case_maps in mappings.items():
        judge_file = judgments_dir / f"{case_id}.json"
        if not judge_file.exists():
            candidates = list(judgments_dir.glob(f"*{case_id}*.json"))
            if not candidates:
                parts = case_id.split("_")
                if len(parts) >= 2 and parts[1].isdigit():
                    candidates = list(judgments_dir.glob(f"*{parts[1]}*.json"))
            if candidates:
                judge_file = candidates[0]
            else:
                raise FileNotFoundError(f"Judgment file not found for case {case_id} in {judgments_dir}")

        raw_content = _clean_json_str(judge_file.read_text(encoding="utf-8"))
        try:
            raw_judge_data = json.loads(raw_content)
            parsed_judge = RawCaseJudgment.model_validate(raw_judge_data)
        except Exception as exc:
            raise ValueError(f"Invalid judgment format in {judge_file}: {exc}") from exc

        if parsed_judge.case_id != case_id:
            raise ValueError(
                f"Case ID mismatch in {judge_file}: expected '{case_id}', got '{parsed_judge.case_id}'"
            )

        judge_by_rep = {p.repetition: p for p in parsed_judge.pairs}

        case_pair_summaries: list[PairSummary] = []
        c_a_wins = 0
        c_b_wins = 0
        c_ties = 0
        c_voids = 0

        for map_item in case_maps:
            rep = map_item["repetition"]
            left_var = map_item["left"]
            right_var = map_item["right"]
            p_data = judge_by_rep[rep]

            # Validate left_evidence and right_evidence against recordings
            if rec_index:
                rec_left = rec_index.get((case_id, left_var, rep))
                rec_right = rec_index.get((case_id, right_var, rep))

                if rec_left:
                    left_text = f"{rec_left.output.get('scene_text', '')}\n{rec_left.output.get('closing_prompt', '')}"
                    if p_data.left_evidence not in left_text:
                        other_reps = [r for r in [1, 2, 3] if r != rep]
                        leaked_rep = None
                        for o_rep in other_reps:
                            o_rec = rec_index.get((case_id, left_var, o_rep)) or rec_index.get((case_id, right_var, o_rep))
                            if o_rec and p_data.left_evidence in f"{o_rec.output.get('scene_text', '')}\n{o_rec.output.get('closing_prompt', '')}":
                                leaked_rep = o_rep
                                break
                        if leaked_rep:
                            raise ValueError(
                                f"Cross-pair pollution detected in {judge_file.name} Pair {rep}: "
                                f"left_evidence '{p_data.left_evidence}' does not belong to Pair {rep}, "
                                f"but was found in Pair {leaked_rep}!"
                            )
                        raise ValueError(
                            f"Invalid left_evidence in {judge_file.name} Pair {rep}: "
                            f"'{p_data.left_evidence}' not found in target text."
                        )

                if rec_right:
                    right_text = f"{rec_right.output.get('scene_text', '')}\n{rec_right.output.get('closing_prompt', '')}"
                    if p_data.right_evidence not in right_text:
                        other_reps = [r for r in [1, 2, 3] if r != rep]
                        leaked_rep = None
                        for o_rep in other_reps:
                            o_rec = rec_index.get((case_id, right_var, o_rep)) or rec_index.get((case_id, left_var, o_rep))
                            if o_rec and p_data.right_evidence in f"{o_rec.output.get('scene_text', '')}\n{o_rec.output.get('closing_prompt', '')}":
                                leaked_rep = o_rep
                                break
                        if leaked_rep:
                            raise ValueError(
                                f"Cross-pair pollution detected in {judge_file.name} Pair {rep}: "
                                f"right_evidence '{p_data.right_evidence}' does not belong to Pair {rep}, "
                                f"but was found in Pair {leaked_rep}!"
                            )
                        raise ValueError(
                            f"Invalid right_evidence in {judge_file.name} Pair {rep}: "
                            f"'{p_data.right_evidence}' not found in target text."
                        )

            left_viols = p_data.left_violations
            right_viols = p_data.right_violations
            a_viols = left_viols if left_var == "A" else right_viols
            b_viols = right_viols if left_var == "A" else left_viols

            def map_vote(vote: Literal["left", "right", "tie"]) -> Literal["A", "B", "tie"]:
                if vote == "left":
                    return cast(Literal["A", "B", "tie"], left_var)
                if vote == "right":
                    return cast(Literal["A", "B", "tie"], right_var)
                return "tie"

            mapped_overall = map_vote(p_data.overall_preference)
            mapped_continue = map_vote(p_data.continue_desire)
            mapped_affordance = map_vote(p_data.open_affordance)
            mapped_surprise = map_vote(p_data.surprising_but_coherent)
            mapped_agency = map_vote(p_data.npc_world_agency)
            mapped_specificity = map_vote(p_data.specificity)

            if a_viols and not b_viols:
                pair_winner = "B"
            elif b_viols and not a_viols:
                pair_winner = "A"
            elif a_viols and b_viols:
                pair_winner = "void"
            else:
                pair_winner = mapped_overall

            if pair_winner == "B":
                c_b_wins += 1
                total_b_pair_wins += 1
            elif pair_winner == "A":
                c_a_wins += 1
                total_a_pair_wins += 1
            elif pair_winner == "tie":
                c_ties += 1
                total_tie_pairs += 1
            else:
                c_voids += 1
                total_void_pairs += 1
            total_pairs += 1

            if pair_winner != "void":
                dim_counts["overall_preference"][mapped_overall] += 1
                dim_counts["continue_desire"][mapped_continue] += 1
                dim_counts["open_affordance"][mapped_affordance] += 1
                dim_counts["surprising_but_coherent"][mapped_surprise] += 1
                dim_counts["npc_world_agency"][mapped_agency] += 1
                dim_counts["specificity"][mapped_specificity] += 1

            case_pair_summaries.append(
                PairSummary(
                    repetition=rep,
                    left_variant=left_var,
                    right_variant=right_var,
                    winner=pair_winner,
                    overall_preference=mapped_overall,
                    continue_desire=mapped_continue,
                    open_affordance=mapped_affordance,
                    surprising_but_coherent=mapped_surprise,
                    npc_world_agency=mapped_agency,
                    specificity=mapped_specificity,
                    left_evidence=p_data.left_evidence,
                    right_evidence=p_data.right_evidence,
                    a_violations=a_viols,
                    b_violations=b_viols,
                    reason=p_data.reason,
                )
            )

        if c_b_wins >= 2:
            case_winner = "B"
            b_case_wins += 1
        elif c_a_wins >= 2:
            case_winner = "A"
            a_case_wins += 1
        else:
            case_winner = "tie"
            tie_cases += 1

        cases_summary.append(
            CaseSummary(
                case_id=case_id,
                winner=case_winner,
                a_pair_wins=c_a_wins,
                b_pair_wins=c_b_wins,
                tie_pairs=c_ties,
                void_pairs=c_voids,
                pairs=case_pair_summaries,
            )
        )

    resource_stats: dict[str, Any] = {"A": {}, "B": {}}
    if recordings_path and recordings_path.exists():
        raw_recs = json.loads(recordings_path.read_text(encoding="utf-8"))
        exp_recs = ABExperimentRecordings.model_validate(raw_recs)
        for var in ["A", "B"]:
            var_recs = [r for r in exp_recs.recordings if r.variant == var and not r.error]
            if var_recs:
                n = len(var_recs)
                resource_stats[var] = {
                    "char_count": round(sum(r.char_count for r in var_recs) / n, 2),
                    "prompt_tokens": round(sum(r.token_usage.get("prompt_tokens", 0) for r in var_recs) / n, 2),
                    "completion_tokens": round(sum(r.token_usage.get("completion_tokens", 0) for r in var_recs) / n, 2),
                    "total_tokens": round(sum(r.token_usage.get("total_tokens", 0) for r in var_recs) / n, 2),
                    "latency_ms": round(sum(r.latency_ms for r in var_recs) / n, 2),
                }

    total_cases = len(cases_summary)
    win_rate = round(b_case_wins / (total_cases or 1), 4)

    return ABSummaryReport(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        total_cases=total_cases,
        a_case_wins=a_case_wins,
        b_case_wins=b_case_wins,
        tie_cases=tie_cases,
        b_case_win_rate=win_rate,
        total_pairs=total_pairs,
        a_pair_wins=total_a_pair_wins,
        b_pair_wins=total_b_pair_wins,
        tie_pairs=total_tie_pairs,
        void_pairs=total_void_pairs,
        dimension_wins=dim_counts,
        resource_stats=resource_stats,
        cases=cases_summary,
    )


def print_ab_summary_table(report: ABSummaryReport) -> None:
    print("\n" + "=" * 90)
    print("           NARRATIVE COMPOSER A/B EXPERIMENT SUMMARY REPORT")
    print("=" * 90)
    print(f"Timestamp:             {report.timestamp}")
    print(f"Total Cases:           {report.total_cases}")
    print(f"Case-level Wins:       Candidate (B): {report.b_case_wins} | Baseline (A): {report.a_case_wins} | Tie: {report.tie_cases}")
    print(f"Candidate Win Rate:    {report.b_case_win_rate * 100:.1f}%")
    print(f"Total Evaluated Pairs: {report.total_pairs}")
    print(f"Pair-level Wins:       Candidate (B): {report.b_pair_wins} | Baseline (A): {report.a_pair_wins} | Tie: {report.tie_pairs} | Void: {report.void_pairs}")
    print("-" * 90)
    print("DIMENSIONAL WINS (Across all non-void pairs):")
    print(f"{'Dimension':<30} | {'Baseline (A)':<15} | {'Candidate (B)':<15} | {'Tie':<10}")
    print("-" * 90)
    for dim_name, counts in report.dimension_wins.items():
        print(f"{dim_name:<30} | {counts.get('A', 0):<15} | {counts.get('B', 0):<15} | {counts.get('tie', 0):<10}")
    print("-" * 90)
    print("RESOURCE & EFFICIENCY COMPARISON (Average per generation):")
    print(f"{'Metric':<30} | {'Baseline (A)':<15} | {'Candidate (B)':<15} | {'Delta (B - A)':<15}")
    print("-" * 90)
    res = report.resource_stats
    for metric, key in [
        ("Scene Text Length (chars)", "char_count"),
        ("Prompt Tokens", "prompt_tokens"),
        ("Completion Tokens", "completion_tokens"),
        ("Total Tokens", "total_tokens"),
        ("Latency (ms)", "latency_ms"),
    ]:
        val_a = res.get("A", {}).get(key, 0.0)
        val_b = res.get("B", {}).get(key, 0.0)
        delta = val_b - val_a
        delta_str = f"{delta:+.2f}" if delta != 0 else "0.00"
        print(f"{metric:<30} | {val_a:<15.2f} | {val_b:<15.2f} | {delta_str:<15}")
    print("-" * 90)
    print("CASE-BY-CASE BREAKDOWN:")
    print(f"{'Case ID':<35} | {'Winner':<8} | {'Pair Wins (B-A-Tie)':<22}")
    print("-" * 90)
    for c in report.cases:
        pair_str = f"{c.b_pair_wins}B - {c.a_pair_wins}A - {c.tie_pairs}Tie"
        if c.void_pairs:
            pair_str += f" ({c.void_pairs}Void)"
        print(f"{c.case_id:<35} | {c.winner:<8} | {pair_str:<22}")
    print("=" * 90 + "\n")


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
        help="Run single-version real generation and record outputs",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Run single-version LLM-as-a-Judge on recorded outputs",
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

    # A/B Testing Arguments
    parser.add_argument(
        "--ab-record",
        action="store_true",
        help="Run interleaved A/B experiment recordings (54 calls)",
    )
    parser.add_argument(
        "--ab-pack",
        action="store_true",
        help="Generate balanced blind evaluation Markdown prompts and mapping.json",
    )
    parser.add_argument(
        "--ab-summary",
        action="store_true",
        help="Compute de-blinded summary report from web judgments directory",
    )
    parser.add_argument(
        "--task-a",
        type=str,
        default="narrative_compose",
        help="Task / Prompt name for Variant A (Baseline)",
    )
    parser.add_argument(
        "--task-b",
        type=str,
        default="narrative_compose_open_affordance",
        help="Task / Prompt name for Variant B (Candidate)",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=3,
        help="Number of repetitions per case per variant (default: 3)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for blind eval left/right shuffling (default: 42)",
    )
    parser.add_argument(
        "--prompts-dir",
        type=Path,
        default=DEFAULT_EVAL_PROMPTS_DIR,
        help="Directory to save generated blind eval Markdown files",
    )
    parser.add_argument(
        "--mapping-path",
        type=Path,
        default=DEFAULT_MAPPING_PATH,
        help="Path to save or read blind eval mapping JSON",
    )
    parser.add_argument(
        "--judgments-dir",
        type=Path,
        default=DEFAULT_JUDGMENTS_DIR,
        help="Directory containing returned judgment JSON files",
    )
    parser.add_argument(
        "--reuse-a",
        type=Path,
        nargs="?",
        const=DEFAULT_AB_RECORDINGS,
        default=None,
        help="Reuse existing Variant A recordings from JSON file (default: recordings/ab_experiment.json) and only run Variant B calls",
    )

    args = parser.parse_args()

    if args.compare:
        compare_reports(args.baseline, args.compare)
        return

    fixtures = load_fixtures(args.fixtures)

    # A/B Flow 1: Record calls
    if args.ab_record:
        gateway = create_gateway(model=args.model)
        ab_recordings = run_ab_recording(
            fixtures,
            gateway,
            task_a=args.task_a,
            task_b=args.task_b,
            repetitions=args.reps,
            only_case_id=args.only,
            reuse_a_path=args.reuse_a,
        )
        out_path = args.output or DEFAULT_AB_RECORDINGS
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            ab_recordings.model_dump_json(indent=2),
            encoding="utf-8",
        )
        manifest_path = out_path.with_name("manifest.json")
        manifest = ab_recordings.model_dump(exclude={"recordings"})
        manifest["recordings_file"] = out_path.name
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Recorded {len(ab_recordings.recordings)} A/B calls to {out_path}")
        return

    # A/B Flow 2: Export blind eval package
    if args.ab_pack:
        rec_path = args.output or (args.recordings if args.recordings != DEFAULT_RECORDINGS else DEFAULT_AB_RECORDINGS)
        if not rec_path.exists():
            print(f"AB Recordings file not found: {rec_path}. Please run --ab-record first.")
            sys.exit(1)
        raw_recs = json.loads(rec_path.read_text(encoding="utf-8"))
        exp_recordings = ABExperimentRecordings.model_validate(raw_recs)
        generate_blind_eval_package(
            fixtures,
            exp_recordings,
            output_dir=args.prompts_dir,
            mapping_path=args.mapping_path,
            seed=args.seed,
        )
        print(
            f"Exported {len(fixtures)} blind eval Markdown prompts to {args.prompts_dir} "
            f"and saved secret mapping to {args.mapping_path}"
        )
        return

    # A/B Flow 3: Summarize judgments
    if args.ab_summary:
        if not args.mapping_path.exists():
            print(f"Mapping file not found: {args.mapping_path}. Please run --ab-pack first.")
            sys.exit(1)
        if not args.judgments_dir.exists():
            print(f"Judgments directory not found: {args.judgments_dir}.")
            sys.exit(1)

        rec_path = args.recordings if args.recordings != DEFAULT_RECORDINGS else DEFAULT_AB_RECORDINGS
        summary_report = summarize_ab_judgments(
            mapping_path=args.mapping_path,
            judgments_dir=args.judgments_dir,
            recordings_path=rec_path if rec_path.exists() else None,
        )
        print_ab_summary_table(summary_report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                summary_report.model_dump_json(indent=2),
                encoding="utf-8",
            )
            print(f"Saved summary report to {args.output}")
        return

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

        passed, messages = check_recordings(fixtures, recs)
        for msg in messages:
            print(msg)

        if not passed:
            sys.exit(1)
        print("All invariant checks passed.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
