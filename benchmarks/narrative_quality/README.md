# Composer 叙事测试库

本文件是测试库的管理入口。实验计划与精简报告保存在各实验目录，完整执行材料集中在 local。

**14 个可复用用例，5 个实验。** 导演材料采用记录实验已完成六例 A/B 的评分与解盲，结果见下方实验索引。

## 文件目录

```text
narrative_quality/
├── README.md
├── cases/                             入库：14 个固定用例及来源
│   ├── standard.json                  构造十例清单
│   └── <case_id>/
│       ├── input.json
│       ├── source.json                有来源记录时
│       └── assets.json                有后台资源时
├── experiments/                       入库：实验计划、精简报告和结论
│   ├── director-usages/               README.md 精简报告、plan.json 实验设置
│   ├── npc-usages/
│   │   ├── README.md                  目标、十例 A/B 设置和操作入口
│   │   ├── suite.json                  普通验证六例
│   │   ├── plan.json                   正式十例、抽样种子与版本
│   │   ├── judge.md                    评分说明
│   │   └── comparison/                 通用 Prompt／Schema 前后版本
│   ├── tr-composer-unification/
│   ├── outcome-anchored/
│   └── open-affordance-legacy/
└── local/                             不入库：全部原始与临时材料
    ├── <实验>/<轮次>/                  调用、重试、整套快照、脚本
    └── sources/                       历史来源及提取过程
```

## 用例：14 个

每例输入只有一份，放在自己的目录内；来源和后台资源跟随该例。实验通过 `suite.json` 引用用例。01–10 为构造场景，11–14 来自游玩记录；来源不影响用例的命名和调用方式。

| 编号 | 场景 | 重点 | 导演输入 | NPC 使用记录正式十例 |
|---|---|---|---|---|
| [01](cases/case_01_tavern_inquiry/input.json) | 孙掌柜提供私下拍卖消息 | 事件演员、线索披露、后续调查空间 | `present_event`，孙掌柜为 actor | 是 |
| [02](cases/case_02_rogue_recruitment/input.json) | 招募艾莉丝 | 人物性格、顾虑与招募互动 | `introduce_npc` 艾莉丝 | 是 |
| [03](cases/case_03_composite_infiltrate_and_disarm/input.json) | 潜入、拆解机关、发信号 | 连续三项行动的顺序与覆盖 | 无 | 是 |
| [04](cases/case_04_secret_investigation/input.json) | 刑侦秘密调查 | 具体物证与调查方向 | 无 | 是 |
| [05](cases/case_05_faction_confrontation/input.json) | 势力关卡交锋 | 化解阻力、通过关卡 | 无 | 是 |
| [06](cases/case_06_dungeon_puzzle/input.json) | 地下机关谜题 | 无 NPC 的环境互动 | 无 | — |
| [07](cases/case_07_partial_success_alchemy/input.json) | 炼金部分成功 | 成功与代价同时成立 | 无 | — |
| [08](cases/case_08_failed_action_recovery/input.json) | 行动失败后应变 | 失败后果与撤退选择 | 无 | 是 |
| [09](cases/case_09_critical_failure_ambush/input.json) | 伏击中的大失败 | 严重劣势的兑现 | 无 | — |
| [10](cases/case_10_composite_mixed_outcomes/input.json) | 复合行动混合成败 | 分项结果与叙事衔接 | 无 | — |
| [11](cases/case_11_tavern_entry/input.json) | 初入客栈，赵七进店 | 正式姓名与匿名出场称呼的对应 | 引入苏婉娘＋呈现赵七事件 | 是 |
| [12](cases/case_12_disclosure_intrusion/input.json) | 与掌柜谈心时事件介入 | 披露升级、人物互动与事件介入 | 苏婉娘披露升级＋赵七介入 | 是 |
| [13](cases/case_13_event_pressure/input.json) | 玩家旁观，赵七推进事件 | 被动观察中的人物使用记录 | 赵七介入进行中的事件 | 是 |
| [14](cases/case_14_spontaneous_constable/input.json) | 厢房交谈，差役敲门 | 即兴人物及声音、间接出场的对应 | 即兴引入周德海 | 是 |

01、02 已补入本次实验的导演输入；03–10 保持原输入。01 的陷阱全貌保存在后台 `assets.json`，Composer 只接收 Hook。四个游玩场景独立运行，不将新生成的前例正文串入后例。

11/12/13 统一了苏婉娘、悦来客栈的名称，13 去掉与赵七重复的在场者“中年汉子”。12、14 经用户同意改为非露骨交谈，保留完整第二人称上一幕，原动作顺序和结果档位作为固定测试参数；逐例整理内容见各自 `source.json`。历史原件在[存档调查的提取资料](local/sources/player-save-20261101)中保留。

## 实验与结果

| 实验 | 做过的测试 | 结论与入口 |
|---|---|---|
| [导演材料采用记录](experiments/director-usages/README.md) | 六例、两版本各3次，共18对 | 已解盲：A胜6对、B胜5对、7平；均分12.39 / 12.11 |
| [NPC 使用记录](experiments/npc-usages/README.md) | 已固定十例、两版本各 3 次 | 已解盲：A 胜 9 对、B 胜 10 对、11 平；重大视角问题另列 |
| [TR 与 Composer 合并](experiments/tr-composer-unification/README.md) | 10 例，每侧 3 次，30 对 | 候选胜 20 对；总 Token 均值下降 43.5% |
| [结果锚定 Composer](experiments/outcome-anchored/README.md) | 正式 10 例，每侧 3 次 | 未取得稳定叙事收益，候选未采用 |
| [早期开放叙事](experiments/open-affordance-legacy/README.md) | 旧用例 27 对，24 对有效 | A/B 各胜 12 对；旧用例集合与当前不同 |

## 工具检查与产品构建

Windows 发布构建运行产品测试，排除 `app/tests/test_narrative_benchmark.py`。实验工具检查单独手动运行，需要本机保留实验计划引用的 Git 历史：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q app/tests/test_narrative_benchmark.py
```

## 继续收录与测试

- 新用例：新增 `cases/<case_id>/input.json`，有来源或后台资源时放在同目录；在本页用例表加一行。
- 新实验：新增 `experiments/<实验>/README.md` 及实验计划或用例清单，列明目标、用例范围和轮次。
- 普通验证：连通性、字段准确性和输入排查等材料保存在 `local/<实验>/`，实验 README 简要记录准备进展。
- 正式测试：先确定版本、用例、重复次数、模型配置和评分标准，冻结双方输入与版本后执行。完整调用、详细评分报告和理由保存在 `local/<实验>/<轮次>/`；仓库在实验 README 或轮次目录保留精简小结、核心统计和结论，并登记本地证据位置。
- 完整报告默认输出到本轮 `local/<实验>/<轮次>/analysis/`；脚本拒绝将执行产物写到 `local/` 外。prepare 只在本轮目录冻结版本，不再自动导出仓库快照。
- Prompt／Schema 的源码变化通过 Git 历史查看，实验保留版本标识；完整冻结版本、正文、逐例输入、盲评包和用例副本集中在该轮 `local/`。
- 用例改动后重新开一轮。旧轮次保留当时冻结的输入和输出；输入错误或未完成的轮次明确标注。

完成评分复核与解盲后，更新实验 README 的核心统计、主要问题与结论，并在本页登记轮次和证据位置。普通验证不登记为正式结果。

仓库保留实验设计、版本标识、核心统计和精简结论。本地目录保留完整诊断证据，需另行备份。对应忽略规则只有 `/benchmarks/narrative_quality/local/`，没有反向例外。

普通验证和正式 A/B 的操作入口见 [NPC 使用记录实验](experiments/npc-usages/README.md)。默认 `--record` / `--check` 使用原构造十例清单 `cases/standard.json`，默认原始记录写入 `local/contract-check/current.json`。
