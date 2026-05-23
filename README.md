# BabyGrowHelper

> 一个能记得住孩子童年、陪伴几十年、帮父母看懂孩子的 AI 育儿伙伴。

这不是一个相册，不是一个日记本，也不是一个育儿百科。
这是一个**有持久记忆、会主动观察、能给出个性化判断**的长期家庭观察员。

第一个用户：**瑶瑶**（2.5 岁）和她的爸爸（项目作者）。

---

## 我是谁？该读什么？

### 你是项目作者本人
按这个顺序回顾整体：
1. [VISION.md](./VISION.md) — 使命与价值观（项目宪法）
2. [DIFFERENTIATION.md](./DIFFERENTIATION.md) — 差异化与护城河
3. [ROADMAP.md](./ROADMAP.md) — 8 周 MVP 路线图
4. [ARCHITECTURE.md](./ARCHITECTURE.md) — 技术架构 + 多Agent + 成本模型
5. [FEEDBACK_LOOP.md](./FEEDBACK_LOOP.md) — 反馈到迭代的闭环
6. [COLLABORATION.md](./COLLABORATION.md) — Cowork × Code 协同方式

### 你是 Cowork 模式下的 Claude（产品 / 战略层）
你已经在读它了。新会话进来先确认 [memory/](#) 是否还和这里一致；不一致以这里为准并更新 memory。

### 你是 Claude Code（实现层）
**第一份要读的是 [CLAUDE.md](./CLAUDE.md)**，不是这个 README。CLAUDE.md 里有约束清单和必读顺序。

### 你是未来某天的"我"或新加入的协作者
按"项目作者本人"那个顺序读。中间任何一份疑问 → 看 [decisions/](./decisions/) 里有没有相关 ADR。

---

## 一分钟产品概念

```
家长每天和 AI 简单对话 (语音 / 文字)
   ↓ 本地小模型实时结构化
事件库（每个孩子一份持久档案）
   ↓ 信号提取 Agent (本地, 每日)
信号（兴趣 / 情绪 / 技能 / 异常）
   ↓ 洞察 Agent (云端, 每周)
周报：本周亮点 + 趋势观察 + 一个开放问题
   ↓ 培养建议 Agent (云端, 按需)
基于孩子画像 + 经典育儿理论 + 文化适配的多方向建议
   ↓ 反馈循环
小改自动 / 大改人工 → 持续个性化
```

15 年后，瑶瑶能从这里看到自己的童年；瑶瑶将来当父母时，能从这里得到启发。

---

## 当前状态

- **阶段**：战略文档定型中（Phase 0 之前）
- **下一步**：用户审阅这套文档 → 进入 Phase 0（架构基线，详见 ROADMAP.md）
- **首位用户**：项目作者本人
- **使用与合规声明**：当前阶段为**私人项目，仅作者自用，未商业化，无第三方数据共享**。
  v1 商业化 / 引入外部用户 / 采购服务器存储真实数据之前，必须先完成 PIPL + 未保条例 +
  数据出境影响评估（届时新增 `compliance/` 目录）。

---

## 项目结构

```
baby_grow_helper/
├── README.md                  ← 你正在读
├── CLAUDE.md                  ← Claude Code 进项目第一份要读的
├── VISION.md                  ← 使命与价值观
├── DIFFERENTIATION.md         ← 差异化分析
├── ARCHITECTURE.md            ← 技术架构 + 多Agent + 成本
├── ROADMAP.md                 ← MVP 路线图
├── COLLABORATION.md           ← Cowork × Code 协同
├── FEEDBACK_LOOP.md           ← 反馈→迭代闭环
├── prd/                       ← 需求卡（待 Phase 0 建立）
├── decisions/                 ← ADR 决策记录
├── src/                       ← 代码（待 Phase 0）
└── tests/                     ← 测试（待 Phase 0）
```

---

## 三个北极星问题（来自 VISION）

每次产品决策都要回答：

1. 这个功能是让 AI 更懂孩子，还是更不懂？
2. 这个功能积累的是一次性数据，还是跨年复利的资产？
3. 15 年后孩子自己看见这部分内容，会感谢我们，还是会尴尬 / 不安？

---

_最后更新：2026-05-15_
