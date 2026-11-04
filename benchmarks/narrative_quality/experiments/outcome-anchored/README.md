# 结果锚定 Composer 实验

结论：候选未采用。A 使用生产 Composer，B 删除前置规划，在行动结果之后直接输出正文；同时改变 Prompt 与 Schema。

正式 10 例 × 每侧 3 次，共 60 次生成、30 对，全部返回、无重试。原始偏好 A 8 胜、B 10 胜、12 平；质量均分 12.000/16 与 12.033/16。B 正文字符增长 3.7%、结构字符下降 49.5%、耗时下降 18.4%，未稳定改善叙事。Token 用量未返回。

| 轮次 | 用例 / 比较 | 结果位置 |
|---|---|---|
| formal-ocgo-20261101-01 | 10 例 × 每侧 3 次 | [正式小结](results/formal-20261101/summary.md) · [评分与统计](results/formal-20261101/report.json) |

正式轮的[统计](results/formal-20261101/results.json)、[评分理由](results/formal-20261101/judgments)、[解盲复核](results/formal-20261101/reason_review.md)及 [通用 Prompt／Schema 前后版本](results/formal-20261101/comparison)随结果入库。完整正文、逐例盲评题包和冻结的用例副本保存在 `local/outcome-anchored/`。

[原始结案归档（本地）](../../local/outcome-anchored/closed-20261101/README.md)保留完整调用、源码快照及历史工具。此前五次单例预试及其人工反馈保留在本地，作为准备过程记录。

实际评分在一个 Codex 会话完成，没有按约定逐例新开网页对话；解盲复核未覆盖原始分数。原版也未在这些用例中稳定触发防御性退化，因此本轮未能检验对该问题的有效修复。历史原始调用仅在本地保存。
