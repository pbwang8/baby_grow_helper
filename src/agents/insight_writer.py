"""Phase 2 M2.2 — insight_writer agent.

Why this exists (PRD prd/phase2-weekly-insight.md §2.1#2-3, §3.7, §10.1):
  Reads a CompressedContext (built by context_compressor) and asks the
  cloud writer to produce a 4-section weekly insight in Chinese, with
  open questions and traceable source ids.

What's locked (do not "improve" without a fresh ADR):
  - Backend default = "claude" (Sonnet 4 weeks first per PRD §3.1; A/B Haiku
    starts week 5). The local fallback model+chain is gated behind
    backend="local-fallback" and stays simple in v0 — Phase 2 baseline only
    requires it run end-to-end, not match Sonnet quality.
  - System prompt is loaded from src/prompts/insight_writer.md and passed
    intact to LLMClient, which wraps it in a `cache_control` block (PRD §3.3
    1h ephemeral cache).
  - sources_used **MUST** ⊆ input ids. Hard constraint per PRD §3.7:
    one retry at lower temperature; second violation → degrade with a
    canned "本周内容暂不可用" insight + raise so the API surface logs it.
  - At least one section must have axis="change_over_time" (PRD §10.1).
    Same retry-or-degrade logic.

We do NOT:
  - Persist to DB here. The API layer owns the (id, version) lifecycle.
  - Stream responses — the writer is one-shot non-streaming JSON.
  - Inline the prompt — `# i18n_locked` style files only.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agents.context_compressor import CompressedContext
from src.core.llm_client import LLMClient, LLMError, parse_json_strict

logger = logging.getLogger(__name__)

PROMPT_PATH: Final[Path] = (
    Path(__file__).parent.parent / "prompts" / "insight_writer.md"
)

ALLOWED_AXES: Final[frozenset[str]] = frozenset(
    {"highlight", "change_over_time", "next_week_focus", "open_questions"}
)
REQUIRED_AXIS: Final[str] = "change_over_time"  # PRD §10.1
REQUIRED_SECTION_COUNT: Final[int] = 4
MIN_OPEN_QUESTIONS: Final[int] = 1
MAX_OPEN_QUESTIONS: Final[int] = 3

Backend = Literal["claude", "local-fallback", "remote-local"]


class InsightWriterError(RuntimeError):
    """Raised when the writer can't produce a valid insight after retry."""


# ---- output shapes (Pydantic, frozen) -------------------------------------


class InsightSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    axis: Literal["highlight", "change_over_time", "next_week_focus", "open_questions"]
    title: str = Field(min_length=1, max_length=32)
    body: str = Field(min_length=1)
    sources_used: list[str] = Field(default_factory=list)


class WeeklyInsight(BaseModel):
    """Validated writer output. Persisted by the API layer, not here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=64)            # UUID4 hex
    child_id: str = Field(min_length=1, max_length=64)
    week_start: str = Field(min_length=10, max_length=10)   # YYYY-MM-DD
    week_end: str = Field(min_length=10, max_length=10)
    child_age_months: int = Field(ge=0, le=600)
    sections: list[InsightSection] = Field(min_length=REQUIRED_SECTION_COUNT,
                                            max_length=REQUIRED_SECTION_COUNT)
    open_questions: list[str] = Field(
        min_length=MIN_OPEN_QUESTIONS, max_length=MAX_OPEN_QUESTIONS
    )
    sources_used: list[str]
    backend: Backend
    model_used: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)


@dataclass(frozen=True)
class _DraftPayload:
    """Raw shape we expect the model to return, before WeeklyInsight wrap.

    The model emits only `sections + open_questions + sources_used`; the
    Agent layer adds id/child_id/week boundaries/token counts.
    """

    sections: list[dict[str, object]]
    open_questions: list[str]
    sources_used: list[str]


# ---- public API -----------------------------------------------------------


def write_weekly_insight(
    ctx: CompressedContext,
    *,
    backend: Backend = "claude",
    llm: LLMClient | None = None,
) -> WeeklyInsight:
    """Compose a WeeklyInsight from a CompressedContext.

    `backend` selects the route in LLMClient: "claude" → cloud Anthropic,
    "local-fallback" → local Ollama 3B chain. PRD §3.1 default is claude.
    """
    writer = InsightWriter(llm=llm)
    return writer.run(ctx, backend=backend)


# ---- agent ----------------------------------------------------------------


class InsightWriter:
    """Pipeline: render input → LLM call → validate → retry once → degrade."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or LLMClient()
        self._system = _load_prompt()

    def run(
        self, ctx: CompressedContext, *, backend: Backend = "claude"
    ) -> WeeklyInsight:
        allowed_ids = _allowed_source_ids(ctx)
        prompt = _ctx_to_prompt(ctx)

        # First attempt — temperature default for the chosen backend.
        try:
            draft, tokens_in, tokens_out, model_used = self._call_writer(
                prompt=prompt, backend=backend
            )
            self._validate_draft(draft, allowed_ids)
        except (InsightWriterError, LLMError, ValidationError) as e:
            logger.warning(
                "insight_writer first pass failed (%s); retrying once", e
            )
            try:
                draft, tokens_in, tokens_out, model_used = self._call_writer(
                    prompt=prompt + _RETRY_NUDGE, backend=backend
                )
                self._validate_draft(draft, allowed_ids)
            except (InsightWriterError, LLMError, ValidationError) as e2:
                logger.error(
                    "insight_writer retry also failed (%s); degrading", e2
                )
                return _degrade(ctx, backend=backend, reason=str(e2))

        return _assemble(
            ctx=ctx,
            draft=draft,
            backend=backend,
            model_used=model_used,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    # --- internals ----------------------------------------------------------

    def _call_writer(
        self, *, prompt: str, backend: Backend
    ) -> tuple[_DraftPayload, int, int, str]:
        """Invoke LLMClient and parse the JSON shell. Schema validation is
        the caller's job (so we can produce a more useful retry message)."""
        llm_backend: Literal["local", "cloud"]
        if backend == "local-fallback":
            llm_backend = "local"
        elif backend == "claude":
            llm_backend = "cloud"
        else:  # remote-local placeholder — treat as cloud route, future ADR-0003
            llm_backend = "cloud"

        try:
            result = self._llm.generate(
                prompt=prompt,
                system=self._system,
                backend=llm_backend,
                purpose="insight",
                json_mode=(llm_backend == "local"),
                cache_system=True,
            )
        except LLMError as e:
            raise InsightWriterError(f"writer LLM call failed: {e}") from e

        try:
            payload = parse_json_strict(result.text)
        except LLMError as e:
            raise InsightWriterError(f"writer returned non-JSON: {e}") from e

        sections = payload.get("sections")
        open_questions = payload.get("open_questions")
        sources_used = payload.get("sources_used")
        if not isinstance(sections, list):
            raise InsightWriterError(
                f"writer.sections must be list, got {type(sections).__name__}"
            )
        if not isinstance(open_questions, list):
            raise InsightWriterError(
                f"writer.open_questions must be list, got {type(open_questions).__name__}"
            )
        if not isinstance(sources_used, list):
            raise InsightWriterError(
                f"writer.sources_used must be list, got {type(sources_used).__name__}"
            )
        # We don't trust types inside the lists yet — validation step does that.
        return (
            _DraftPayload(
                sections=[s for s in sections if isinstance(s, dict)],
                open_questions=[str(q) for q in open_questions],
                sources_used=[str(s) for s in sources_used],
            ),
            result.tokens_in,
            result.tokens_out,
            result.model_used,
        )

    def _validate_draft(
        self, draft: _DraftPayload, allowed_ids: set[str]
    ) -> None:
        """Enforce PRD's hard constraints. Raises InsightWriterError on miss."""
        # 1) section count
        if len(draft.sections) != REQUIRED_SECTION_COUNT:
            raise InsightWriterError(
                f"sections must be {REQUIRED_SECTION_COUNT}, got {len(draft.sections)}"
            )
        # 2) every axis is allowed
        axes = []
        for s in draft.sections:
            ax = s.get("axis")
            if not isinstance(ax, str) or ax not in ALLOWED_AXES:
                raise InsightWriterError(f"section.axis invalid: {ax!r}")
            axes.append(ax)
        # 3) at least one change_over_time (PRD §10.1)
        if REQUIRED_AXIS not in axes:
            raise InsightWriterError(
                f"PRD §10.1: at least one section must have axis={REQUIRED_AXIS!r}"
            )
        # 4) open_questions count
        if not (MIN_OPEN_QUESTIONS <= len(draft.open_questions) <= MAX_OPEN_QUESTIONS):
            raise InsightWriterError(
                f"open_questions count out of range: {len(draft.open_questions)}"
            )
        # 5) sources_used ⊆ allowed_ids (PRD §3.7 hard constraint)
        unknown = [sid for sid in draft.sources_used if sid not in allowed_ids]
        if unknown:
            raise InsightWriterError(
                f"PRD §3.7: sources_used contains ids not in input: {unknown[:5]!r}"
            )
        # 6) per-section sources_used must also be ⊆ allowed
        for i, s in enumerate(draft.sections):
            sec_srcs = s.get("sources_used", [])
            if not isinstance(sec_srcs, list):
                raise InsightWriterError(
                    f"sections[{i}].sources_used must be list"
                )
            sec_unknown = [
                str(sid) for sid in sec_srcs if str(sid) not in allowed_ids
            ]
            if sec_unknown:
                raise InsightWriterError(
                    f"sections[{i}].sources_used has unknown ids: {sec_unknown[:5]!r}"
                )


# ---- helpers --------------------------------------------------------------


_RETRY_NUDGE: Final[str] = (
    "\n\n# 注意（重试提示）\n"
    "上一次输出未通过校验。请严格遵守：\n"
    "- 恰好 4 个 sections；\n"
    "- 至少 1 个 section 的 axis = 'change_over_time'；\n"
    "- sources_used 里的所有 id 必须出现在我提供的 signals 或 event_highlights 中。\n"
)


def _load_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise InsightWriterError(f"prompt missing at {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _allowed_source_ids(ctx: CompressedContext) -> set[str]:
    ids: set[str] = set()
    ids.update(s.signal_id for s in ctx.signals)
    ids.update(h.event_id for h in ctx.event_highlights)
    return ids


def _ctx_to_prompt(ctx: CompressedContext) -> str:
    """Render the CompressedContext as the user-side JSON for the writer.

    We deliberately strip nothing — context_compressor already did its job.
    """
    payload = {
        "child_id": ctx.child_id,
        "week_start": ctx.week_start.isoformat(),
        "week_end": ctx.week_end.isoformat(),
        "child_age_months": ctx.child_age_months,
        "signals": [
            {"signal_id": s.signal_id, "one_liner": s.one_liner}
            for s in ctx.signals
        ],
        "event_highlights": [
            {
                "event_id": h.event_id,
                "timestamp": h.timestamp,
                "summary": h.summary,
                "type": h.type,
                "domains": h.domains,
                "reason": h.reason,
            }
            for h in ctx.event_highlights
        ],
        "period_deltas": [
            {
                "domain": d.domain,
                "delta": d.delta,
                "current_event_count": d.current_event_count,
                "prior_event_count": d.prior_event_count,
            }
            for d in ctx.period_deltas
        ],
        "raw_token_count": ctx.raw_token_count,
    }
    return json.dumps(payload, ensure_ascii=False)


def _assemble(
    *,
    ctx: CompressedContext,
    draft: _DraftPayload,
    backend: Backend,
    model_used: str,
    tokens_in: int,
    tokens_out: int,
) -> WeeklyInsight:
    sections: list[InsightSection] = []
    for s in draft.sections:
        raw_srcs = s.get("sources_used") or []
        srcs = list(raw_srcs) if isinstance(raw_srcs, list) else []
        sections.append(
            InsightSection(
                axis=s["axis"],  # type: ignore[arg-type]
                title=str(s["title"]),
                body=str(s["body"]),
                sources_used=[str(x) for x in srcs],
            )
        )
    return WeeklyInsight(
        id=uuid.uuid4().hex,
        child_id=ctx.child_id,
        week_start=ctx.week_start.isoformat(),
        week_end=ctx.week_end.isoformat(),
        child_age_months=ctx.child_age_months,
        sections=sections,
        open_questions=draft.open_questions,
        sources_used=draft.sources_used,
        backend=backend,
        model_used=model_used,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def _degrade(
    ctx: CompressedContext, *, backend: Backend, reason: str
) -> WeeklyInsight:
    """PRD §3.7 fallback: emit a benign placeholder so the UI doesn't crash.

    The four sections include `change_over_time` so we still satisfy schema.
    All text is honest about the failure; no fake observations.
    """
    body = (
        "本周内容暂不可用——洞察生成两次都未通过校验，"
        "请检查事件源或稍后重试。"
    )
    placeholder = [
        InsightSection(
            axis="highlight",
            title="洞察暂不可用",
            body=body,
            sources_used=[],
        ),
        InsightSection(
            axis="change_over_time",
            title="变化追踪暂未生成",
            body=body,
            sources_used=[],
        ),
        InsightSection(
            axis="next_week_focus",
            title="下周关注暂未生成",
            body=body,
            sources_used=[],
        ),
        InsightSection(
            axis="open_questions",
            title="开放问题暂未生成",
            body=body,
            sources_used=[],
        ),
    ]
    return WeeklyInsight(
        id=uuid.uuid4().hex,
        child_id=ctx.child_id,
        week_start=ctx.week_start.isoformat(),
        week_end=ctx.week_end.isoformat(),
        child_age_months=ctx.child_age_months,
        sections=placeholder,
        open_questions=[f"洞察生成失败：{reason[:80]}。是否要重新生成？"],
        sources_used=[],
        backend=backend,
        model_used="degraded",
        tokens_in=0,
        tokens_out=0,
    )
