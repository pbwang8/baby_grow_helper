# PRD: Phase 2.5 — 家庭手机内测 MVP

> **状态：proposed（2026-06-24）**
> 起草者：Code 模式（根据作者 2026-06-24 方向确认）
> 关联文档：`ROADMAP.md` Phase 2/3 之间、`ARCHITECTURE.md` §1/§5/§8、
> `decisions/0004-database-evolution-local-sqlite-to-cloud-postgres.md`

---

## 1. Why（为什么做）

Phase 0-2 已经证明 BabyGrowHelper 的核心链路可以跑通：

```text
记录 -> 结构化事件 -> 信号 -> 压缩上下文 -> 周报 -> 反馈
```

但它目前仍主要运行在作者本机和合成 `xiaoming` fixture 上。下一步不是马上做
知识库 Coach，也不是直接做 Android 原生 App，而是让家庭成员在真实手机上
开始试用，验证两个更接近产品真实世界的问题：

1. 家庭成员是否愿意用手机记录孩子日常？
2. 家庭真实事件流是否能产出有价值的时间轴、信号和周报？

作者家庭成员主要使用华为/安卓机型。为了最快到达可用状态，本阶段采用
**手机 Web / PWA 优先**，不先做 APK，不上应用市场。

本阶段也是从“本地实验室”走向“家庭内测产品”的桥。它必须为未来上云、
收费和 10 万用户扩展留出正确数据模型，但不做商业化系统本身。

## 2. Product Goal（目标）

让家庭成员通过一个 HTTPS 地址，在华为/安卓手机浏览器里完成：

- 登录或输入家庭访问码
- 选择孩子
- 记录一条成长事件
- 查看时间轴
- 查看信号热度图
- 查看或触发周报

体验目标：

> 家人不需要懂技术，不需要打开终端，只要手机浏览器能用。

## 3. In Scope（本次必须做）

### 3.1 手机 Web / PWA

继续使用现有 `web/` Next.js，不重写 Android 原生。

必须：

- 手机 viewport 下 `/log` 可舒服输入
- `/timeline` 可滚动查看事件
- `/heatmap` 在手机上不崩布局，可横向滚动或简化展示
- `/weekly` 可查看周报和提交 section 反馈
- 增加 PWA 基础配置：manifest、图标占位、添加到桌面名称

### 3.2 最小家庭访问控制

本阶段不做完整商业账号系统，但不能裸奔公网。

必须支持一种最小访问机制：

- 方案 A：家庭访问码（推荐，最快）
- 方案 B：magic link（更正式，但需要邮件/SMS 服务）

默认推荐 A。

访问控制最小模型：

```text
users
families
family_members
children
```

事件、信号、周报、反馈必须能追溯到 `family_id` 和 `child_id`。

### 3.3 服务端数据库

家庭真实数据不再只写入作者某台电脑的 SQLite 文件。

必须：

- 引入 Postgres 支持
- 引入正式 migration 机制（优先 Alembic）
- 保留 SQLite 开发模式或测试模式
- 不把真实数据写进 Git

最低数据隔离要求：

- 每个 API 请求必须限定在当前 family 范围内
- 禁止仅凭任意 `child_id` 读取其他家庭数据

### 3.4 公网部署

必须提供一种可复现的部署方式。

推荐最小形态：

```text
Caddy/Nginx HTTPS
Next.js web
FastAPI api
Postgres
Ollama-compatible local/remote model endpoint
```

可用 Docker Compose，但不强制 Kubernetes。

必须有：

- `.env.example`
- production API base URL 配置
- CORS 配置
- health check
- 启停说明

### 3.5 真实数据保护底线

家庭内测会产生真实儿童数据，本阶段必须补最小保护：

- 导出：按 child/family 导出 JSON + markdown
- 删除：按 child/family 删除数据
- 日志：生产日志不得打印完整 `raw_text`
- 云端调用：仍必须经过 `context_compressor`
- 测试：继续使用 `xiaoming`，禁止把瑶瑶或家人真实数据写入 tests/fixtures

### 3.6 用量与成本记录

未来收费需要从现在开始留口子。

必须：

- `usage_log` 能归属到 user/family
- 云端调用记录 model、tokens、latency、purpose
- 保留每用户/月 token cap 的配置位置

本阶段不要求做支付，但不能让未来支付系统无处挂载。

## 4. Out of Scope（本次明确不做）

- Android 原生 App
- 华为应用市场上架
- iOS App
- 公开注册
- 手机号登录 / 微信登录
- 支付 / 订阅 / 发票
- 多家庭运营后台
- 照片/语音上传
- 家庭成员复杂权限（例如只读/编辑/管理员多级 RBAC）
- Phase 3 知识库 RAG / 培养建议 Agent
- A/B Sonnet vs Haiku
- 10 万用户完整基础设施

## 5. Acceptance（验收标准）

### 5.1 手机端可用性

- 华为/安卓手机浏览器可打开内测地址
- 可以添加到桌面（PWA 基础能力）
- `/log` 能提交事件
- `/timeline` 能看到刚提交的事件
- `/weekly` 能查看已有周报或触发生成
- 页面主要文本和按钮在 360px 宽度下不溢出

### 5.2 数据隔离

- 未授权请求不能读取事件
- family A 不能读取 family B 的 `events/signals/weekly_insights`
- 所有真实数据写入 Postgres，不写入 Git

### 5.3 数据保护

- 可以导出一个 child 的 JSON + markdown
- 可以删除一个 child 的事件、信号、周报和反馈
- 生产日志不包含完整 `raw_text`

### 5.4 工程质量

- `make test` 通过
- 新增 API 有测试
- 新增 DB migration 有测试或 smoke
- `ruff` / `mypy` 通过
- `web` typecheck + lint 通过
- `reports/phase2_5-family-mobile-baseline.md` 记录部署、测试和手机端验证结果

## 6. Constraints（约束）

- 不修改 `VISION.md` / `DIFFERENTIATION.md`
- 不把真实儿童数据提交到 Git
- 不在运行时让日常记录 Agent 常驻调用云端大模型
- 不绕过 `LLMClient`
- 不让云端洞察直接吃原始长期事件流，必须先压缩
- 不引入 LangChain / LlamaIndex
- 不使用 `sudo npm install -g`
- 不为了家庭内测引入不可控 SaaS 锁定

## 7. Future Commercial Compatibility

本阶段不做收费，但必须为未来收费留结构。

需要预留：

- `user_id`
- `family_id`
- `family_members.role`
- `usage_log.user_id/family_id`
- 每用户/月 token cap
- subscription/billing 表的未来挂载点

未来商业化路径：

```text
family -> plan/subscription -> usage cap -> billing records
```

Phase 2.5 只实现 `family -> usage cap` 的基础，不实现支付。

## 8. Suggested Milestones

### M2.5.1 ADR + PRD 收口

- `decisions/0004` accepted
- 本 PRD accepted
- 选择部署路线：本机公网隧道、云服务器、还是托管平台

### M2.5.2 数据层

- Postgres 连接配置
- migration 机制
- `users/families/family_members` schema
- 现有核心表增加 family/user 归属
- SQLite 测试模式保留

### M2.5.3 Auth + API 隔离

- 家庭访问码登录
- 请求上下文解析出 `family_id`
- events/signals/insights 按 family 隔离
- export/delete API

### M2.5.4 手机 Web/PWA

- `/log` 手机输入优化
- `/timeline` 手机信息密度优化
- `/weekly` 手机反馈控件优化
- PWA manifest

### M2.5.5 部署

- Docker Compose 或等价部署脚本
- `.env.example`
- HTTPS
- smoke test
- 家庭手机真机验证

## 9. Open Questions

1. Phase 2.5 的部署目标选哪一个？
   - A. 作者自有 Mac/小主机 + 公网隧道
   - B. 云服务器 + Docker Compose
   - C. Vercel/Render/Railway + 托管 Postgres

2. 家庭访问控制首版选哪一个？
   - A. 家庭访问码
   - B. magic link
   - C. 微信/手机号（不推荐本阶段）

3. 家庭内测是否只支持一个孩子，还是 schema 支持多孩子但 UI 先单孩子？

4. Postgres 是否允许使用托管服务？如果使用，优先国内云还是海外云？

5. 导出/删除在 Phase 2.5 是否要求 UI 按钮，还是先提供管理员 API？

---

_本 PRD 是 Phase 2 与 Phase 3 之间的产品化桥梁：先让真实家庭能用，再继续做培养建议。_
