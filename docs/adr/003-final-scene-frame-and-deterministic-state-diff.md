# 3. 以落幕终态快照驱动确定性状态差分

**Date**: 2026-08-28  
**Status**: Accepted

## Context (上下文与背景)

CurrentState 更新曾要求 LLM 直接输出地点、人员和时钟的增量操作。在一段正文经历多地点流转、NPC 间接进出或跨日时，这要求模型同时理解故事、计算集合差异和换算时段，容易遗留“幽灵在场”人员或错误的时钟步数。

Scene Facts 是面向多个下游的共享事实投影，但它会压缩原文，不一定保留判断场景落幕状态所需的完整时空脉络。将 StateUpdater 与 SceneFactExtractor 合并又会把当前状态、历史事实投影和其他下游的职责绑在一起。

## Decision (架构决策)

1. **确认正文是 StateUpdater 的主要观察材料**：StateUpdater 始终接收带稳定段落 ID 的完整确认正文。Scene Facts 可作为辅助压缩参考，但与正文冲突时以正文为准。
2. **LLM 负责语义理解与落幕定格**：它输出 `FinalSceneFrame`，描述正文结束时的最终地点、日期偏移、最终时段、在场 NPC 和离场 NPC。LLM 仍可输出态度、外观、资源与资产等需要语义判断的操作，但不直接生成地点、人员和时钟操作。
3. **Python 负责确定性求差**：程序将 `FinalSceneFrame` 编译为可审计的原子操作，包括地点变更、人员进出和世界时钟推进。地点变更时，旧在场 NPC 除非被明确列入最终在场名单，默认不跟随主角。时间推进使用日期偏移与目标时段查表计算，不由 LLM 计算相对步数。
4. **SceneFactExtractor 继续保持独立**：Scene Facts 继续服务于 NPC、Continuity、Director 等其他投影消费者，不与当前状态编译合并。
5. **局部无效结果不引入额外语义重试**：未知 evidence 先被清理；缺少有效 evidence 的 frame 或 operation 被丢弃并记录 warning，其他独立有效的结果继续处理。

## Consequences (后果与权衡)

### 积极影响

- LLM 专注于它更擅长的场景理解，程序承担可重复、可测试的集合和时间计算。
- 换地离场、人员别名归一和时钟推进具有一致规则，不再受模型的增量操作遗漏影响。
- 编译后的每个状态操作仍然带有证据和 warning，可用现有确认事务原子提交。
- Scene Facts 的共享契约与 CurrentState 的现时状态责任保持分离。

### 代价与限制

- StateUpdater 需要读取完整正文，输入 token 成本高于只读压缩后的 Scene Facts。
- 确定性引擎只能精确编译 frame，不能修正 LLM 对落幕场景本身的语义误判。
- Scene Facts 与 `FinalSceneFrame` 会对同一正文做不同目的的投影，这是为了保持下游责任边界而接受的重复计算。
