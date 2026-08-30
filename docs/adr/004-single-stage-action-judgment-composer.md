# 4. 以确定性 Action Judgments 驱动单阶段 Composer

**Date**: 2026-08-30  
**Status**: Accepted

## Context (上下文与背景)

原生成管线先调用 Turn Resolution LLM 把骰子判定改写为结果事实，再调用
Composer 规划并写作正文。两阶段的规划语义重叠，后一次调用容易刻板复述
前一次的输出，同时增加 Token 和延迟。成败档位本已由程序根据骰子决定，
不需要第二个 LLM 重复判定。

10 个用例、每个 3 次重复的真机 A/B 显示，单阶段候选方案在 30 对中 20 胜，
用例级 5:5，平均总 Token 下降 43.5%，平均总延迟下降 34.4%。

## Decision (架构决策)

1. Python 为每个原子行动生成 `ActionJudgment`，其 `outcome_band` 是 Composer 不可修改的权威输入。
2. 一次 `NarrativeComposerAgent` 调用同时产生具体结果事实、场景停止点、计划事件与正文。删除独立 LLM Turn Resolution 阶段。
3. Reroll 重算骰子与 Action Judgments 后重跑 Composer。Rephrase 用原权威上下文重跑整个 Composer，不固定前一版 LLM 的中间输出。
4. 不保留旧 TR+Composer 运行时或 Benchmark 兼容管线。历史双盲录制和判决仅作为已完成决策的证据归档。
5. Composer 无法在 Gateway 重试后产生合法完整结果时，失败向上传播，不构造空正文 pending turn。

## Consequences (后果与权衡)

- Composer 拥有更大的创作空间，同时成败边界仍由程序保证。
- 只维护一套生成 schema、prompt、运行图与测试契约。
- Rephrase 可能重新创作具体结果展开，但不得改变原行动或程序判定的成败档位。
- 旧 pending turn 的中间 schema 不再作为兼容目标；确认后的正文与玩家判定投影仍保持原持久化边界。
