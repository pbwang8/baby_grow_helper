# Phase 2 真云端烟雾测授权卡

> 状态：**已授权**（Cowork × 作者 2026-05-31）
> 类型：一次性运维任务，不进 Phase 3 主线，但要在切 A/B 之前完成
> 触发：`reports/phase2-baseline.md §8` 第 1 条 "Real-cloud integration smoke"

---

## 1. 授权范围（Code 只能在以下边界内执行）

- **模型**：仅 `claude-sonnet-4`（PRD §3.1 默认主模型），不允许顺手跑 Haiku、不允许跑 Opus
- **次数**：≤ **2 次** `POST /insights/generate` 真实云端调用
  - 第 1 次：用现有合成 fixture `tests/fixtures/backfill_xiaoming.jsonl`，week_start=2026-05-18
  - 第 2 次（可选）：仅在第 1 次产出明显缺陷时用同一份 fixture 复跑一次，复跑前在终端打印一行"复跑原因：xxx"
- **预算硬上限**：本次单独预算 **$2 USD**，超出立即 abort 并把已花费写进报告
  - Sonnet 4 当前价位下，单份周报输入 ~1k tokens、输出 ~1.5k tokens，成本 ~$0.025/次，远低于上限；上限是防止重试风暴
- **数据**：**只能用合成 fixture（xiaoming）**，禁止用任何含瑶瑶真实信息的数据（CLAUDE.md §5.4 红线）
- **缓存**：`cache_system=True` 必须开（PRD §3.3），降低重复调用成本

## 2. 必交产出

落到 `reports/phase2-real-snapshot.md`，至少包含：

1. 调用元数据：模型名、tokens in/out、wall-clock、实际美元花费
2. 产出 JSON 全文（脱敏：把任何意外出现的儿童名/地名替换为 `<REDACTED>`，即使 fixture 是合成的也走一遍流程）
3. 与 mock 版 baseline 的差异点：四节段标题、`change_over_time` 是否覆盖、`sources_used` 是否合规、`open_questions` 数量
4. 一句话主观评分：洞察是否"父母会想读完"——这是真云端唯一不能 mock 的信号

## 3. 验收（任一不通过，本次不算完成）

- [ ] PRD §2.1#3 四节段、§10.1 至少一个 `change_over_time`、§3.7 `sources_used ⊆ signal_ids ∪ event_ids`
- [ ] 实际花费 ≤ $0.10（远低于 $2 上限；超过说明缓存或重试有问题，要排查）
- [ ] 产出 JSON 通过 `tests/test_phase2_e2e.py` 同款 schema 校验（建议把校验抽成一个独立 helper，烟雾测脚本直接复用）
- [ ] `reports/phase2-real-snapshot.md` 提交到主干，commit message：`docs(phase2): real-cloud smoke snapshot`

## 4. 边界外的事（这次不要做）

- **不要**起 A/B Sonnet↔Haiku（那是 baseline §8 第 2 条，week 5+ 独立任务）
- **不要**改 `src/agents/insight_writer.py` 的提示词——这次只是观测当前 prompt 在真云端的表现
- **不要**给真实数据跑——哪怕作者自己提供，本次也只跑 fixture
- **不要**把 API key 写进任何 commit，使用 `BGH_ANTHROPIC_API_KEY` 环境变量，且 `.env` 必须在 `.gitignore` 内（已确认）

## 5. Open Questions（执行中遇到再回填）

- _(待 Code 在执行中补充)_

---

_作者：Cowork（2026-05-31，21:00 巡检后追加）。授权一次性，完成后这张卡片归档到 `prd/inbox/_done/` 或直接删除，不进主 prd/。_
