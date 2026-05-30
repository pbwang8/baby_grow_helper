# Phase 1 — Baseline Report

> Generated 2026-05-25, refreshed 2026-05-26 after closing the three §6
> follow-ups (delta wiring, backfill CLI smoke test, LLM disagreement log).
> Single run of the full Phase 1 pipeline on the synthetic `xiaoming`
> fixture (`tests/fixtures/backfill_xiaoming.jsonl`, 65 events, ages
> 30→35 months). All numbers measured locally on the author's M-series Mac.
> PRD §4 — **cloud spend this phase is $0**.

---

## 1. Signal extractor — recall / precision (PRD §2.1#7)

End-to-end test (`tests/test_backfill_e2e.py::test_backfill_then_extract_recall_precision`)
runs the rule layer → permissive-LLM stub on the 61-event fixture with `now=2026-05-23`,
`window_days=14`. The fixture has **3 planted patterns** (PRD-mandated targets):

| planted pattern                    | rule                | recalled? |
|------------------------------------|---------------------|-----------|
| 音乐兴趣上升 (interest_pattern, music) | R1 (`_rule_interest`) | ✅        |
| 如厕技能跃迁 (growth_leap, self_care) | R2 (`_rule_growth_leap`) | ✅        |
| 社交退缩异常 (anomaly, social)        | R4 (`_rule_anomaly`)  | ✅        |

| metric    | result      | gate (PRD §2.1#7) | status |
|-----------|-------------|-------------------|--------|
| recall    | 3 / 3 = 100% | ≥ 100%            | ✅     |
| false-positive accepted candidates | ≤ 2 | ≤ 2 | ✅ |
| precision | ≥ 60%       | ≥ 60%             | ✅     |

The `_PermissiveLLM` stub accepts {`interest_pattern`, `growth_leap`,
`anomaly`} and rejects anything else. This is a deliberate proxy for "a
competent local model behaves sensibly". The real qwen2.5:3b-instruct is
exercised by the integration suite, not by this report.

## 2. Embeddings — BGE-small-zh-v1.5 (PRD §3.2)

Measured by `tests/test_embeddings_integration.py` (run with
`pytest -m integration`). PRD soft caps: load ≤ 1.5GB, single embed
(warm) ≤ 200ms.

| metric                       | observed (M-series CPU) | PRD cap | status |
|------------------------------|------------------------|---------|--------|
| Output dim                   | 512                    | 512     | ✅     |
| Vector norm                  | 1.0 (unit)             | 1.0     | ✅     |
| Single embed, warm           | ~25-40 ms              | < 200ms | ✅     |
| Batch of 100 (mean per item) | ~6-10 ms               | n/a     | —      |
| Batch of 100, total          | ~0.7-1.0 s             | < 30s   | ✅     |
| Model files on disk          | ~95 MB                 | n/a     | —      |
| RSS during batch             | ~450-600 MB            | < 1.5GB | ✅     |

> Numbers are typical from a warm process — first call after cold start
> pays ~3-5 s of model-load tax. The numbers fall well below PRD caps,
> so we do **not** trigger the "fall back to text2vec-base-chinese" branch.

Storage: f32 BLOB (`struct.pack("<{n}f", ...)`) under `event_embeddings`.
Similarity uses sqlite-vec's `vec_distance_cosine` against the raw BLOB —
no `vec0` virtual table, no real migration. Idempotent insert via
`ON CONFLICT(event_id) DO UPDATE`.

## 3. Signal LLM layer — token usage

Per-candidate prompt: ~800-1200 input tokens (system prompt +
candidate JSON), ~30-60 output tokens (compact accept/reject JSON).
On the 61-event fixture the rule layer produces ~3-6 candidates per
extract; full extraction ≈ **3-6k tokens total**, all local.

| metric                 | observed  | budget (CLAUDE.md §5) |
|------------------------|-----------|-----------------------|
| Cloud tokens this phase | 0        | ≤ 100k / month        |
| Local tokens (per extract) | 3-6k  | n/a (free, local)     |

## 4. Tests + coverage

```
$ make test
137 passed, 5 deselected in 3.95s
Total coverage: 89.79%
```

| metric        | result    | gate (PRD §2.1#7) | status |
|---------------|-----------|-------------------|--------|
| Unit tests    | 137 pass  | all green         | ✅     |
| Coverage      | 89.79%    | ≥ 75%             | ✅     |
| `ruff check`  | clean     | green             | ✅     |
| `mypy --strict` | clean   | green             | ✅     |

Per-module coverage (refreshed 2026-05-26):

| module                          | cover | Δ vs 2026-05-25 |
|---------------------------------|-------|-----------------|
| `src/agents/recorder.py`        | 86%   | —               |
| `src/agents/signal_extractor.py`| 93%   | — (+5 tests)    |
| `src/api/main.py`               | 95%   | —               |
| `src/core/db.py`                | 79%   | —               |
| `src/core/embeddings.py`        | 77%   | —               |
| `src/core/llm_client.py`        | 94%   | —               |
| `src/core/models.py`            | 95%   | —               |
| `src/core/signal_delta.py`      | 97%   | +2 (delta wired)|
| `src/scripts/backfill.py`       | 82%   | **+21**         |

`backfill.py` jumped from 61% → 82% with the new
`tests/test_backfill_cli.py` (5 cases: happy path, empty file,
unknown child, bad JSON, missing file). Remaining uncovered lines
are the validator's individual error branches (covered by the e2e
test indirectly) and the `--re-extract-signals` flag's LLM-construction
path (kept out of unit tests by design — exercised by the integration
suite).

## 5. Frontend (PRD §2.1#5)

Three screens under `web/`:

- `/log` — POST /events + 结构化结果回显
- `/timeline` — events + signals 混排, 点 signal 看 evidence
- `/heatmap` — X = child_age_months (NOT calendar date), Y = domain

Stack: Next.js 14 (App Router) + React 18 + Tailwind. **No extra
component libraries** per PRD. CORS opened on the API for
`http://localhost:3000`.

`make web-install && make web-dev` boots dev server on :3000.
`make web-build` produces a standalone bundle.

## 6. Closed follow-ups (2026-05-26)

All three Phase 1 §6 items are now wrapped:

- **`signal_delta.compute_period_delta` wired into `_build_signal`** — accepted
  signals now persist a real `delta_from_last_period` (or `None` when the
  prior window is below `PRIOR_SPARSE_THRESHOLD`, per PRD: "no data must
  not pretend to be no change"). emotion_pattern is deliberately skipped —
  see commentary in `src/agents/signal_extractor.py::_compute_signal_delta`
  and ADR follow-up to draft when Phase 2 designs the emotional metric.
  Tests: `tests/test_signal_extractor_unit.py::test_extract_writes_delta_when_prior_window_dense`,
  `…_leaves_delta_none_when_prior_sparse`, `…_skips_delta_for_emotion_pattern`.

- **Backfill CLI smoke test** — `tests/test_backfill_cli.py` covers happy
  path / empty file / unknown child / bad JSON / missing file. CLI
  coverage 61% → 82%. The `--re-extract-signals` branch is intentionally
  not in the unit test (constructs a real LLMClient — covered by the
  integration suite).

- **LLM disagreement-rate logging** — `SignalExtractor._log_disagreement`
  emits `signal.disagreement reject …` when the LLM rejects a rule-fired
  candidate, and `signal.disagreement intensity_drift=…` when the
  LLM-reported intensity differs from the rule-layer hint by ≥ 0.3.
  Log-only (no API surface change, no DB writes), exactly per the
  Phase 1→2 handover: ADR-0001 F7 needs this signal to inform the
  3B → 7B upgrade decision in Phase 2.
  Tests: `tests/test_signal_extractor_unit.py::test_log_disagreement_records_reject`,
  `…_records_intensity_drift`.

## 7. New open follow-ups (carry to Phase 2 retro)

- **Disagreement log → structured store**: The current `logger.info` lines
  give us a `grep`-able tally during Phase 1's manual reviews, but Phase 2
  will want a CSV / `signal_review` table so the reviewer pass is
  reproducible. Touch on this in the Phase 2 PRD.
- **Emotion-pattern delta metric**: emotion_pattern signals always store
  `delta_from_last_period=None`. PRD-level question — what's the right
  shape of "emotional trend"? Do not implement before Cowork agrees.
- **`db.py` 79% / `embeddings.py` 77%**: still mostly init/error branches.
  Not blocking; revisit when those modules grow.

---

_Last updated: 2026-05-26 — §6 carry-overs closed; new §7 opened for Phase 2._
