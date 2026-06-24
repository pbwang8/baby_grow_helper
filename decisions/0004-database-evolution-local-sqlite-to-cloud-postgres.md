# 0004: 数据库从本地 SQLite 演进到云端 Postgres

Date: 2026-06-24
Status: accepted

## Context

BabyGrowHelper 目前已经完成 Phase 0-2 的本地 MVP：

- 本地 SQLite 保存 `children/events/signals/weekly_insights/insight_feedback`
- `sqlite-vec` 保存事件向量
- 本地 Web + FastAPI 可以跑通 `/log`、`/timeline`、`/heatmap`、`/weekly`
- 所有 demo/test 数据使用合成孩子 `xiaoming`，不使用真实瑶瑶数据

这个选择在 Phase 0-2 是正确的：它最大化本地隐私、降低运维成本、让产品心脏
先跑起来。但作者现在明确提出两个新目标：

1. 家庭成员要在华为/安卓手机上试用。
2. 未来不排除上云、收费，并希望长期能应对超过 10 万用户。

这意味着数据库策略必须从“单机文件”升级为“双层架构”：

- 本地 SQLite 仍然存在，负责开发、离线、本地缓存、私有部署。
- 家庭内测和未来商业化需要服务端主库，不能让真实数据困在某一台电脑文件里。

## Decision

采用 **Local SQLite + Cloud Postgres** 的演进策略。

### 1. SQLite 的定位

SQLite 继续用于：

- 本地开发
- 单机 demo
- 小明 fixture 验证
- 本地离线缓存
- 私有部署 / 个人数据导出

SQLite 不再作为家庭内测真实数据的唯一存储。

### 2. Postgres 的定位

从 Phase 2.5 家庭手机内测开始，服务端主库首选 **PostgreSQL**。

Postgres 负责：

- 真实用户 / 家庭 / 孩子关系
- 家庭成员权限
- 真实事件、信号、周报、反馈
- 用户级模型用量统计
- 未来订阅与计费状态
- 导出、删除、审计所需的可追溯记录

向量检索早期使用 `pgvector`，等规模或检索形态证明 `pgvector` 不够时，
再拆到专用向量服务。不要在 Phase 2.5 过早引入独立向量数据库。

### 3. 数据归属模型

未来服务端 schema 必须围绕以下归属链设计：

```text
user -> family -> child -> events/signals/weekly_insights/feedback
```

不能继续让真实业务只依赖：

```text
child_id -> events
```

原因：未来收费、家庭协作、删除导出、权限隔离、用量统计，都必须落在
`user/family` 层。

### 4. 迁移策略

Phase 0-2 的 SQLite `append-to-SCHEMA_SQL` 策略到此为止。

Phase 2.5 若引入 Postgres，必须同时引入正式迁移机制：

- 优先：Alembic
- 备选：项目内自研轻量 migration runner

SQLite 的开发 schema 可以继续保留，但不能成为云端 schema 的唯一来源。

### 5. 10 万用户时的方向

10 万用户不是 Phase 2.5 的实现目标，但当前设计不能堵住它。

预期演进：

```text
Phase 2.5: 1 个家庭内测
  Postgres 单实例 + 最小 auth + PWA

v0.2: 10-100 个早期家庭
  Postgres + Alembic + 备份 + 每用户用量 cap

Beta: 1k-10k 用户
  Postgres 分区 + Redis + 队列 + workers + 对象存储

10 万用户:
  API gateway + Postgres 分区/读副本 + Redis + 队列集群
  + pgvector/向量服务 + 对象存储 + 数据仓库/审计
```

## Alternatives

### A. 继续只用 SQLite 承载家庭内测

拒绝。

SQLite 很适合本地和单用户，但家庭成员手机试用意味着公网访问、权限控制、
备份恢复和多设备同步。继续只用一个本机 `.db` 文件，会把真实数据、部署和
未来商业化都绑在作者电脑上。

### B. 现在直接上“10 万用户级”复杂架构

拒绝。

当前还没有家庭内测反馈，也没有真实留存数据。过早引入 Kubernetes、分布式
向量库、复杂消息系统，会拖慢产品验证。Phase 2.5 的目标是家庭可用，不是
企业级平台。

### C. 使用托管 BaaS（如 Supabase/Firebase）作为主后端

暂不采用为默认路线。

Supabase 可以作为 Postgres 托管形态的候选，但业务代码仍应围绕标准
Postgres + FastAPI 设计，避免过早绑定某个 BaaS 的权限和函数模型。

### D. 直接用 MongoDB / 文档库

拒绝。

BabyGrowHelper 的核心数据强关系明显：用户、家庭、孩子、事件、信号、周报、
反馈、用量、订阅。Postgres 更适合做一致性、审计、迁移和未来计费。

## Consequences

接受：

- Phase 2.5 会比单机 demo 多出 auth、Postgres、migration、部署配置的复杂度。
- 本地 SQLite 与云端 Postgres 之间需要抽象边界，不能让 SQL 到处散落。
- 家庭真实数据进入系统前，必须补导出、删除、权限、备份策略。

换来：

- 家庭成员可以跨设备试用。
- 真实数据不困在作者电脑。
- 未来收费模型有承载点。
- 未来 10 万用户扩展路线清晰，不需要推翻当前产品代码。

## Implementation Notes

Phase 2.5 只需要实现第一段：

- Postgres 开发/部署支持
- 最小 `users/families/family_members` 数据模型
- 事件/信号/周报按 `family_id/child_id` 隔离
- 用户级 `usage_log`
- 数据导出/删除的最小 API

不要在 Phase 2.5 实现：

- 支付
- 多租户运营后台
- 复杂 RBAC
- 多区域部署
- 独立向量数据库

## Follow-ups

- 在 `prd/phase2_5-family-mobile-mvp.md` 明确家庭内测范围。
- 在进入代码前，确认 Postgres 部署方式：本机 Docker Compose、云服务器、
  还是托管 Postgres。
- 在首次真实家庭数据入库前，补最小隐私说明和数据删除流程。
