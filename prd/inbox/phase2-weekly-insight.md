# PRD DRAFT: Phase 2 — 洞察周报（Weekly Insight）

> 🚫 **SUPERSEDED — 已废弃**
>
> 本草稿已被 [`prd/phase2-weekly-insight.md`](../phase2-weekly-insight.md)（accepted, 2026-05-26）取代。
> 保留此文件仅作历史追溯（看 §8 原八条开放问题）。**不要按此文件实现。**
>
> 历史信息：
> 起草时间：2026-05-26（Phase 1 §6 follow-ups 收尾后）
> 起草者：Code 模式（自主时段）
> 涉及文档：[ROADMAP.md Phase 2]、[VISION.md §1]、
>           [ARCHITECTURE.md §6]、[decisions/0001 F2 / F6 / F7]

---

## 1. Why（为什么做）

对应 ROADMAP **Phase 2：洞察周报**。

Phase 1 已经把"原始事件 → 信号"这条管道跑通：65 条 fixture 事件下，
3/3 召回、precision ≥ 60%；信号现在带 `delta_from_last_period`，热度图横
轴按月龄分桶。但**信号本身是冰冷的数据点**——一条 "interest_pattern,
music, intensity=0.7" 不会让父母 say "诶，我没注意到这一点"。

Phase 2 要把这堆数据加工成一份**每周一次、能被父母真正读完的洞察周报**。
对照 VISION §1："给父母提供他们没注意到的视角"是产品的核心承诺；
Phase 2 是这个承诺第一次以"完整产物"的形式落到用户眼前。

对照 decisions/0001 F2：北极星指标延后到 v0.2 才正式定义，但 Phase 2
末尾我们必须能看出"AI 给出过让你意外的洞察了吗"——周报是这个判断的
载体。如果连续两周都没有任何"诶"的瞬间，整个 MVP 假设要回 VISION
重审。

---

## 2. What（本次范围）

### 2.1 必须做（In Scope）

**1) 上下文压缩 Agent** `src/agents/context_compressor.py`

Phase 2 的第一道关。云端洞察 Agent 不能直接吃原始事件流——按
decisions/0001 F6 估算，单周 ~30-50 条事件直发 Claude 会让单次 token
开销在 ~15-25k，远超 CLAUDE.md §5 的 100k/月预算。

接口：

```python
def compress_week_context(
    child_id: str,
    week_start: date,
    *,
    max_tokens: int = 4000,
) -> CompressedContext

@dataclass(frozen=True)
class CompressedContext:
    week_start: date
    week_end: date
    child_age_months: int          # 当周月龄（取 week_start 那一天的）
    signals: list[SignalSummary]   # 全量信号（精简成 60 字以内的 1 行摘要）
    event_highlights: list[str]    # ≤ 8 条最值得保留的原文事件摘要
    period_deltas: list[DomainDelta]  # 每个活跃 domain 的 delta + 同月龄基线（占位）
    raw_token_count: int           # 用于成本日志
```

压缩策略（**本 PRD §3 选型决策已锁，不再 OR**）：

- **信号层全量进入**——它们已经是聚合过的判断
- **事件层只保留**：
  - `type=milestone` 全保留（里程碑稀少且重要）
  - 每个活跃信号的 `evidence_event_ids` 各取最多 2 条
  - 其余事件按"未被信号 cover 的 domain"原则各取 1-2 条
- 删除 `raw_text`，只保留 `summary`（recorder 已经做了浓缩）
- 输出走 `parse_json_strict`，类型受 Pydantic 守门

**2) 洞察 Agent v0** `src/agents/insight_writer.py`

Phase 2 的核心。**第一次走云端**（CLAUDE.md §5 红线提醒：必须在
context_compressor 之后调用）。

接口：

```python
def write_weekly_insight(
    ctx: CompressedContext,
    *,
    backend: Literal["claude", "local-fallback"] = "claude",
) -> WeeklyInsight

@dataclass(frozen=True)
class WeeklyInsight:
    id: str                        # ins_YYYYMMDD_NNN
    child_id: str
    week_start: date
    week_end: date
    sections: list[InsightSection]
    open_questions: list[str]      # 留给父母的问题（不是结论）
    sources_used: list[str]        # 引用的 signal/event id（可追溯）
    model_used: str
    tokens_in: int
    tokens_out: int
    backend: Literal["claude", "local-fallback"]
```

模型选型（详见 §3）：默认 Claude Haiku 当主，预算耗尽降级 qwen2.5:7b
本地兜底。

**3) 周报模板** `src/prompts/insight_writer.md`

按 ROADMAP M2.3："亮点 / 趋势 / 下周关注 / 开放问题"——但写法上
有几条硬约束（按 decisions/0001 F1 的"父母赋能"原则，**绝不指令化**）：

- "亮点"必须是**观察句**，不是评价句。
  - ❌ "本周表现优秀"
  - ✅ "本周三次主动靠近钢琴；上周仅一次（详见 `evt_…`）"
- "趋势"必须带 `delta_from_last_period` 数字，且明确标注"小样本，仅供参考"
- "下周关注"必须是**开放问题**而非任务清单
  - ❌ "建议每天讲故事"
  - ✅ "上周第三次出现'拒绝睡前刷牙'——可能是阶段性还是有具体诱因？"
- "开放问题"必须 ≥ 1 条，是给父母的反思引子，不是给 AI 的 TODO

**4) 反馈结构** `src/api/main.py` + 前端

ROADMAP M2.4：每条洞察行末附三态反馈按钮。**多元反馈追踪**（按
decisions/0001 F1 调整后）：

- `accuracy`: 准确 / 不准确 / 不确定
- `value`: 有启发 / 无启发 / 错过重点
- 自由文本（可选）

落库到 `insight_feedback` 表（新建），schema 草案见 §5。
**不做**采纳率（F1 显式禁止）；**不做**自动化"按反馈调 prompt"（Phase 4）。

**5) Web 前端 `/weekly`** `web/app/weekly/page.tsx`

第四块屏幕（前 3 屏在 Phase 1）：

- 顶部：可选周次切换（仅"本周 / 上周 / 上上周" 3 档）
- 主体：渲染 `WeeklyInsight.sections`，每节末尾附反馈控件
- 底部："开放问题" 单独一栏
- 旁注："本份周报基于 N 条事件、M 个信号生成，模型=…，tokens=…"
  （透明化是赢得信任的第一步）

**6) 周报生成的触发与持久化**

- 接口：`POST /insights/generate?child_id=…&week_start=YYYY-MM-DD`
- 落库：`weekly_insights` 表（新建）+ `insight_sections` 表
- **不做** cron / 自动每周一发——Phase 4 的反馈闭环再加；
  Phase 2 由父母手动触发即可

**7) 测试与基线**

- context_compressor：合成 50-event 输入，断言输出 < 4k tokens；
  断言 milestone 事件全部在压缩输出里
- insight_writer：mock LLMClient，断言输出 schema 合法 + sources_used
  全部能在输入里找到（防幻觉）
- 反馈接口的 HTTP shape
- 端到端：在 Phase 1 的 fixture 上跑一份完整周报，验证 sections ≥ 3、
  open_questions ≥ 1、sources_used ⊆ 输入信号 ∪ 事件 id
- 覆盖率门槛：≥ 80%（Phase 1 是 75%，逐 Phase 抬一档）

### 2.2 显式不做（Out of Scope）

- ❌ 培养建议（这是 Phase 3 知识库 RAG 的事）
- ❌ 反馈分类与自动改 prompt（Phase 4）
- ❌ 移动端 / 推送 / 邮件投递
- ❌ 多孩子对比、跨家庭基线
- ❌ 周报历史归档与搜索（v1+）
- ❌ 自动每周生成定时任务

---

## 3. 选型决策（本 PRD 一次性裁定）

按 Cowork 流程，以下决策需要瑶瑶爸爸最终拍板，但 Code 在此先给推荐 +
理由 + 触发回审条件。

**3.1 主洞察模型：Claude Haiku vs Sonnet?**

→ **推荐 Claude Haiku**（按 decisions/0001 F6 的成本测算）。

理由：周报输入压缩到 ≤ 4k tokens、输出 ≤ 1k tokens；Haiku 足够。
Sonnet 提升的"洞察深度"对单用户验证 MVP 假设非必需。

**触发升级条件**（写进 ADR）：
- 连续 4 周父母对周报的 `value=有启发` 比例 < 30%
- 或父母给"模型理解错"的反馈率 > 20%

**3.2 本地兜底：用什么模型？**

→ **推荐 qwen2.5:7b-instruct**，仅在云端预算耗尽（CLAUDE.md §5 的
100k tokens/月）时启用。

理由：Phase 1 我们用 3B 已验证它能做"判断接受/拒绝"；写完整周报需要
更连贯的中文叙述能力，3B 偏弱、7B 在本机可跑。
Code 实现时测一次 7B 写一份周报的耗时基线，落 `reports/phase2-baseline.md`。

**3.3 prompt 缓存：用 Anthropic prompt caching 吗？**

→ **用**——周报 prompt 的 system 段（包含模板 + 风格约束）每周复用。
按 Anthropic 文档预计可节省 ~70% input tokens。

实现要点：把不变段放在 system 里 + 标 `cache_control`；
Phase 1 的 LLMClient 已经有 backend 路由，加一层 `cache_namespace="insight_v1"`
即可。

**3.4 时区与"周"的定义**

→ **推荐"本地周一 00:00 → 下周一 00:00"**（中国习惯）。

不在 PRD 主体讨论 ISO 周；如果将来要支持多用户多时区，统一记录
`tzinfo`，UI 层再换算。

---

## 4. 数据模型（schema 草案）

### 4.1 `weekly_insights` 表（新建）

```sql
CREATE TABLE weekly_insights (
    id TEXT PRIMARY KEY,                       -- ins_YYYYMMDD_NNN
    child_id TEXT NOT NULL REFERENCES children(id),
    week_start TEXT NOT NULL,                  -- ISO date (Mon)
    week_end   TEXT NOT NULL,                  -- ISO date (next Mon, exclusive)
    child_age_months INTEGER NOT NULL,         -- frozen at write time
    sections_json TEXT NOT NULL,               -- list[InsightSection]
    open_questions_json TEXT NOT NULL,         -- list[str]
    sources_used_json TEXT NOT NULL,           -- list of signal/event ids
    backend TEXT NOT NULL,                     -- 'claude' | 'local-fallback'
    model_used TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    created_at TEXT NOT NULL                   -- ISO 8601 with offset
);
CREATE UNIQUE INDEX idx_weekly_insights_child_week
    ON weekly_insights(child_id, week_start);
```

### 4.2 `insight_feedback` 表（新建）

```sql
CREATE TABLE insight_feedback (
    id TEXT PRIMARY KEY,
    insight_id TEXT NOT NULL REFERENCES weekly_insights(id),
    section_idx INTEGER NOT NULL,              -- which section the feedback applies to
    accuracy TEXT,                             -- 'accurate' | 'inaccurate' | 'unsure' | NULL
    value TEXT,                                -- 'inspiring' | 'unhelpful' | 'missed_point' | NULL
    free_text TEXT,
    created_at TEXT NOT NULL
);
```

### 4.3 迁移策略

按 ADR-0002 (signals-schema)：append-to-`SCHEMA_SQL` only，不引入 Alembic。
Phase 2 启动时检测两张表是否存在，缺则建。

---

## 5. 评估与基线

按 decisions/0001 F2 的"盲测对照"约束，本 PRD 在实现完成后必须交付：

- **盲测对照实验**：
  - A 组：Phase 2 完整 pipeline 输出的周报
  - B 组：纯 prompt（无信号、无压缩、无模板）让 Claude Haiku 直接读 raw events
  - C 组（可选）：通用 ChatGPT/Gemini 接口同样输入
  - 让作者**蒙眼**给三份打分（"哪份更击中"），连续 4 周打 4 份
  - 通过门槛：A 组中标率 ≥ 50%（即不能被通用 prompt 打平）

- **token 成本基线**（写进 `reports/phase2-baseline.md`）：
  - 单份周报实际 input/output tokens
  - 月预估（按周 4 次 × 1 个孩子）
  - 是否在 100k tokens/月 预算内（CLAUDE.md §5 红线）

- **disagreement 日志连接**：
  - Phase 1 已经在 logger 里输出 `signal.disagreement reject/intensity_drift`
    （见 `reports/phase1-baseline.md` §6 收尾）
  - Phase 2 把这些日志结构化进 `signal_review` 表（草案：`signal_id, source,
    rule_hint, llm_intensity, llm_accept, reviewer_label, reviewer_at`）
  - 4 周末跑一次手工 review，决定 ADR-0001 F7 的 3B → 7B 升级（针对信号层）

---

## 6. 实现顺序与时间预算（建议）

按 ROADMAP Phase 2 = 2 周（10 工作日），细分：

| 步骤 | 文件 | 预算 |
|------|------|-------|
| 1. context_compressor + tests | `src/agents/context_compressor.py` | 1.5 天 |
| 2. weekly_insights schema + migration | `src/core/db.py` | 0.5 天 |
| 3. insight_writer + prompt | `src/agents/insight_writer.py`, `src/prompts/insight_writer.md` | 2 天 |
| 4. LLMClient 加 prompt caching | `src/core/llm_client.py` | 0.5 天 |
| 5. API: `POST /insights/generate`, `GET /insights/:id` | `src/api/main.py` | 0.5 天 |
| 6. 反馈表 + API | `src/api/main.py` | 0.5 天 |
| 7. `/weekly` 前端 | `web/app/weekly/` | 1.5 天 |
| 8. 端到端测试 + 盲测准备 | `tests/test_insight_e2e.py`, `reports/` | 1.5 天 |
| 9. 第一份真实周报 + 评分 | (作者动手) | 1.5 天（跨周） |

合计 ~10 工作日（含一次 LLMClient 重构 buffer）。

---

## 7. 与 Phase 1 的接口契约

本 PRD 不动 Phase 1 任何已落库的数据形状；只读 + 追加：

- 读：`signals` (status='active' WHERE `last_seen_at` ∈ week)
       `events` (timestamp ∈ week)
       `event_embeddings` (Phase 2 暂不直接用，预留给 Phase 3 RAG)
- 写：`weekly_insights`、`insight_sections`、`insight_feedback`
       （三张表均为新建，无主表迁移）

**禁止改动**：`signals.delta_from_last_period`、`signals.intensity` 任何
计算逻辑——它们是 Phase 1 已稳定的输出，Phase 2 只读不写。

---

## 8. 待 Cowork 决议（开放问题）

以下条目 Code 模式不能自决，必须等回到 Cowork 与瑶瑶爸爸讨论：

1. **§3.1 模型选型 Haiku vs Sonnet** — 推荐 Haiku，但作者可能希望第一份
   周报用 Sonnet 看一次"上限在哪"再决定下沉到 Haiku。
2. **§3.2 本地兜底用 7B 还是 3B 多次组合** — 7B 占用 ~5GB 内存；如果
   作者的 Mac 内存吃紧，可能要回到 3B + 多步 chain 的方案。
3. **§3.3 prompt cache TTL 选 5min 还是 1h** — 一周一次的频率两者差异
   不大，但 1h 在我们手动反复试模板时更省钱。
4. **§4.1 weekly_insights 主键** — 现在是 `ins_YYYYMMDD_NNN` 与信号同
   风格，是否改用 UUID 以避免同周二次生成时的 N 冲突？
5. **§5 盲测对照打分制** — 4 周 4 份样本量极小；是否一开始就引入"配偶
   评分"作为 inter-rater 校验？涉及 ADR-0004 的"家庭成员协同"扩展，
   作者本人未表态。
6. **§4.2 反馈表的 `section_idx`** — 反馈定位到 section 还是更细的
   "段落级"？后者更准但需要前端给每段加 anchor，工作量翻倍。
7. **是否在 §2.1#2 加入"洞察 Agent 必须给出 evidence 引用"的硬约束**
   （类似 sources_used 字段）—— 我倾向**强制**，但这会限制 LLM 的语言
   表现力。
8. **VISION 协调**：Phase 2 不动 VISION/DIFFERENTIATION，但写完后我们
   是否需要在 VISION §1 加一段"周报作为洞察落点"的明示？这是 Cowork
   决定，不是 Code。

---

## 9. 红线对照（Code 自检清单）

- [x] 不修改 VISION.md / DIFFERENTIATION.md（本 PRD 是新建文件）
- [x] 不超 100k tokens/月（§3.1 + §5 token 基线 + 兜底机制）
- [x] 不在运行时让 Agent 长驻云端（仅父母手动触发周报）
- [x] 不悄改 ARCHITECTURE §1-2（schema 通过 ADR-0002 的 append 流程加表）
- [x] 测试用 `xiaoming` 不用 `yaoyao`（ADR-0001 F16）
- [x] PRD 在 `prd/inbox/` 而非 `prd/` 根目录——**等 Cowork 审定后才搬**

---

_起草于 2026-05-26 by Code 模式（自主时段）。等待 Cowork 审。_
