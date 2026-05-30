# 0002: signals 表的落地与迁移策略

Date: 2026-05-23
Status: accepted

## Context

PRD `prd/phase1-signals.md` §2.1 #1 要求落地 `signals` 表，schema 比
ARCHITECTURE.md §5 当时给的概念性占位多 4 个字段（`intensity`、
`child_age_months`、`delta_from_last_period`、`notes`）。

Phase 0 时 `src/core/db.py` 的 `SCHEMA_SQL` 没建 `signals`，理由是 Phase 0
不写信号。现在 Phase 1 要建，需要决定迁移策略。

约束：
- MVP 还没真实用户数据（瑶瑶的真实数据从未入库——按 CLAUDE.md §5 的隐私红线）
- 项目唯一用户是作者本人，本地单机 SQLite
- 测试每次都用 tmpdir 全新 DB，不跨进程持久（见 `tests/conftest.py`）

## Decision

**继续用 `CREATE TABLE IF NOT EXISTS` 风格的 schema 全量定义，把 `signals`
直接 append 到 `SCHEMA_SQL`。不引入 Alembic、不写 migration step 文件。**

迁移路径：
- 作者本机：作者一次性手动 `rm data/babygrow.db && make db-init`（pre-MVP，无真用户数据，零成本）
- 测试：tmp DB 每次新建，无需迁移
- 未来真用户出现后（v0.2+）：再起 ADR，引入 Alembic / 手写 migration runner

## Alternatives

1. **Alembic**：标准 Python 数据库迁移框架。
   - 拒绝理由：单用户、单机、无生产环境，引入 Alembic 是把企业级复杂度
     提前压上来。等到真正有"不能丢的库"时再说，符合 ARCHITECTURE 的
     "复杂度按需引入" 原则。

2. **手写 v1→v2 migration step**（在 db.py 写 `_migrate_v1_to_v2()`）：
   - 拒绝理由：MVP 阶段没有真实库需要保护。一旦写了，就要维护"曾经
     存在过的旧 schema"这条历史记忆，给未来制造路径依赖。

3. **把 `signals` 拆到独立的 schema 文件**（如 `src/core/db_phase1.sql`）：
   - 拒绝理由：MVP 阶段，所有 schema 都在同一个 SCHEMA_SQL 字符串里
     是最易读的状态。拆分等到 schema 超过 ~10 张表再做。

## Consequences

- ✅ 实现成本：在 SCHEMA_SQL 末尾追加 ~30 行 SQL + 一条索引即可
- ✅ 测试无影响：tmp DB 每次走完整 schema
- ⚠️ 作者迁移成本：需要手动 `rm data/babygrow.db`。已在 phase1
  的 README/Makefile 写明
- ⚠️ 未来代价：第一次有真实用户数据后，必须立刻起 ADR 决定迁移工具
  （Alembic 或手写 runner）。本 ADR 显式承认这个未偿债务

## 触发"必须升级到正式 migration 工具"的条件

满足以下任一项，下次 schema 变更前必须先起新 ADR：

1. 作者本机的 DB 已经积累 ≥ 7 天连续真实数据（"不能丢")
2. 项目接入第二位用户
3. v0.2 立项

在那之前，append-to-SCHEMA_SQL + drop-and-recreate 是合理的最小成本路径。
