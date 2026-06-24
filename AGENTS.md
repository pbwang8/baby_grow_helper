# AGENTS.md — 给 Codex 的项目说明书

> 这份文件是你（Codex）每次进入本项目时都会自动读到的"约束清单"。
> 在写任何代码之前，请先读它，然后读 `README.md → ROADMAP.md → 当前 PRD`。

---

## 1. 项目身份

- **名称**：BabyGrowHelper
- **本质**：长期陪伴型育儿 AI（不是相册不是日记本——见 `VISION.md` §1）
- **首位用户**：项目作者本人（瑶瑶爸爸），孩子 2.5 岁
- **核心护城河**：跨年记忆 + 主动洞察 + 个性化建议（见 `DIFFERENTIATION.md`）

---

## 2. 你（Code）的角色边界

### ✅ 你应该做的
- 实现 `prd/` 中已确认的需求卡
- 在 `src/` 写代码、在 `tests/` 写测试、跑 lint 与测试
- 维护 `ROADMAP.md` 中的状态列
- 遇到工程层面的取舍写 `decisions/NNNN-*.md`（ADR）
- 发现 PRD 模糊或矛盾时，**停下来在 PRD 末尾追加 Open Questions，回到 Cowork 解决**

### ❌ 你不应该做的
- **不修改 `VISION.md`、`DIFFERENTIATION.md`** —— 这些是产品宪法，要改先回 Cowork 讨论
- **不擅自加新功能**（哪怕你觉得很合理）—— 新功能必须先有 PRD
- **不悄悄改 `ARCHITECTURE.md` §1-2** —— 设计原则改动要单独走 ADR
- **不直接改任何 `# i18n_locked` 标记的文案常量**
- **不让运行时 Agent 调用云端"长驻"** —— 任何让单用户云端 token 月用量超过 100k 的改动都要先讨论

---

## 3. 必读文档（按顺序）

每次进入项目第一件事，按这个顺序读：

1. `README.md` — 项目入口
2. `ROADMAP.md` — 当前在哪个 Phase
3. `prd/<目标>.md` — 当前任务的具体需求
4. `ARCHITECTURE.md` — 技术约束（写代码前查）
5. `decisions/` — 历史决策（遇到"为什么这样设计"时查）

如果你想改的方向和上述文档冲突，**停下来回到 Cowork**。

---

## 4. 代码风格 / 工程约定

### 4.1 语言与栈
- MVP：Python 3.11+ / FastAPI / SQLite + sqlite-vec / Ollama 本地推理 / Anthropic API 兜底
- 前端 (MVP)：Next.js + React，先 Web 后移动端
- 包管理：`uv` (Python) / `pnpm` (前端)

### 4.2 项目结构（建议）
```
baby_grow_helper/
├── README.md
├── AGENTS.md            ← 你正在读
├── VISION.md
├── DIFFERENTIATION.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── COLLABORATION.md
├── FEEDBACK_LOOP.md
├── prd/                 ← 需求卡
│   ├── inbox/           ← 反馈触发的草稿
│   └── *.md             ← 已确认的 PRD
├── decisions/           ← ADR
│   └── NNNN-title.md
├── src/
│   ├── agents/          ← 各 Agent 实现
│   ├── core/            ← 数据模型、存储
│   ├── api/             ← FastAPI 路由
│   └── prompts/         ← 提示词模板
├── tests/
├── reports/             ← 实验/性能数据
└── changes/auto/        ← 反馈自动改动暂存
```

### 4.3 编码规范
- Python：black + ruff + mypy（严格模式）
- 任何 Agent 调用都要写**单元测试 + 一组真实样例的快照测试**
- 提示词放在 `src/prompts/`，**不要硬编码在代码里**
- 所有外部模型调用都要走 `src/core/llm_client.py` 的统一封装（方便切模型、加缓存、记录 token 用量）

### 4.4 测试与跑通
- 每提交一次都要本地通过 `make test`
- 重要模块要求 ≥ 80% 覆盖率
- 端到端测试用一份 fixtures 模拟瑶瑶 30 天的事件流

---

## 5. 成本与隐私红线

1. **本地优先**：日常对话、记录、信号提取**必须在本地跑**
2. **云端调用前压缩**：发到 Codex/GPT 的输入必须经过 `src/agents/context_compressor.py`
3. **每月用量上限可配置**：默认 100k tokens / 用户 / 月，到顶降级
4. **数据不外发**：测试和 demo 用合成数据，不上传真实瑶瑶数据到任何外部 SaaS
5. **导出 / 删除**：用户能随时一键导出 markdown + JSON，删除时同步清云端缓存

---

## 6. 提交与决策记录

### 6.1 commit 规范
```
<类型>(<scope>): <一句话>

类型：feat | fix | chore | docs | test | refactor | adr
scope：agent / core / api / prompts / docs / infra
```

### 6.2 何时写 ADR
做了下列任意一项时，必须在 `decisions/` 写一条：
- 选了一个非显而易见的库/模型
- 改了 ARCHITECTURE 中的某个设计原则
- 否决了一个 PRD 里的方案，换成另一个
- 在性能/成本/简单性之间做了重要权衡

ADR 模板：
```markdown
# NNNN: <标题>

Date: YYYY-MM-DD
Status: proposed / accepted / superseded by NNNN

## Context
<背景，为什么有这个决策>

## Decision
<选了什么>

## Alternatives
<考虑过的其他选项及为什么没选>

## Consequences
<选这个之后我们要承担什么>
```

---

## 7. 开始干活前的检查清单

在写第一行代码之前请确认：

- [ ] 我读完了 README + ROADMAP + 当前 PRD？
- [ ] 这个改动属于当前 Phase 的范围吗？
- [ ] 有没有违反 VISION.md §5 的"不做什么"清单？
- [ ] 有没有触及 §2 的红线？
- [ ] 这次预计要改的文件数和行数是否在 PRD 范围内？

如果任意一个回答模糊，停下来回到 Cowork。

---

_最后更新：2026-05-15 — Cowork 起草。Code 模式接手后可补充工程约定细节，但不要修改 §1-3、§5 红线部分。_
