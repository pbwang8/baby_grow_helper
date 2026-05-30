# PRD: Phase 1 — 信号体系 + 极简前端

> Code 接班的第二份任务卡。
> 完成本 PRD 后，整个项目第一次出现"理解"的雏形：原始事件被聚合成"信号"，
> 你能在浏览器里看到一条时间轴 + 一张热度图，并对它说"对，瑶瑶最近确实迷音乐"。
>
> 工作时长预估：2 周（10-14 个工作日）

---

## 1. Why（为什么做）

对应 ROADMAP **Phase 1：信号体系**。

Phase 0 已经把"对话 → 结构化事件 → 落库"跑通（recorder 9/10 valid + 9/10
type-match @ qwen2.5:3b-instruct），但落到库里的还是离散事件。
Phase 1 要做的是把离散事件**聚合成信号**——也就是把"瑶瑶今天玩了磁力片"
这种零散观察，归并成"过去 14 天里 6 次出现拼搭活动，强度从弱→中→强"
这种**带时间维度、带强度、带变化量**的判断。

对照 VISION §1："识别重复模式 + 成长变化"是产品护城河。Phase 1 是
这条护城河的第一铲土——没有信号层，后面 Phase 2 的洞察 Agent 没东西可
洞察，云端调用就只能往 raw 事件流上灌，token 成本立刻失控。

对照 DIFFERENTIATION：竞品都在做"事件相册"，我们做"成长轨迹"。
Phase 1 是把这条差异化第一次落到代码里。

---

## 2. What（本次范围）

### 2.1 必须做（In Scope）

**1) 信号数据模型** `src/core/models.py` + `src/core/db.py`

在 `signals` 表（ARCHITECTURE §5 已建表占位）上落地以下 schema，作为 Pydantic
模型 + DB schema：

```python
class Signal:
    id: str                          # sig_YYYYMMDD_NNN
    child_id: str
    signal_type: Literal[             # 5 类，按 ROADMAP M1.1 拍板
        "interest_pattern",           # 兴趣模式
        "emotion_pattern",            # 情绪模式
        "skill_pattern",              # 技能模式
        "anomaly",                    # 异常变化
        "growth_leap",                # 成长跃迁
    ]
    domain: list[str]                 # 复用 recorder 的 domain 词表
    intensity: float                  # 0.0-1.0，归一化
    child_age_months: int             # 信号当前所在的孩子月龄（关键!）
    delta_from_last_period: float     # 与上一窗口比的变化量，可正可负
    confidence: float                 # 0.0-1.0
    first_seen_at: datetime
    last_seen_at: datetime
    evidence_event_ids: list[str]     # 至少 2 条，少于 2 条不立信号
    status: Literal["active", "dormant", "dismissed"]
    notes: str                        # 留给后续 Insight Agent 用的"为什么立这个信号"
```

约束：
- `evidence_event_ids` 必须 ≥ 2，单点不成信号
- `child_age_months` 在写入时根据 child.birthday 计算，落库后**不再随时间漂移**
  （这条信号是"5 个月前那个 22 月龄的瑶瑶"的事，不是当前的）
- 所有时间戳 ISO 8601 + 时区

**2) 信号提取 Agent v0** `src/agents/signal_extractor.py`

按 M1.2 走"规则 + 本地模型"混合：

- **规则层**（先跑、便宜、确定性）：
  - 滑动窗口 14 天，按 `domain` 聚合事件计数
  - 同 domain 出现 ≥ 3 次 → 候选 `interest_pattern`
  - `type=milestone` 单条 → 候选 `growth_leap`
  - 同情绪连续 ≥ 3 天出现在同一情境（context 字段近似匹配） → 候选 `emotion_pattern`
- **LLM 层**（候选过来后再跑，省 token）：
  - 输入：候选信号 + 它的 evidence events 摘要
  - 输出：`{ accept: bool, intensity: float, notes: str }`
  - 模型：本地 qwen2.5（默认 3B；7B 升级见 §3 选型决策）
- 跑完后写入 signals 表，状态 `active`

**触发方式**：
- `POST /signals/extract?child_id=...` 手动触发
- CLI: `python -m src.agents.signal_extractor --child=yaoyao --window=14`
- 不做 cron 自动触发（Phase 4 的反馈闭环再加）

**3) 嵌入与检索基础** `src/core/embeddings.py`

把 Phase 0 留空的 `event_embeddings` 表填上：
- 嵌入模型：`BGE-small-zh-v1.5`（按 ARCHITECTURE §7；本 PRD 锁定，不再 OR）
- 写入路径：每条 event 落库后异步算嵌入
- 提供两个原语：
  - `embed_text(text: str) -> list[float]`
  - `find_similar_events(event_id: str, k: int = 5) -> list[Event]`
- 不做 reranker、不做 BM25 混合（Phase 2 真用上检索时再加）

**4) 信号变化量计算** `src/core/signal_delta.py`

这是 ROADMAP M1.3 那条"必须能展示随发展阶段的变化"的关键。

接口：
```python
def compute_period_delta(
    child_id: str,
    domain: str,
    current_window: tuple[date, date],
    prior_window: tuple[date, date],
) -> float
```

输出 `[-1.0, +1.0]` 区间的变化量。规则：
- 当前窗口事件数 vs 上一窗口事件数，归一化
- 强度（来自 LLM 层评估）加权
- 如果上一窗口数据稀疏（< 3 条），返回 None 而不是 0
  （Phase 1 阶段，「没数据」不能伪装成「没变化」）

**5) 极简前端（推迟自 Phase 0 的 M0.4）** `web/`

按 ROADMAP 上次决议——M0.4 推迟到 Phase 1 一起做。

技术栈：Next.js + React + 不引入额外组件库（Tailwind 内联即可）。
单一目标：**让作者能在浏览器里看到信号在动**。

页面三屏：
- `/log` — 一个 textarea + "记一笔" 按钮，调 `POST /events`，返回结构化结果展示
- `/timeline` — 时间轴：events + signals 混排，点 signal 能看到 evidence_events
- `/heatmap` — 热度图：横轴=孩子月龄（不是日历日期！），纵轴=domain，
  色深=该月龄段在该 domain 的事件强度
  - **关键**：横轴必须是月龄不是日历，这样不同时间段对比才有可比性

不做：登录、用户管理、移动端响应式（≥ 1024px 即可）、暗色模式。

**6) 数据回灌脚本** `src/scripts/backfill.py` + `tests/fixtures/backfill_yaoyao.jsonl`

按 ROADMAP M1.4：
- CLI 工具，从一份 JSONL 文件批量灌入历史事件
- 每条带 `timestamp`，绕过 recorder 直接走结构化（因为是回忆，不是实时）
- 可选 flag `--re-extract-signals` 灌完跑一次信号提取
- 提供一份 fixture：50-100 条合成"小明"数据，覆盖 6 个月跨度，
  其中**埋 3 个明显模式**（音乐兴趣上升、社交退缩异常、如厕技能跃迁）
  作为信号系统的"金标准"测试集

**7) 测试与基线**

- 信号 schema 的 Pydantic 校验
- 规则层单测：构造事件流，断言候选信号
- LLM 层走 mock，验证候选 → accept/reject 的字段映射
- 端到端：跑 backfill fixture，断言 3 个埋点信号都被识别（recall ≥ 100%）
  + 误报 ≤ 2 个（precision ≥ 60%）
- 嵌入：批量算 100 条，断言总耗时 < 30s（M-series 本机）+ 平均向量维度
- 覆盖率门槛：≥ 75%（Phase 0 是 70%，逐 Phase 抬一档）

### 2.2 显式不做（Out of Scope）

- ❌ 信号 → 洞察这一跳（Phase 2）
- ❌ 周报生成、云端调用（Phase 2 才打通）
- ❌ 培养建议、知识库 RAG（Phase 3）
- ❌ 反馈按钮 / 反馈闭环（Phase 4）
- ❌ 移动端、PWA、推送通知
- ❌ 多用户、登录、权限
- ❌ 信号的人工编辑 UI（dismiss 通过 SQL 直接改即可，UI 留给 Phase 4）
- ❌ 多孩子对比（瑶瑶 vs 同龄人）——这是 v1+ 的事

---

## 3. 选型决策（本 PRD 一次性裁定，避免 Code 中途卡住）

按 `reports/phase0-baseline.md` 留下的三件 Phase 1 待裁决项，作者已表态：

**3.1 结构化模型：是否升级 7B？**

→ **暂不升级，仍用 qwen2.5:3b-instruct**。
理由：Phase 0 基线 9/10 type-match 已经过 F7 门槛；Phase 1 信号层的 LLM
角色是"对候选信号做轻判断"，不是结构化主力，对模型推理深度要求不高。
等 Phase 2 真要做云端洞察时再讨论是否抬本地模型档位。

**触发升级条件**（写进 ADR，留给 Code 看）：
- 信号 LLM 层 accept-rate 与人工标注 disagreement > 30%
- 或 backfill fixture 上 recall 跌破 80%
满足任一项 → 写 ADR 升级到 7B。

**3.2 嵌入模型：BGE-small-zh-v1.5 选型确认**

→ **确认采用**。本 PRD §2.1 第 3 条已锁定。
Code 实现时**测一份占用基线**写入 `reports/phase1-baseline.md`：
- 模型加载内存
- 单次 embed 延迟（CPU / GPU 各一组）
- 批量 100 条总耗时
- 模型文件大小

如果实测内存 > 1.5GB 或单次 embed > 200ms，回 Cowork 讨论是否换更小的（如
`text2vec-base-chinese-paraphrase`）。

**3.3 LLMClient 是否缓存 system prompt？**

→ **本 Phase 不加缓存**。
理由：Phase 1 仍纯本地，不存在 prompt caching 计费问题；Anthropic 的
prompt cache 要在 Phase 2 接入云端时再讨论。提早加一层缓存只会
把 LLMClient 复杂度抬上去，无收益。

---

## 4. Acceptance（怎么算完成）

按以下顺序跑通即视为完成：

1. `make install && make db-init && make seed`
2. `python -m src.scripts.backfill --child=xiaoming --file=tests/fixtures/backfill_yaoyao.jsonl --re-extract-signals`
3. `curl localhost:8000/signals?child_id=xiaoming` 能看到 ≥ 3 个 active 信号，
   覆盖至少 2 种 signal_type
4. 三个埋点模式都被识别（端到端测试断言）
5. 浏览器打开 `localhost:3000/log`，输入"今天瑶瑶在小区追蝴蝶追了 20 分钟，
   笑得停不下来"，看到结构化结果回显
6. `localhost:3000/timeline` 看到刚刚那条 + 历史回填事件按时间倒排
7. `localhost:3000/heatmap` 看到一张以**月龄为横轴**的热度图，
   能肉眼分辨出某个 domain 在哪几个月龄段最热
8. `make test` 全绿，覆盖率 ≥ 75%
9. `make lint` 全绿
10. `reports/phase1-baseline.md` 落盘，包含：
    - 信号提取在 backfill fixture 上的 precision/recall
    - 嵌入模型实测占用 + 延迟
    - 信号 LLM 层 token 消耗（单次 + 跑完整批 fixture 总量）

**作者亲手验收**：
连续记录 7 天真实数据后，跑一次信号提取，作者看完热度图能说出至少一句
"对，确实……"——按 ROADMAP Phase 1 的完成判定。这条不在自动化测试里，
但在 Code 模式标 ✅ 之前必须由作者点头。

---

## 5. Constraints（不能违反的约束）

- **本地优先**：信号提取不能调云端（哪怕"短"调一次也不行）
- **成本**：Phase 1 单用户月成本仍 = $0
- **依赖最小化**：新增依赖白名单 = `sentence-transformers`（BGE 加载用）+
  `next` / `react`（前端）。**仍然不要 LangChain / LlamaIndex**
- **隐私**：嵌入模型本地推理，不调云端 embedding API
- **child_age_months 不可漂移**：信号一旦立项，月龄字段冻结——这是产品语义，
  不是工程优化点，违反这条等于让"3 个月前的瑶瑶"和"现在的瑶瑶"混在一起
- **不要扩大范围**：发现"顺便也能给信号生成一段文字摘要"——停下，那是 Phase 2

---

## 6. Open Questions（实现中遇到歧义时回填这里，等 Cowork 处理）

<!-- Code 模式实现时，遇到不确定的地方追加到这里，每条带日期 + 上下文 -->
- _(待 Code 在实现中补充)_

---

## 7. 完成后回流到哪里

按 COLLABORATION 接口 B：
- 工作量与坑 → 更新 ROADMAP Phase 1 状态为 ✅ + 链接 commit hash
- 选型决策（如发现 §3 三条结论需要推翻）→ `decisions/0002-*.md` 起编号
- 性能基线（precision/recall、嵌入占用、信号 token 消耗）→ `reports/phase1-baseline.md`
- 前端模板的取舍 → 如果选了非 Next.js，写一条 ADR 说明
- backfill fixture 的"金标准"埋点说明 → `tests/fixtures/backfill_yaoyao.README.md`

---

_PRD 状态：accepted（2026-05-23）_
_作者：Cowork。Code 接手前如有疑问，先在文件底部追加 Open Questions。_
