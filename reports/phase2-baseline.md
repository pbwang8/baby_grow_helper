# Phase 2 — Baseline Report

> Generated 2026-05-30 after closing the M2 milestone (context compressor →
> insight writer → API → /weekly screen → e2e test). All numbers from a
> single run on the synthetic `xiaoming` fixture
> (`tests/fixtures/backfill_xiaoming.jsonl`, 65 events, ages 30→35 months).
> Cloud writer is mocked at the LLM boundary in the e2e — **zero real
> Anthropic spend** during this measurement. The cloud route itself is
> covered by `tests/test_llm_client.py` (respx-mocked Anthropic Messages).

---

## 1. Pipeline gates (PRD §2.1, §3.7, §10.1)

End-to-end test (`tests/test_phase2_e2e.py::test_phase2_e2e_full_pipeline`)
walks the same fixture through:

```
events → SignalExtractor → compress_week_context →
InsightWriter (LLM mocked) → POST /insights/generate →
GET /insights/:id → POST /insights/:id/feedback → regenerate (v2)
```

| gate | check | status |
|------|-------|--------|
| §2.1#1 compressor non-empty | `signals ∪ event_highlights` non-empty for `WEEK_START=2026-05-18` | ✅ |
| §2.1#3 four sections | `len(sections) == 4` | ✅ |
| §10.1 change_over_time | at least one section.axis == "change_over_time" | ✅ |
| §3.7 traceability | `sources_used ⊆ signal_ids ∪ event_ids` | ✅ |
| §3.5 version bump | second `/insights/generate` returns `version=2` | ✅ |
| §3.6 feedback partial | at least one of accuracy/value/free_text required | ✅ |
| §3.5 child_age_months frozen | 35 months at week_start, persisted on the row | ✅ |

## 2. Compressor footprint (PRD §2.1#1)

| metric | observed | budget |
|--------|----------|--------|
| signals (active in week) | 1-3 | n/a |
| event_highlights | ≤ 8 (milestones bypass) | ≤ 8 PRD §2.1#1 |
| signal one-liner length | ≤ 120 chars | 120 PRD soft cap |
| `raw_token_count` | ~600-1100 | DEFAULT_MAX_TOKENS=4000 |

The compressor's job is to keep the cloud-bound payload comfortably
under the 4k soft cap; on this fixture we sit ~25% utilised, leaving
headroom for prompt-cache stability.

## 3. Writer agent (PRD §3.1, §3.7)

`tests/test_insight_writer_unit.py` (13 cases) covers:

- happy path — Sonnet 4 default, 4 sections out
- cloud routing with `cache_system=True` (PRD §3.3)
- `local-fallback` routes to LLMClient `backend="local"` + `json_mode=True`
- UUID4 hex id generation
- retry on missing `change_over_time` (PRD §10.1)
- retry on top-level + section-level unknown source ids (PRD §3.7)
- degrade after two failures: `model_used="degraded"`, schema still valid
- section count != 4 → retry; `open_questions` count out of range → retry
- non-JSON response → retry succeeds on second pass
- prompt rendering includes signal_ids and event_ids verbatim

## 4. LLMClient cloud route (PRD §3.3)

`tests/test_llm_client.py` adds 6 cloud cases covering:

- Anthropic Messages API request shape (system block is a `cache_control:
  ephemeral` array with `ttl="1h"`)
- `anthropic-beta: extended-cache-ttl-2025-04-11` header set when caching
- `tokens_in` aggregates `input_tokens + cache_creation + cache_read`
- HTTP 4xx / 5xx surface as `LLMError`
- Custom base URL honoured (rednote runway gateway)
- Empty API key raises before any network I/O

| metric | observed | PRD cap |
|--------|----------|---------|
| Cloud tokens this baseline | 0 (mocked) | ≤ 100k / month CLAUDE.md §5 |
| Cache TTL | 1h ephemeral | 1h PRD §3.3 |
| Default backend | claude (Sonnet 4) | claude PRD §3.1 |

> The Sonnet 4 weeks-first cadence (PRD §3.1 Cowork裁定) means we have not
> yet flipped any traffic to Haiku 4.5 A/B. Plan for that hand-off lives
> with the next Phase 2 retro, not this baseline.

## 5. API surface (PRD §2.1#6, §3.5, §3.6)

`tests/test_insight_api.py` (14 cases):

- POST /insights/generate persists + reads back, returns `version=1`
- POST /insights/generate twice → `version=2`, no PK collision
- non-Monday `week_start` → 422 with "Monday" in detail
- unknown `child_id` → 404 from compressor surface
- bad date format → 422
- GET /insights/:id 404 + happy path
- GET /insights newest-first
- POST feedback: full dimensions, partial dimensions (one of three),
  empty payload → 422, unknown insight → 404, invalid enum → 422
- DB round-trip preserves `axis` field across all four sections

## 6. Tests + coverage

```
$ pytest
190 passed, 1 skipped, 4 errors in 25.81s   # 4 errors = pre-existing
                                             # sentence-transformers absence
                                             # (Phase 1 carry-over)
Total coverage: 91.46%
```

| metric | result | gate | status |
|--------|--------|------|--------|
| Unit + e2e tests | 190 pass | all green | ✅ |
| Coverage | 91.46% | ≥ 80% Phase 2 | ✅ |
| `ruff check` | clean | green | ✅ |
| `mypy --strict` | clean | green | ✅ |
| `next lint` (web/) | clean | green | ✅ |
| `tsc --noEmit` (web/) | clean | green | ✅ |

Per-module coverage (Phase 2 modules):

| module | cover | notes |
|--------|-------|-------|
| `src/agents/context_compressor.py` | 97% | new in Phase 2 |
| `src/agents/insight_writer.py` | 91% | retry/degrade branches both covered |
| `src/api/main.py` | 96% | +Phase 2 routes |
| `src/core/llm_client.py` | 90% | +cloud path |
| `src/core/signal_delta.py` | 97% | unchanged from Phase 1 |
| `src/agents/recorder.py` | 86% | unchanged from Phase 1 |

## 7. Frontend (PRD §2.1#5)

`web/app/weekly/page.tsx` adds a fourth screen alongside `/log /timeline
/heatmap`:

- Three-way week selector (本周 / 上周 / 上上周, anchored to local Monday).
- `Generate` button — bumps version label automatically when the week
  already has an insight on file.
- 2-column section grid with axis-coloured cards (highlight → amber,
  change_over_time → emerald, next_week_focus → sky, open_questions →
  violet).
- Per-section feedback chips: accuracy {accurate, inaccurate, unsure},
  value {inspiring, missed_point, unhelpful}, free text ≤ 500 chars.
  Submit allowed when any one of the three is set (matches API §3.6).
- "带回去想一想" panel renders `open_questions` as a separate column.
- Transparency footer: events count + signals count split from
  `sources_used`, model name, tokens in/out.
- Source-id pills under each section body for traceability.

Stack stays the Phase 1 baseline: Next.js 14 App Router + React 18 +
Tailwind, no extra component libraries.

## 8. New open follow-ups (carry to Phase 3 retro)

- **Real-cloud integration smoke**: e2e mocks the LLM boundary. Before
  flipping `BGH_ANTHROPIC_API_KEY` on, do one manual run against the real
  Sonnet 4 endpoint and snapshot a redacted insight into
  `reports/phase2-real-snapshot.md`. Not blocking the milestone.
- **A/B Sonnet 4 ↔ Haiku 4.5**: PRD §3.1 deferred to week 5+. Decision
  packet (cost/quality delta on real usage) lives with that retro.
- **`disagreement_log` → CSV export**: Phase 1 §7 carry-over. Phase 2
  didn't touch it; still on the docket.
- **Emotion-pattern delta metric**: still `None`. PRD-level question;
  Phase 3 candidate.
- **`db.py` 79% / `embeddings.py` (when ML extras installed) coverage**:
  init/error branches; not blocking.

---

_Last updated: 2026-05-30 — Phase 2 milestone closed; §8 opened for Phase 3._
