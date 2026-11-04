# TR 与 Composer 合并实验

10 个用例，每侧各 3 次，60 次生成、30 对比较。候选胜 20 对，按用例为 5–5；平均总 Token 下降 43.5%，总延迟下降 34.4%。旧双阶段实现已退出运行管线。

| 轮次 | 运行记录 | 结果 |
|---|---|---|
| formal | [运行标识](results/formal/manifest.json) · [原始调用（本地）](../../local/tr-composer-unification/formal/recordings.json) | [评分汇总](results/formal/report.json) · [性能统计](results/formal/metrics.json) |

[盲评映射](results/formal/mapping.json) · [逐例评分理由](results/formal/judgments)

本轮使用的是当时版本的十例；复查读取运行记录中的输入，不能用当前 01/02 替代。运行标识、盲评映射、评分和汇总纳入 Git；正文、逐例盲评题包与完整调用集中保存在 `local/tr-composer-unification/`。
