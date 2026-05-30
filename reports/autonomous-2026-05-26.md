# 自主时段进度报告 — 2026-05-26

> 你离开 10 小时期间 Code 模式自主推进的工作。
> 全部在 Phase 1 §6 follow-ups + Phase 2 PRD 草稿范围内，**没有越界**。

---

## 总览

| # | 任务 | 状态 | 关键产出 |
|---|------|------|---------|
| 1 | wire `compute_period_delta` 进 `_build_signal` | ✅ | 信号现在带真实 delta；emotion_pattern 故意置 None |
| 2 | backfill CLI smoke test | ✅ | 5 个新测试，CLI 覆盖率 61%→82% |
| 3 | LLM disagreement-rate 日志 | ✅ | log-only，2 条触发条件，2 个新测试 |
| 4 | 复跑 lint + tests，刷新 baseline | ✅ | 137 passed, 89.79% cov, ruff + mypy clean |
| 5 | 起草 Phase 2 PRD | ✅ DRAFT | `prd/inbox/phase2-weekly-insight.md`，待 Cowork 审 |

**红线全部守住**：
- 没动 VISION.md / DIFFERENTIATION.md / ARCHITECTURE.md §1-2
- Phase 2 实现一行没写（PRD 在 inbox 待审）
- 测试用 xiaoming 不用 yaoyao
- 没改全局 git config，没做破坏性 git 操作

---

## 1. 数据库状态（重要）

我**没碰** `data/babygrow.db`（你的原始 yaoyao 数据）：
```
data/babygrow.db          May 20 16:51   (你原来的，未动)
data/demo_xiaoming.db     May 25 22:48   (我新建的演示库)
```

`demo_xiaoming.db` 内容：
- 1 个 child: xiaoming
- 65 条 events（fixture 灌入）
- 0 条 signals（没跑过 extractor）

如果想看前端，启动后端时记得 `BGH_DB=./data/demo_xiaoming.db`，否则会看到空的 yaoyao 库。

---

## 2. 文件变更清单

### 修改

- `src/agents/signal_extractor.py`
  - import `compute_period_delta`
  - 在 `extract_for_child` 主循环里调 `_compute_signal_delta`
  - 新增 `_log_disagreement` 方法 + `_compute_signal_delta` 函数
  - `_build_signal` 加了 `delta: float | None = None` 参数

- `reports/phase1-baseline.md`
  - 头部时间戳改 2026-05-26，刷新数字
  - §4：tests 127→137, cov 86.94%→89.79%
  - §6 改名为"Closed follow-ups"，三条标 ✅
  - §7 新开"New open follow-ups"承接 Phase 2

### 新增

- `tests/test_backfill_cli.py` — 5 个 smoke tests（happy / empty / 未知 child / 坏 JSON / 不存在文件）
- `tests/test_signal_extractor_unit.py` — 末尾追加 5 个 tests（3 个 delta wiring + 2 个 disagreement log）
- `prd/inbox/phase2-weekly-insight.md` — **DRAFT**, 240+ 行，含 9 节 + 8 条待 Cowork 决议

### 没动

- 任何 prompt 文件（`src/prompts/*`）
- recorder agent
- frontend (`web/`)
- VISION / DIFFERENTIATION / ARCHITECTURE 任何一行

---

## 3. 现在还没起服务

我**不能**在 sandbox 里启 FastAPI / Next.js（不能绑 localhost）。两条命令的窗口需要你自己开：

```bash
# 终端 A — 后端
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export UV_CACHE_DIR="$TMPDIR/uv-cache" BGH_DB="./data/demo_xiaoming.db"
uv run --no-sync uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload

# 终端 B — 前端
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper/web
npm run dev
```

打开 http://localhost:3000/timeline 就有 65 条 fixture 数据。

要看到 signals + delta 真数据，先 curl 一次（需要 ollama serve 在跑）：
```bash
curl -X POST 'http://127.0.0.1:8000/signals/extract?child_id=xiaoming&window_days=14&now=2026-05-23T20:00:00%2B08:00'
```

---

## 4. Phase 2 PRD 草稿要点（你回来需要看）

文件：`prd/inbox/phase2-weekly-insight.md`

按 ROADMAP Phase 2 = 2 周 / 5 个 milestone (M2.1-M2.5) 拆分，覆盖：
- 上下文压缩 Agent (M2.1)
- 洞察 Agent v0 走 Claude Haiku (M2.2)
- 周报模板（亮点/趋势/下周关注/开放问题）(M2.3)
- 三态反馈 + 自由文本（按 ADR-0001 F1 不做"采纳率"）(M2.4)
- `/weekly` 第四屏 (M2.5)

**§8 列了 8 条等你决议的开放问题**，最重要三条：
1. 主模型 Haiku vs Sonnet
2. 本地兜底用 7B 还是 3B 多步组合（取决于你的 Mac 内存）
3. 是否引入"配偶评分"做 inter-rater 校验（涉及 ADR-0004）

我在 §3 都给了推荐 + 触发回审条件，但**最终拍板必须 Cowork**。

---

## 5. 我没做但你回来可以接着做的

- 起前后端 → 浏览器开 http://localhost:3000/timeline 看 Phase 1 实际效果
- Cowork 审 `prd/inbox/phase2-weekly-insight.md`，决议 §8 的 8 条
- 如果决议通过，把它从 inbox 搬到 `prd/phase2-weekly-insight.md`，
  ROADMAP 状态板 Phase 2 → 🟡 进行中
- 如果有想法觉得 PRD 草稿哪里没切中，直接说"PRD §X.Y 改成…"，
  我下次进 Code 模式就动

---

## 6. 验证建议

```bash
# 确认全套测试 + lint 都干净
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export UV_CACHE_DIR="$TMPDIR/uv-cache"
uv run --no-sync ruff check src tests   # All checks passed!
uv run --no-sync mypy                    # Success: no issues found in 33 source files
uv run --no-sync pytest -m "not integration"  # 137 passed, 89.79% cov

# 单独看新增的测试通过
uv run --no-sync pytest tests/test_backfill_cli.py tests/test_signal_extractor_unit.py --no-cov
```

期望：全绿。

---

_完。_
