# `backfill_xiaoming.jsonl` — 金标准 fixture

> Phase 1 PRD §2.1#6 + §7：信号系统的"端到端"测试集。

## 谁是小明

合成虚拟孩子。**永远不是瑶瑶**（ADR 0001 F16 — 真实数据不进测试）。

- `child_id`: `xiaoming`
- 出生日：`2023-06-01`
- fixture 跨度：2025-12-02 → 2026-05-21（~6 个月）
- 月龄范围：30 → 35 个月
- 行数：61 条事件

## 三个被有意"埋"进去的模式

测试 `tests/test_backfill_e2e.py` 在 `now=2026-05-23` 时跑 SignalExtractor，
要求**召回 = 100%**（这三个都被识别）+ **精确率 ≥ 60%**（误报 ≤ 2 个）。

### 1. 音乐兴趣上升（→ `interest_pattern`，domain=`music`）

近 14 天内（2026-05-09 ~ 2026-05-21）出现 ≥ 3 条 `domain=music` 事件，
强度递进——从"主动喊放音乐"→"敲小鼓 10 分钟"→"边搭积木边哼歌"。

证据 ID 关键词：`2026-05-02`, `05-06`, `05-08`, `05-12`, `05-15`, `05-17`, `05-21`。

### 2. 如厕技能跃迁（→ `growth_leap`，domain=`self_care` + `independence`）

2026-05-04 第一次主动说"要尿尿"——`type=milestone`。
紧随其后 05-10 / 05-19 自我巩固，rule 层会把这两条作为同 domain 的关联证据
attach 到 milestone evidence 里。

### 3. 社交退缩异常（→ `anomaly`，domain=`social`）

前一窗口（2026-04-23 ~ 05-08）几乎没有 `domain=social` 事件——而往前推到
12 月-1 月，社交活动很密集（小区追跑、邻居家过家家、幼儿园生日会）。
当窗口逻辑滑到 5 月，prior 窗口仍能看到 4 月初的小公园社交（04-02），
之后社交骤减——单独玩沙子、独自听音乐、幼儿园不愿进教室。

`_rule_anomaly` 会把这识别为：prior ≥ 4 → current ≈ 0。

## 不在 fixture 里的东西

- **没有埋点的 `emotion_pattern`** —— 怕对 `_rule_emotion` 的 same-context 阈值过拟合
- **没有埋点的 `skill_pattern`** —— PRD v0 把 skill_pattern 完全交给 LLM，
  规则层不主动推；fixture 里因此不强求其出现
- 误报的容忍带：≤ 2 个虚假信号在精确率内

## 改 fixture 时的注意

每加一条事件，请：

1. 先想这条会不会**意外触发**新的 R1/R3/R4——尤其是同一 domain 累计到 3
2. 检查是否会让某个原埋点**反触发不到**（比如把音乐窗口拉宽到 14 天外）
3. 跑一次 `pytest tests/test_backfill_e2e.py -k recall_precision`
4. 如果改了"埋点"的语义，回来更新本 README

不要为了让某个测试通过而**手术刀式微调**——那会让 fixture 失去"这是
真正的 6 个月日常记录"的代表性。
