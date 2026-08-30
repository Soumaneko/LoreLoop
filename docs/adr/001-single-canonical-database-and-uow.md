# 1. 收敛物理数据库边界与引入工作单元 (CampaignUnitOfWork)

**Date**: 2026-08-19  
**Status**: Accepted

## Context (上下文与背景)

在目前的架构中，单机存盘（Campaign）目录被按照“领域（Domain）隔离”拆分成了 10 个业务 SQLite 数据库（如 `current_state.db`, `continuity.db`, `npc_profiles.db` 等）和 1 个运行时控制数据库（`turn_history.db`）。这种“微服务化数据库”的反模式导致了多项严重的工程与一致性痛点：

1. **跨库事务的伪 ACID 问题**：确认（Confirmation）阶段需要原子化地提交剧情进度。由于 SQLite 无法提供跨文件的强一致性（2PC），我们被迫采用了“前置快照 + 失败后跨多文件补偿回滚”的设计（例如使用 `director_mutation.json` 进行善后）。这极大地增加了 I/O 开销与逻辑脆弱性。
2. **在线恢复 (Undo/Redo) 导致锁死**：为了使旧长连接或缓存失效，目前的 Undo/Redo 在还原快照时采用了 SQLite 原生的 `backup()` 向在线活跃数据库执行就地覆写，并配合 `wal_checkpoint(TRUNCATE)`。但在存在并发 Reader 或残留句柄时（尤其在 Windows 系统），这会引发 `[WinError 32]` 或 `SQLITE_BUSY` 彻底崩溃。
3. **规范状态 (Canonical State) 与偏好混淆**：快照还原白名单绑定了 `settings.json`，导致玩家回滚一回合剧情时，连带把界面偏好等应用层设置也错误地一起撤销了。

## Decision (架构决策)

针对上述痛点，我们决定从存储物理模型、连接生命周期以及状态边界三个维度重构：

1. **统一物理持久层（Consolidate Databases）**：
   - 废除单领域单库策略。建立单一的 **`campaign.db`**，囊括所有的规范状态（Canonical State，如状态、历史、NPC、世界设定等），使其可以利用原生 SQLite 事务实现强一致性的 `COMMIT` 与 `ROLLBACK`。
   - 保留 **`control.db`** (前身为 `turn_history.db`) 专门负责不随剧情 Undo 的控制面与元数据。
2. **引入工作单元（CampaignUnitOfWork）**：
   - 将原来各个 Store 中散落的 `sqlite3.connect()` 收口。模块边界仍通过 Store 区分（如 `NPCProfileStore`），但通过 `CampaignUnitOfWork` 共享同一个事务连接，生命周期严格由 UoW 控制。
3. **改造 Undo/Redo 原语（Offline Replacement）**：
   - 彻底废弃对活跃数据库原地执行 `backup` 还原的机制。
   - 新流程变更为：**Quiesce（停止所有活动并关闭所有连接） -> 校验新库文件 -> Atomic Replace（原子替换文件） -> 递增 `connection_epoch` -> 重新打开连接**。这不仅绕过了文件系统占用的痛点，且从根本上隔离了脏连接。
4. **状态隔离分层**：
   - 明确将应用偏好（Application Preferences，如 `settings.json`）移出剧情快照回滚周期。

## Consequences (后果与权衡)

### 积极影响 (Pros)
- **强一致性事务**：消除所有手工跨库补偿代码，将 Confirmation 回归最纯粹的 SQLite 单库事务。
- **消灭系统级 I/O 死锁**：Windows 环境下不会再发生文件占用相关的 Undo 失败。
- **防幽灵读取**：通过 `connection_epoch`，生命周期外的残留连接只要尝试读写就会被拦截，极大增强了系统安全性。

### 消极影响/成本 (Cons)
- **重构成本较高**：涉及整个 `app/runtime` 生命周期模块以及所有的测试用例（尤其是大量模拟多库并行的测试）的底层更替。
- **并行写入受限**：由于所有的 Domain 都合并进了同一个 `campaign.db`，原先在不同文件中可以完全并行的写入操作，现在将在同一个 WAL 机制下串行写入，但基于单机架构的性能压力微乎其微。
