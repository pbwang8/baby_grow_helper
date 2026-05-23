# PRD: Phase 0 — 本地服务骨架

> Code 接班的第一份任务卡。
> 完成本 PRD 后，整个项目第一次出现"可运行"的产物：你能往里说一句话，看到结构化记录落库。
>
> 工作时长预估：1 周（5-7 个工作日）

---

## 1. Why（为什么做）

对应 ROADMAP **Phase 0：架构基线**。
我们需要先把"对话 → 结构化记录 → 存储 → 检索"这条最短链路跑通，**所有后续 Agent（信号提取/洞察/建议）都建立在它之上**。
跳过它直接做 Agent 等于在沙地上盖楼。

对照 VISION：本步骤本身不直接产出"理解孩子"的能力，但它是承载所有跨年记忆的地基。

---

## 2. What（本次范围）

### 2.1 必须做（In Scope）

**1) 项目工程脚手架**
- `pyproject.toml`（Python 3.11+，包管理用 `uv`）
- `Makefile`（test / lint / fmt / run / db-init）
- 代码风格：black + ruff + mypy（严格）
- 目录按 CLAUDE.md §4.2 建立：`src/{agents,core,api,prompts}/`、`tests/`

**2) 统一的 LLM 客户端封装** `src/core/llm_client.py`
- 接口：`generate(prompt: str, system: str | None, model: str = "auto") -> LLMResult`
- `LLMResult` 包含：`text, tokens_in, tokens_out, model_used, latency_ms`
- 后端可切换：`local`（Ollama，HTTP localhost:11434）/ `cloud`（Anthropic API）
- 自动按用量记录到 `usage_log` 表（每月统计 token，对接预算 cap）
- **所有 LLM 调用必须走它，不允许散落 `requests.post(...)` 调 LLM**

**3) 数据库初始化** `src/core/db.py`
- SQLite + `sqlite-vec`（按 decisions/0001 F19 拍板，不再 OR chromadb）
- 启动时建表：`children, events, event_embeddings, usage_log`（其余表后续 PRD 加）
- 提供 `init_db()` / `get_conn()` / `transactional()` 三个原语

**4) 记录 Agent v0** `src/agents/recorder.py`
- 输入：`{ child_id, raw_text, timestamp? }`
- 调用本地 Ollama（默认 `qwen2.5:3b-instruct`）做结构化提取
- 输出 schema 严格对齐 ARCHITECTURE §3.1 中的 JSON 例子
- prompt 模板放在 `src/prompts/recorder.md`，**不许硬编码**
- 失败回退：本地模型不可用时 fail-fast 报错（暂不走云端兜底，避免成本失控；后续 PRD 引入兜底）

**5) FastAPI 最小路由** `src/api/main.py`
- `POST /events` — body: `{ child_id, raw_text }` → 调 recorder → 写库 → 返回结构化结果
- `GET /events?child_id=...&limit=...` — 倒序读取
- `GET /health` — 检查 SQLite + Ollama 是否在线
- 注意：MVP 阶段不做认证（单机自用），但接口预留 `X-User-Id` header 供 v1 加 auth

**6) 一份"瑶瑶友好"的 seed** `src/core/seed.py`
- 启动一次 `make seed` 即可建出 child = 瑶瑶（虚构生日 2023-11-01）
- 注意：所有真实数据由作者自己输入，**fixtures / 单元测试用合成孩子"小明"**（按 decisions/0001 F16）

**7) 单元 + 快照测试**
- recorder 至少 10 条中文样例的快照测试（snapshot），验证结构化稳定性
- llm_client 走 mock 测路由切换 + 用量记录
- db 模块测建表 / 写读 / 事务回滚
- 覆盖率门槛 ≥ 70%（Phase 0 暂时低于 ROADMAP 要求的 80%，后续补到位；写在 ADR）

### 2.2 显式不做（Out of Scope）
- ❌ 信号提取（Phase 1 才做）
- ❌ 周报洞察 / 培养建议（Phase 2/3）
- ❌ Web/移动前端（Phase 0 内只跑 curl / httpie）
- ❌ 用户认证、多用户隔离
- ❌ 云端 LLM 调用（Phase 0 不打通，避免在记录环节产生云端费用）
- ❌ 向量嵌入实际计算（建好 `event_embeddings` 表结构即可，留空占位；嵌入在 Phase 1 做）

---

## 3. Acceptance（怎么算完成）

按以下顺序跑通即视为完成。每条都是可执行的命令：

1. `make install && make db-init` — 干净环境从零起来
2. `ollama pull qwen2.5:3b-instruct && make run` — 服务跑起来
3. `curl localhost:8000/health` 返回 `{"ok": true, "sqlite": true, "ollama": true}`
4. ```
   curl -X POST localhost:8000/events \
     -H "Content-Type: application/json" \
     -d '{"child_id":"yaoyao","raw_text":"今天瑶瑶第一次自己尿尿了，特别兴奋还跳起来庆祝"}'
   ```
   返回结构化 JSON，包含 `summary, type=milestone, domain=[self_care,independence], emotion=[proud,excited]` 之类
5. `curl localhost:8000/events?child_id=yaoyao&limit=5` 能读出刚才那条
6. `make test` 全绿，覆盖率 ≥ 70%
7. `make lint` 全绿（ruff + mypy --strict）
8. `usage_log` 表里能查到一条 ollama 调用记录（tokens_in/out）

---

## 4. Constraints（不能违反的约束）

- **成本**：Phase 0 不调云端，单用户月成本 = $0（仅本地 + 电费）
- **隐私**：所有真实事件数据落本地 SQLite，不发任何远端
- **依赖最小化**：除 fastapi / sqlite-vec / httpx / pydantic / 测试库外，别引入新框架；**不要 LangChain / LlamaIndex**
- **不要扩大范围**：发现"顺便也能做信号提取"——停下，回 Cowork 加 PRD
- **i18n_locked 区域**：本 PRD 暂无锁定文案，Phase 2 起会有

---

## 5. Open Questions（实现中遇到歧义时回填这里，等 Cowork 处理）

<!-- Code 模式实现时，遇到不确定的地方追加到这里，每条带日期 + 上下文 -->
- _(待 Code 在实现中补充)_

---

## 6. 完成后回流到哪里

按 COLLABORATION 接口 B：
- 工作量与坑 → 更新 ROADMAP Phase 0 状态为 ✅ + 链接 commit hash
- 选型决策 → `decisions/0002-*.md`（如发现需要换模型 / 加库）
- 性能基线（recorder 平均延迟、token 消耗）→ `reports/phase0-baseline.md`

---

_PRD 状态：accepted（2026-05-17）_
_作者：Cowork。Code 接手前如有疑问，先在文件底部追加 Open Questions。_
