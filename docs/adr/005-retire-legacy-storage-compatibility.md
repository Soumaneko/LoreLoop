# 5. 0.1.0 前停止旧物理布局兼容

**Date**: 2026-08-30  
**Status**: Partially superseded (2026-10-22)

## 当前适用范围（2026-10-22）

当前产品规范见根目录 `INVARIANTS.md`，实现见 `CURRENT.md`。
按基础设施精简 ticket 01，运行入口直接读写当前格式：启动校验当前数据库，
未参与读取的旁置文件留在原处；复制与导出枚举支持的文件；不可读的当前文件
保留原件并反馈原因。领域 Store 直接初始化和校验当前 schema。
当前快照、归档路径校验和确认／撤销控制协议继续按现有实现运行。

下文保留为 2026-08-30 的决策记录；其中旧布局专项拒绝和领域 schema
递增迁移的决定已由本次产品规范修订及 ticket 01 取代。

## Context

`0.1.0` 正式发布前，存档物理布局已从十个领域数据库（`current_state.db` 等 10）+ `.runtime/turn_history.db` + 含 `settings.json` 的旧快照，收敛为 `campaign.db` + `.runtime/control.db` + 仅含 `campaign.db/core_memory.json/initialization.json` 的快照（ADR-001）。Phase 1–3 的迁移代码（`ATTACH/copy`、FTS 重建、`turn_history.db` 晋升、`settings.json` 重写、`saves/.app` 搬迁、离线 `replay_npc_field_history`）已在单库上稳定运行，但继续保留旧布局的自动迁移会掩盖 `unsupported legacy layout` 的显式失败语义，且使 `save_archive` 白名单无法收敛为可审计的当前集合。

工作区仍存在 1 个被 `.gitignore` 排除的真实旧存档 `saves/207599e8-31e3-446b-9e13-cd219fec07d8`（10 库 + `turn_history.db` + 11 旧快照 + `.npc-field-replay*`），需在 Phase 4 前以 `9eef791` 在副本上完成迁移验证。副本 ` /tmp/phase4-checkpoint-207599e8` 已以 `9eef791` 完成 `migrated`，当前 `campaign.db`（`turn_transactions` 122 / `turn_snapshots` 10）与 `control.db` 已生成，`.npc-field-replay*` 逐文件一致；但 `.snapshots` 仍保留 11 个旧快照目录（含 109 个旧十库文件 + 11 个 `settings.json`），按 Phase 4 语义视为 `unsupported legacy layout`，不再被当前快照/归档路径消费。

## Decision

自本版本起，**停止导入或自动迁移**：

1. 根目录十个领域数据库、`director_mutation.json` 跨库日志、`\.runtime/legacy-db-v1`；
2. `.runtime/turn_history.db`（含 WAL/SHM sidecar 的 checkpoint/rename 晋升）；
3. 旧 Undo/Redo 快照及快照内 `settings.json`、含上述文件的旧 ZIP；
4. `saves/.app`/`logs/*.jsonl` 的源码目录自动搬迁；
5. `scripts/replay_npc_field_history.py` 离线 replay。

当前格式仅 `campaign.db`/`control.db` + 上述三件套快照。检测到旧布局时，`ensure_campaign_database`/`ensure_control_database` 在 `RuntimeError("unsupported legacy layout …")` 上 fail-closed，不创建空库；`save_archive`/`saves` 在 `SaveArchiveError` 上 fail-closed，不安装目标或覆盖源。`CURRENT.md` 仅描述当前格式；`INVARIANTS.md` 不改（未承诺任意历史格式）。真实旧存档的副本已在 `/tmp/phase4-checkpoint-207599e8` 以 `9eef791` 完成 `migrated/validated`、`turn_transactions` 122 / `turn_snapshots` 10、Undo/Redo 与 export→import 均通过，`.npc-field-replay*` 完整保留。

## Consequences

### 积极

- `campaign.db`/`control.db` 的启动、快照、白名单、ZIP/path/hash/schema 校验保持最小可审计面；旧分支的 `ATTACH`、`wal_checkpoint`、`settings` 重写等不再进入生产路径。
- 旧布局的失败语义统一为显式 `unsupported`，不产生空库或半成品安装。

### 能力损失

- 旧十库存档、旧 `turn_history.db`、`settings.json` 快照、含旧文件的 ZIP、源码目录自动搬迁、离线 NPC replay 将不能再由新版直接使用；需先以 `9eef791`（或更早）迁移，或重建新存档。
- `scripts/replay_npc_field_history.py` 及其 `\.npc-field-replay` 产物不再维护。

### 保留

- `campaign.db` 内的领域 schema 递增迁移（Continuity/NPC/WorldSetting 等）、当前快照/Undo/Redo/crash recovery/epoch、ZIP 安全边界、`control.db` 的 `full-chain backfill` 均保留。
