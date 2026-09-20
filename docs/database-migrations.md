# 数据库迁移说明

本项目使用 Alembic 管理 MySQL Schema。当前迁移历史是单一线性链，唯一 head 为 `e5a1c7d9b302`。

本文描述迁移代码的真实行为，不包含任何开发数据库凭据、业务数据、备份位置或环境专用连接信息。

## 迁移原则

- ORM 模型变化必须同步创建和人工检查 Alembic revision；`Base.metadata.create_all()` 不能替代已有数据库迁移。
- MySQL DDL 不是事务性的。高风险演进拆为 expand、backfill、contract 和 cleanup 小步，每一步先验证再执行破坏性动作。
- 数据回填必须验证数量、映射、外键与旧数据不变量，不能只依赖迁移命令退出码。
- 不修改已经部署的历史 revision；后续修正使用新 revision。
- 对不可逆清理，必须在升级前制作并恢复验证完整备份。不能用 `stamp` 冒充真实 Schema 状态。
- 迁移测试只允许使用通过安全门禁的专用可销毁 MySQL 数据库。

## 完整 revision 链

```text
b8e33b316aab
  -> 8c5960f0109e
  -> 3a1d4e6f8b20
  -> 6c2f9a4d7e31
  -> 9f4c2a7b1d30
  -> c1e8d5a4b762
  -> d4b6e8f1a203
  -> e5a1c7d9b302 (head)
```

| Revision | 作用 | 可逆性说明 |
|---|---|---|
| `b8e33b316aab` | 创建 `users` 与旧版 `tasks`，Task 使用 `owner_id`、`completed` | 可按依赖反序删除初始表 |
| `8c5960f0109e` | 为旧 Task 增加可空 `description` | 可删除该列 |
| `3a1d4e6f8b20` | 创建 `workspaces`、名称 CHECK、creator 外键和索引 | 在无依赖对象时可删除表 |
| `6c2f9a4d7e31` | 创建 `workspace_members`、复合主键、角色 CHECK、双外键和反向索引 | 可在 Workspace 前删除成员表 |
| `9f4c2a7b1d30` | Expand：为 Task 增加可空协作字段、外键、状态 CHECK、索引，并创建临时映射表 | 数据迁移反转后可删除兼容结构 |
| `c1e8d5a4b762` | Backfill：为每个旧 User 创建个人 Workspace/OWNER，记录映射并回填旧 Task | 只在自动生成的数据完全未变时安全反转 |
| `d4b6e8f1a203` | Contract：验证回填，收紧非空与 CHECK，删除 `owner_id`、`completed` | 可恢复兼容字段，但 `IN_PROGRESS` 降级为 `completed=false`，存在语义损失 |
| `e5a1c7d9b302` | Cleanup：硬校验最终结构和数据后删除临时映射表 | 明确不可逆；downgrade 在任何写操作前拒绝 |

## 从单用户 Task 到协作 Task

### 1. Expand：`9f4c2a7b1d30`

这一阶段不改变旧 Task 语义，先增加可空的新字段：

- `workspace_id`
- `creator_id`
- `assignee_id`
- `status`
- `created_at`
- `updated_at`

同时创建三个 `ON DELETE RESTRICT` 外键、状态 CHECK 和五个查询索引。新字段暂时可空，使旧数据在回填完成前仍满足 Schema。

Revision 还创建迁移专用表 `task_collaboration_user_workspace_map`，按 user_id 唯一记录自动生成的 Workspace、旧 Task 数量、最大 Task ID 和迁移时间。它不是业务 ORM 模型。

### 2. Backfill：`c1e8d5a4b762`

对每个已有 User：

1. 记录该用户旧 Task 的数量与最大 ID。
2. 创建一个个人 Workspace。
3. 创建该用户的一条 OWNER membership。
4. 写入迁移映射表。
5. 将该用户的旧 Task 回填到个人 Workspace：
   - `creator_id = owner_id`
   - `completed=false → TODO`
   - `completed=true → DONE`
   - `assignee_id = NULL`
   - `created_at`、`updated_at` 使用迁移时间

回填后验证：每个 User 恰好有映射，Workspace creator 与 OWNER 正确，Task 数量/最大 ID 与映射一致，所有旧 Task 使用创建者的映射 Workspace，且迁移前已经存在的 Workspace 与 Membership 未变化。

该 revision 的 downgrade 很保守：只有自动生成的 Workspace、OWNER 和回填 Task 完全保持迁移时状态才允许删除；发现后续业务改动即拒绝继续，避免误删真实协作数据。

### 3. Contract：`d4b6e8f1a203`

在执行 DDL 前验证：

- User 与映射数量一致，每个 User 都有映射；
- 每个映射 Workspace 有对应 OWNER；
- Task 新必填字段均已回填；
- status 只包含 `TODO`、`IN_PROGRESS`、`DONE`；
- creator 等于旧 owner；旧 completed 与新 status 映射正确；
- title、priority 满足最终约束；
- Workspace/creator 引用无孤立；
- 所有 Task 都使用 owner 对应的映射 Workspace；
- `owner_id` 外键可以被唯一识别。

验证通过后才会：

- 将 `workspace_id`、`creator_id`、`status`、`created_at`、`updated_at` 收紧为 NOT NULL；
- 为 status 与时间戳设置最终 server default；
- 增加 title 与 priority CHECK；
- 删除旧 owner 外键、`owner_id` 和 `completed`。

## 映射表退役：`e5a1c7d9b302`

应用已经只使用最终 Task Schema 后，映射表仍保留了一段时间，用于验证历史回填以及在安全条件下回退早期协作迁移。最终清理 revision 在删除表前完成全部硬校验。

### 结构校验

- 映射表存在，列、可空性、DATETIME(6)、主键、唯一约束和两个 RESTRICT 外键符合预期。
- 当前数据库 revision 是直接前驱 `d4b6e8f1a203`。
- User、Workspace、WorkspaceMember、Task 与 `alembic_version` 均存在。
- Task 已不存在 `owner_id`、`completed`，且最终列集合完全匹配。
- status 是受 CHECK 限制的 VARCHAR，不是 MySQL ENUM。
- Task 的 3 个 CHECK、5 个索引和 3 个 RESTRICT 外键完全匹配最终模型。

### 数据校验

- 映射 user_id、workspace_id 均无重复。
- 所有映射 User 与 Workspace 均存在。
- 映射 Workspace 的 `created_by_id` 与 user_id 一致。
- 每个映射 Workspace 仍存在对应 OWNER membership。
- Task 的最终必填字段均非空，status 合法。
- Task 的 Workspace、creator、非空 assignee 均无孤立引用。

全部校验完成后，upgrade 只有一个破坏性动作：删除 `task_collaboration_user_workspace_map`。它不修改业务表或业务行。

### 为什么不可逆

映射表包含“哪些 Workspace 是迁移自动生成的”以及迁移时旧 Task 的数量/身份摘要。表删除后，当前业务数据可能已经发生以下变化：

- 用户创建新的 Workspace；
- 自动生成的个人 Workspace 被重命名或加入新成员；
- Task 被新增、删除、移动、重新指派或修改状态。

因此不能从当前数据可靠推断原始历史映射。创建空映射表或按名称猜测 Workspace 都会制造虚假的迁移历史。

`e5a1c7d9b302.downgrade()` 会在任何 DDL/DML 前抛出 RuntimeError。跨越这一边界的唯一安全方式是恢复在清理前制作并验证过的完整数据库备份；不允许使用 `alembic stamp` 绕过事实状态。

## 最终 Task Schema

当前 Task 字段：

| 字段 | 约束 |
|---|---|
| `id` | INT，自增主键 |
| `title` | VARCHAR(100)，非空，trim 后长度 1～100 |
| `description` | VARCHAR(500)，可空 |
| `priority` | INT，非空，1～5 |
| `workspace_id` | 非空，FK → workspaces.id，RESTRICT |
| `creator_id` | 非空，FK → users.id，RESTRICT |
| `assignee_id` | 可空，FK → users.id，RESTRICT |
| `status` | VARCHAR(16)，非空，TODO/IN_PROGRESS/DONE |
| `created_at` | DATETIME(6)，非空，UTC server default |
| `updated_at` | DATETIME(6)，非空，UTC server default，应用更新时刷新 |

索引：

- `(workspace_id, id)`
- `(workspace_id, status, id)`
- `(workspace_id, assignee_id, id)`
- `(creator_id)`
- `(assignee_id)`

## 安全部署流程

下面是操作顺序，而不是可以跳过审查直接复制到任意环境的脚本：

1. 核对 Git revision、Alembic 当前 revision 和唯一 head。
2. 只读检查数据库结构、孤立引用、非法状态与关键数据摘要。
3. 在旧 API 仍服务时构建包含新 revision 的镜像。
4. 停止 API 写入，保持 MySQL 运行。
5. 使用匹配版本的 MySQL 工具制作包含 Schema、数据、Alembic、trigger、routine 和 event 的完整逻辑备份。
6. 在全新、隔离、tmpfs、非 root 的临时 MySQL 中恢复备份，并比较 revision、表结构和行数。
7. 使用新镜像显式运行一次 `alembic upgrade head`；不要把首次迁移隐藏在 API 启动里。
8. 对比迁移前后的业务表行数、主键摘要、全行摘要、约束、索引和外键。
9. 运行 `alembic check`；只有无差异时才启动新 API。
10. 检查 health、OpenAPI、日志、最终 revision、容器和持久卷。

常用只读命令：

```bash
alembic current
alembic heads
alembic history
alembic check
```

`alembic upgrade head` 会修改目标数据库，执行前必须确认环境和备份。不要在未确认连接目标时运行 upgrade，也不要对不可逆边界执行 downgrade 或 stamp。

## 测试策略

迁移测试使用与开发数据库不同的专用 `*_test_db`，并要求 `TEST_DATABASE_RESET_ALLOWED=true`。账号必须是非 root，权限只覆盖该测试库。

当前测试覆盖：

- 从历史 revision 升级到最终 Task Schema；
- 协作迁移可逆部分的往返与重复执行；
- 旧 Task 到个人 Workspace 的数据映射；
- 最终列、CHECK、索引和外键；
- `e5a1c7d9b302` 删除映射表且保持业务数据；
- cleanup 前置校验失败时不执行 DROP；
- downgrade 拒绝后 revision、Schema、行数和连接可用性不变；
- fresh database 从 base 升级到当前 head；
- `alembic check` 没有待生成操作。

这些测试会重建专用测试 Schema，因此被安排在默认 pytest 的最后执行。测试通过不能替代真实环境的备份恢复演练，但能验证迁移脚本和 ORM metadata 的一致性。

## 新增迁移时的检查清单

- 模型、Schema 和 revision 是否表达同一约束？
- 外键名称、ON DELETE、CHECK 和索引是否显式且可检查？
- MySQL DDL 失败时能否知道已执行到哪一步？
- 所有数据验证是否在第一个破坏性 DDL 前完成？
- 数据回填是否有数量、孤立引用和映射不变量？
- downgrade 是真实可恢复，还是应该明确拒绝？
- 是否在专用 MySQL 上验证 fresh upgrade、历史 upgrade、downgrade 和 `alembic check`？
- 部署前备份是否实际恢复验证，而不只是确认文件存在？
