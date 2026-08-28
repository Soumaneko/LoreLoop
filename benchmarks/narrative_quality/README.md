# Narrative Quality Benchmark (叙事质量基准评测)

本 Benchmark 用于评估与对比 **叙事合成器（Narrative Composer）** 在多题材场景下的生成质量与行动空间，提供 100% 贴合生产输入契约的自动化评估、确定性硬性检查与基准评测流程。

---

## 核心设计

1. **真实生产管线执行**：
   - 输入数据严格采用生产 Pydantic 数据契约（`CoreMemory`, `CurrentState`, `TurnResolutionResult`, `ContextFactSet`, `NPCProfileDirectorView`, `DirectorStageResult`, `ShortTermScene`）；
   - 执行逻辑直接调用生产代码 `run_narrative_composition`（加载 `app/prompts/generation/narrative_compose_v1.md` 模板，使用真实生成温度 `temperature=0.8`）。

2. **评估机制**：
   - **当前支持：确定性门禁（Deterministic Invariant Gate - Pass/Fail）**：
     - `schema_valid`：JSON 输出符合 `NarrativeComposerResult` 结构契约；
     - `all_outcomes_linked`：所有 Turn Resolution outcome 均被 `planned_events` 关联覆盖；
     - `stopping_point_respected`：`scene_text` 与 `scene_stopping_point` 均非空；
     - `no_forbidden_phrases`：正文未泄漏已登记的系统内部字段名（如 `CoreMemory`, `TurnResolutionResult` 等）。
   - **当前支持：单篇 LLM-Judge 评分（绝对分打分）与报告比较**：
     - 使用 `--judge` 对录制结果打分并生成包含各维度均分的 `BenchmarkReport`；
     - 使用 `--compare` 比较两份 `BenchmarkReport`。
    - **成对双盲 A/B 盲评体系（Issue 01 实现）**：
      - `--ab-record`：54 次交错录制（9 cases × 2 variants × 3 reps），记录耗时、Token 与字符数；
      - `--ab-pack`：导出 9 份独立的双盲 Markdown 评测提示词，平衡随机打乱左右顺序（全局差 $\le 1$），并生成本地隔离的 `mapping.json`；
      - `--ab-summary`：读取裁判返回的 JSON 结果，执行硬性语义违规一票否决与 3 局 2 胜多数决，输出胜率与多维度资源对比表。

---

## 文件结构

- `fixtures.json`：9 个覆盖仙侠、日式西幻、正统西幻、现代刑侦、废土、蒸汽朋克、丧尸生存、赛博朋克、太空科幻等多元题材的标准测试用例；
- `baseline_legacy.json`：重构前的旧版 10 案例基准存档（已废弃标记为 legacy，仅作历史参考）；
- `recordings/`：（git 忽略）保存各实验版本运行生成的原始输出与 Token 记录；
- `recordings/manifest.json`：保存本次 A/B 的模型、案例、重复次数、温度、fixture hash 与实际展开 Prompt fingerprint；
- `eval_prompts/`：（git 忽略）存放 `--ab-pack` 生成的双盲评测 Markdown 文件与 `mapping.json`；
- `judgments/`：（git 忽略）存放裁判返回的 9 份 JSON 评测结果。

---

## 常用命令

### 1. 成对 A/B 实验工作流（推荐）

```bash
# 步骤 1：交错录制 54 次生成（Variant A vs Variant B）
python scripts/benchmark_narrative_quality.py --ab-record

# 步骤 2：打包双盲评测 Markdown 与 mapping.json
python scripts/benchmark_narrative_quality.py --ab-pack

# 步骤 3：将网页端 LLM 裁判的 JSON 结果放入 benchmarks/narrative_quality/judgments/ 并生成汇总报告
python scripts/benchmark_narrative_quality.py --ab-summary
```

### 2. 离线合规检查（无需 LLM 调用）
```bash
python scripts/benchmark_narrative_quality.py --check --recordings benchmarks/narrative_quality/recordings/baseline.json
```

### 3. 单版本录制与绝对分 Judge 评分（旧版流程）
```bash
# 录制单版本
python scripts/benchmark_narrative_quality.py --record --output benchmarks/narrative_quality/recordings/baseline.json

# 单版本绝对分 Judge 评分
python scripts/benchmark_narrative_quality.py --judge --recordings benchmarks/narrative_quality/recordings/baseline.json --output benchmarks/narrative_quality/baseline.json

# 对比两份单版本绝对分报告
python scripts/benchmark_narrative_quality.py --compare benchmarks/narrative_quality/candidate_report.json --baseline benchmarks/narrative_quality/baseline_report.json
```
