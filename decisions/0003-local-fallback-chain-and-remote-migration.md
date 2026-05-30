# 0003: 本地兜底用 3B chain，并保留远端可迁移占位

Date: 2026-05-26
Status: accepted

## Context

Phase 2 PRD `prd/phase2-weekly-insight.md` §3.2 需要决定云端预算耗尽
（CLAUDE.md §5 的 100k tokens/月红线）后的兜底模型方案。

约束面：

- 作者主力机为 8GB Mac。同时跑 Chrome / IDE / Ollama / FastAPI 时，
  qwen2.5:7b-instruct（~5GB 内存）会让系统进入交换页，影响开发体验。
- Phase 1 已验证 qwen2.5:3b-instruct 能做"信号判断接受/拒绝"，但写
  完整周报需要更连贯的中文叙述能力，单步 3B 偏弱。
- 作者已表态：未来可能把后端部署到自建服务器，本地只保留前端 + 录入。
  这意味着兜底的"本地"概念短期是"作者 Mac"，中期可能是"作者的小服务器"。
- 不能引入 LangChain（CLAUDE.md §2 红线）；多步链由我们自己用 LLMClient
  组合。

## Decision

兜底分两部分。

### 一、本地兜底用 3B + 两步 chain

`src/agents/insight_writer.py` 的 `local-fallback` 路径走两步：

1. **Step 1 — 抽要点**：输入 `CompressedContext`，让 qwen2.5:3b 输出
   bullet 形 `InsightDraft`（≤ 600 tokens，schema 受 Pydantic 校验）。
2. **Step 2 — 串叙述**：把 draft 当 prompt 的一部分喂回 3B，让它写
   ≤ 1000 tokens 的自然中文周报。

理由：3B 在"短任务 + 严格 schema"上稳定，分两步能把"判断"和"叙述"解耦，
减少长程一致性问题。两步合计 ~2-3 秒（基于 Phase 0 baseline 推算），
可接受。

### 二、LLMClient 路由保留 `remote-local` 占位

`src/core/llm_client.py` 的 backend 枚举为
`Literal["claude", "local", "remote-local"]`：

- `local`：走本机 Ollama（当前实现）。
- `remote-local`：走作者自建服务器上的 Ollama-compatible endpoint。
  实现上只需读环境变量 `BGH_REMOTE_LOCAL_URL` 替换 base_url，
  其余调用与 `local` 同构（vLLM / Ollama / TGI 都暴露 OpenAI 风格 API）。
- 业务代码（agent 层）只关心 `backend` 是云端还是本地族；
  `local` vs `remote-local` 的切换由配置决定，不影响 prompt / 重试 /
  缓存逻辑。

`weekly_insights.backend` 字段记录三态之一，便于回溯每份周报实际用了哪条
路径。

## Alternatives

### A. 直接用 7B（被否）

- 8GB Mac 体验差；
- 即便未来切到服务器跑 7B，业务代码也无需立刻为 7B 写新分支——
  `remote-local` 路径切 endpoint 即可，让本地 vs 远端的差异收敛到部署层。

### B. 单步 3B（被否）

- Phase 1 验证 3B 单步写长篇中文叙述时连贯性下滑明显；
- 拆两步并不显著增加复杂度（LLMClient 已支持复用 system prompt）。

### C. 完全不做本地兜底，云端预算耗尽就报错（被否）

- 触顶后周报功能直接失效会破坏 ROADMAP Phase 2 验证 ——"连续 4 周收到
  周报"是 M2.5 的判定条件之一；一次断流就有可能让样本量不够。
- 兜底虽然质量打折扣，但能让 pipeline 持续跑，反馈数据不断流。

### D. 引入更大模型（13B/14B）做兜底（被否）

- 本机跑不动；
- 远端跑大模型与"自建服务器"成本倒挂，不如直接走云端（Sonnet/Haiku）。

## Consequences

接受：

- 兜底质量低于云端（已知 trade-off）。Phase 2 §5 的盲测对照里
  应单独记录"`local-fallback` vs `claude` 的得分差距"，作为是否升级
  本地模型的依据。
- LLMClient 多一条 `remote-local` 分支，需要单元测试覆盖（mock endpoint）。
- 部署到自建服务器时，Ollama-compatible API 必须以同样 model 名
  （`qwen2.5:3b-instruct`）暴露——否则要在 LLMClient 加 model name 重映射，
  这条留给真正部署时再写一条 ADR。

放弃：

- 兜底路径暂不享受云端的 prompt cache（Anthropic 独有），单次 input 全量。
  同等 token 量本地推理仍便宜，可接受。
- 不为多模型选型保留更通用的抽象（OpenAI / Together / DeepSeek）——
  必要时再开 ADR-0004。

## Follow-ups

- Phase 2 实现完成后在 `reports/phase2-baseline.md` 给出 3B chain 写一份
  完整周报的耗时与（盲测打分意义上的）质量基线。
- 若 4 周盲测里 `local-fallback` 得分 < 30%（即父母明显感到"换了引擎就
  退步"），考虑：(a) 把 chain 拆成 3 步；(b) 切到自建服务器跑 7B；
  (c) 在 prompt 里做更激进的模板填空，让 3B 主要做"填空"而非"创作"。
