# NPC 使用记录实验

**正式 A/B 已完成评分、复核和解盲；保留本次人物使用记录改动。**

目标：加入 `npc_usages`，先验证人物使用记录，再通过 A/B 盲评检查正文质量。`director_reason` 和正文前的规划结构保持原样。

选取 **6 例**：01 孙掌柜、02 艾莉丝、11 赵七进店、12 事件介入谈心、13 旁观事件推进、14 差役敲门。[用例目录](../../cases/) · [运行套件](suite.json)

普通验证：已完成三批单例验证、两次六例试跑。六例输入修正后均首轮返回；发现局部连续性问题和称呼归纳偏差。随后尝试将 surfaces 改为原文摘取，复查仍有拼接与漏记，该措辞修改已回滚。调用及检查记录集中在 `local/npc-usages/`。

## 普通验证调用

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmark_narrative_quality.py --record \
  --fixtures benchmarks/narrative_quality/experiments/npc-usages/suite.json \
  --service opencode-go \
  --run-dir benchmarks/narrative_quality/local/npc-usages/smoke-YYYYMMDD-NN
```

使用新的本地目录。正式 A/B 按确定的版本、用例、重复次数和评分标准执行后，再收录正式结果。

## 正式 A/B：已解盲

十例已固定为 **01、02、03、04、05、08、11、12、13、14**。新增四例从剩余八例以种子 42 一次抽选，来源池、抽样结果和基线提交见 [plan.json](plan.json)。`suite.json` 继续表示普通验证的六例，正式脚本读取 `plan.json` 的十例。

| 项目 | 固定设置 |
|---|---|
| A | 新增字段前的 Composer，基线提交 `7adb0398f4d7fa4d1475c8b0d85879321a88e1b9` |
| B | 姓名版 `npc_usages`，使用已恢复的措辞；后续复现固定到 `51766d5`，避免新字段混入旧实验 |
| 差异 | Prompt 增加字段说明，Schema 增加该字段；其余 Prompt 内容和用例输入一致 |
| 次数 | 每例每版本 3 次，共 60 次逻辑调用、30 对比较 |
| 模型 | OpenCode Go / deepseek-v4.1-flash，temperature 0.8，沿用一次结构重试 |
| 调用顺序 | 预先随机排列；每对连续调用，两侧各有 15 对先调用 |
| 盲评 | 左右随机且总体平衡，每例 3 对；实际由用户指定 Codex 完成，逐例会话隔离情况未记录 |

[Prompt 前后版本](comparison/)为通用模板，未填入具体用例。准备目录 `local/npc-usages/formal-20261104-01/` 已冻结十例输入、两侧源码、Prompt、Schema、评分说明与调用顺序；本轮已完成 60 次生成、6 次结构重试，零失败；十份盲评题包共 30 对，无排除样本。本地题包位于 `blind/`，打包文件为 `blind-packets.zip`。运行时按隔离模块加载两侧源码，各自使用自己的 Schema；共享生产 Gateway 和基础工具。

### 评分与统计

沿用[评分说明](judge.md)：场景构建、因果互动、正文展开、可继续游玩，各 0–4 分，总分 16。先独立评价两篇，再给出偏好。每项附对应正文引文和理由，违规按类别与严重程度单列。

盲评题包提供共同权威上下文和左右正文，隐藏版本、人物使用记录、规划输出和调用统计。原计划逐例独立网页会话，实际改为用户指定 Codex 评分；具体部署型号、逐例会话隔离和原始评分日期未记录，已如实记入报告。2026-11-05 完成用户确认的评分复核与解盲。

辅助统计正文字符数（含标点换行）、非空白字符数、结构输出和人物记录字符数、耗时、正文开始时间、Token、重试及可获得的结束信息。Token 和结束信息未返回时保持缺失，不用字符数代替。人物记录对应关系另行离线观察；正文评分围绕四项质量指标、偏好和违规，字数不作为单独结论。

## 本轮结果

A 为新增字段前的原版，B 为当前 `npc_usages` 版本。[完整统计、评分理由和复核记录](results/formal-20261104-01/report.json)。

| 指标 | A 原版 | B 新增人物记录 |
|---|---:|---:|
| 配对胜场 / 30 对 | 9 | 10 |
| 用例胜场 / 10 例 | 2 | 2 |
| 质量均分 / 16 | 12.40 | 12.57 |
| 正文字符均值（含标点换行） | 654.3 | 697.9 |
| 平均耗时（秒，含重试） | 17.49 | 17.16 |
| 重试次数 | 4 | 2 |
| 重大问题数（含叙事视角） | 1 | 3 |
| 局部问题数 | 7 | 4 |

配对平局 11 对，用例平局 6 例。B 正文平均增加 6.7%，四项质量总分仅高 0.17 分，配对偏好接近。Token、结束原因和截断信息未返回。

复核在读取版本映射前完成：03 第1对右篇场景构建 2→3、偏好改为平局；删除 04 第1对右篇普通装袋取证的越权判定；补记 12 第2对左篇店小二在场却声称跑堂全由自己承担的局部冲突。04 第1对右篇全篇第三人称被用户确认为重大呈现问题，单列记录，偏好改为左篇，四项质量分保持；解盲后确认该篇为 B。原评分副本留在本地，报告中保留修改记录。

解盲后讨论：用户认为正文质量基本无变化，先提交本次改动。人称问题暂按前文示范不足搁置；招募问题应结合行为与系统收尾判断，傲娇台词本身不直接等同于招募失败，后续引导应承接已达成的结果。上表分数和问题计数保留此前复核时状态，没有根据这次讨论重评分；详细说明已随报告保存。

### 执行步骤

脚本：[`scripts/benchmark_npc_usages_ab.py`](../../../../scripts/benchmark_npc_usages_ab.py)。本轮已完成。以下保留各阶段命令供复查。

```bash
# 正式录制。中断后再次执行，只续跑同一冻结批次中尚未记录的调用。
PYTHONPATH=. .venv/bin/python scripts/benchmark_npc_usages_ab.py record \
  --run-dir benchmarks/narrative_quality/local/npc-usages/formal-20261104-01

# 全部录制完成后，生成十份盲评题包。
PYTHONPATH=. .venv/bin/python scripts/benchmark_npc_usages_ab.py pack \
  --run-dir benchmarks/narrative_quality/local/npc-usages/formal-20261104-01
```

题包在该目录 `blind/`，每例的返回 JSON 保存到 `judgments/<case_id>.json`。盲评时只读取题包，保持 `mapping.json` 和 `recordings.json` 隔离。正式评分全部完成并确认后，再执行解盲统计；以下评委名称与日期按实际填写：

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmark_npc_usages_ab.py report \
  --run-dir benchmarks/narrative_quality/local/npc-usages/formal-20261104-01 \
  --judge 'Codex（用户提供评分，具体型号未记录）' --date 2026-11-05 --mode codex-session
```

失败调用保留，失败所在比较单列为排除；不以额外生成替换。`report` 检查评分是否齐全、引文是否来自对应正文，并输出分数、偏好、逐例表现、违规和性能统计。后续运行的完整报告默认写入本轮 `local/.../analysis/`，连同快照、正文和实际输入一起保留在本地；仓库只更新实验计划和精简总结。上方已提交的历史结果保持原位置。

以后开启新批次时先准备新目录：

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmark_npc_usages_ab.py prepare \
  --run-dir benchmarks/narrative_quality/local/npc-usages/formal-YYYYMMDD-NN
```

准备和打包均使用新目录，已有输入与结果不会被覆盖。如果中断调用已留下目录但尚未写入汇总，先检查该目录原始 trace，脚本会停止而非自动重复请求。
