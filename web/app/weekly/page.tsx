"use client";

// PRD §2.1#5 / §3.6 — /weekly: 父母按需触发的周报视图。
// - 顶部三选一选周（本周 / 上周 / 上上周，按本地周一为锚）
// - 中部：四个 section 卡片 + 段级反馈（accuracy + value + 自由文本）
// - 底部：open_questions + 透明度脚注（事件/信号数 / 模型 / tokens）

import { useEffect, useMemo, useState } from "react";
import {
  DEFAULT_CHILD_ID,
  generateInsight,
  listInsights,
  postFeedback,
  type FeedbackAccuracy,
  type FeedbackValue,
  type InsightAxis,
  type InsightSectionOut,
  type WeeklyInsightOut,
} from "@/lib/api";

// ---- date helpers --------------------------------------------------------

function mondayOf(d: Date): Date {
  // ISO Monday: Monday=1 .. Sunday=7. We use local time on purpose —
  // PRD §3.5: weeks are local-time anchored.
  const day = d.getDay(); // 0=Sun..6=Sat
  const diff = (day === 0 ? -6 : 1 - day);
  const m = new Date(d);
  m.setHours(0, 0, 0, 0);
  m.setDate(m.getDate() + diff);
  return m;
}

function fmtISO(d: Date): string {
  // YYYY-MM-DD in local time
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

function fmtZh(d: Date): string {
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

function shiftWeeks(d: Date, weeks: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + weeks * 7);
  return out;
}

// ---- axis labels ---------------------------------------------------------

const AXIS_LABEL: Record<InsightAxis, string> = {
  highlight: "本周高光",
  change_over_time: "成长变化",
  next_week_focus: "下周关注",
  open_questions: "开放问题",
};

const AXIS_BG: Record<InsightAxis, string> = {
  highlight: "bg-amber-50 border-amber-200",
  change_over_time: "bg-emerald-50 border-emerald-200",
  next_week_focus: "bg-sky-50 border-sky-200",
  open_questions: "bg-violet-50 border-violet-200",
};

const AXIS_PILL: Record<InsightAxis, string> = {
  highlight: "bg-amber-100 text-amber-900",
  change_over_time: "bg-emerald-100 text-emerald-900",
  next_week_focus: "bg-sky-100 text-sky-900",
  open_questions: "bg-violet-100 text-violet-900",
};

// ---- main page -----------------------------------------------------------

type WeekChoice = { offset: 0 | -1 | -2; label: string; weekStart: string };

export default function WeeklyPage() {
  const choices = useMemo<WeekChoice[]>(() => {
    const thisMonday = mondayOf(new Date());
    return [
      { offset: 0, label: "本周", weekStart: fmtISO(thisMonday) },
      {
        offset: -1,
        label: "上周",
        weekStart: fmtISO(shiftWeeks(thisMonday, -1)),
      },
      {
        offset: -2,
        label: "上上周",
        weekStart: fmtISO(shiftWeeks(thisMonday, -2)),
      },
    ];
  }, []);
  const [selectedOffset, setSelectedOffset] = useState<0 | -1 | -2>(-1);
  const selected = choices.find((c) => c.offset === selectedOffset)!;

  const [insights, setInsights] = useState<WeeklyInsightOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const rows = await listInsights({ child_id: DEFAULT_CHILD_ID, limit: 12 });
      setInsights(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  // Pick the latest insight for the selected week (highest version).
  const current = useMemo<WeeklyInsightOut | null>(() => {
    const same = insights.filter((i) => i.week_start === selected.weekStart);
    if (same.length === 0) return null;
    return same.reduce((a, b) => (a.version >= b.version ? a : b));
  }, [insights, selected.weekStart]);

  async function onGenerate() {
    setGenerating(true);
    setError(null);
    try {
      await generateInsight({
        child_id: DEFAULT_CHILD_ID,
        week_start: selected.weekStart,
        backend: "claude",
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">周报</h1>
          <p className="mt-1 text-sm text-stone-500">
            按周生成的洞察。父母触发，不下评判，留追问。
          </p>
        </div>
        <button
          onClick={onGenerate}
          disabled={generating}
          className="rounded-md border border-stone-300 bg-white px-3 py-1.5 text-sm
                     transition hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {generating
            ? "生成中…"
            : current
              ? `重新生成（v${current.version + 1}）`
              : "生成周报"}
        </button>
      </div>

      {/* week selector */}
      <div className="flex gap-2">
        {choices.map((c) => {
          const active = c.offset === selectedOffset;
          const start = new Date(c.weekStart);
          const end = shiftWeeks(start, 1);
          end.setDate(end.getDate() - 1);
          return (
            <button
              key={c.offset}
              onClick={() => setSelectedOffset(c.offset)}
              className={`rounded-md border px-3 py-1.5 text-sm transition ${
                active
                  ? "border-stone-900 bg-stone-900 text-white"
                  : "border-stone-300 bg-white hover:bg-stone-100"
              }`}
            >
              {c.label}
              <span className="ml-2 text-xs opacity-70">
                {fmtZh(start)}–{fmtZh(end)}
              </span>
            </button>
          );
        })}
      </div>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading && <div className="text-sm text-stone-500">加载中…</div>}

      {!loading && !current && (
        <div className="rounded-md border border-dashed border-stone-300 p-8 text-center text-sm text-stone-500">
          这一周还没有洞察。点右上角「生成周报」试试。
        </div>
      )}

      {current && <InsightView insight={current} />}
    </div>
  );
}

// ---- insight view --------------------------------------------------------

function InsightView({ insight }: { insight: WeeklyInsightOut }) {
  const sigCount = insight.sources_used.filter((s) =>
    s.startsWith("sig_"),
  ).length;
  const evtCount = insight.sources_used.length - sigCount;

  return (
    <div className="space-y-4">
      <div className="text-xs text-stone-500">
        v{insight.version} · 月龄 {insight.child_age_months} ·
        生成于 {insight.created_at.slice(0, 16).replace("T", " ")}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {insight.sections.map((s, idx) => (
          <SectionCard
            key={`${insight.id}-${idx}`}
            insightId={insight.id}
            section={s}
            sectionIdx={idx}
          />
        ))}
      </div>

      {insight.open_questions.length > 0 && (
        <div className="rounded-md border border-stone-200 bg-stone-50 p-4">
          <h3 className="text-sm font-semibold text-stone-700">
            带回去想一想
          </h3>
          <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-stone-700">
            {insight.open_questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-stone-500">
        本份周报基于 {evtCount} 条事件、{sigCount} 个信号生成；
        模型 = {insight.model_used}；
        tokens in/out = {insight.tokens_in}/{insight.tokens_out}。
      </p>
    </div>
  );
}

// ---- section card with feedback -----------------------------------------

const ACCURACY_OPTIONS: { value: FeedbackAccuracy; label: string }[] = [
  { value: "accurate", label: "准确" },
  { value: "inaccurate", label: "不准" },
  { value: "unsure", label: "不确定" },
];

const VALUE_OPTIONS: { value: FeedbackValue; label: string }[] = [
  { value: "inspiring", label: "有启发" },
  { value: "missed_point", label: "没说到点" },
  { value: "unhelpful", label: "无感" },
];

function SectionCard({
  insightId,
  section,
  sectionIdx,
}: {
  insightId: string;
  section: InsightSectionOut;
  sectionIdx: number;
}) {
  const [accuracy, setAccuracy] = useState<FeedbackAccuracy | null>(null);
  const [value, setValue] = useState<FeedbackValue | null>(null);
  const [freeText, setFreeText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submittedAt, setSubmittedAt] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const canSubmit = (accuracy || value || freeText.trim()) && !submitting;

  async function onSubmit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setErr(null);
    try {
      await postFeedback({
        insight_id: insightId,
        section_idx: sectionIdx,
        accuracy,
        value,
        free_text: freeText.trim() || null,
      });
      setSubmittedAt(new Date().toLocaleTimeString());
      setFreeText("");
      setAccuracy(null);
      setValue(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <article
      className={`rounded-md border p-4 ${AXIS_BG[section.axis]}`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-stone-900">
          {section.title}
        </h2>
        <span
          className={`rounded px-2 py-0.5 text-xs ${AXIS_PILL[section.axis]}`}
        >
          {AXIS_LABEL[section.axis]}
        </span>
      </div>
      <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-stone-800">
        {section.body}
      </p>
      {section.sources_used.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {section.sources_used.map((sid) => (
            <span
              key={sid}
              className="rounded bg-white/70 px-2 py-0.5 font-mono text-[10px] text-stone-600"
            >
              {sid}
            </span>
          ))}
        </div>
      )}

      {/* feedback */}
      <div className="mt-4 border-t border-stone-200/70 pt-3">
        <div className="flex flex-wrap gap-1">
          {ACCURACY_OPTIONS.map((o) => (
            <button
              key={o.value}
              onClick={() =>
                setAccuracy(accuracy === o.value ? null : o.value)
              }
              className={`rounded px-2 py-0.5 text-xs transition ${
                accuracy === o.value
                  ? "bg-stone-900 text-white"
                  : "bg-white text-stone-700 hover:bg-stone-100"
              }`}
            >
              {o.label}
            </button>
          ))}
          <span className="mx-1 text-xs text-stone-400">·</span>
          {VALUE_OPTIONS.map((o) => (
            <button
              key={o.value}
              onClick={() => setValue(value === o.value ? null : o.value)}
              className={`rounded px-2 py-0.5 text-xs transition ${
                value === o.value
                  ? "bg-stone-900 text-white"
                  : "bg-white text-stone-700 hover:bg-stone-100"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
        <textarea
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
          placeholder="想多说几句…（可选）"
          rows={2}
          className="mt-2 w-full rounded border border-stone-300 bg-white px-2 py-1 text-xs"
          maxLength={500}
        />
        <div className="mt-2 flex items-center justify-between">
          <button
            onClick={onSubmit}
            disabled={!canSubmit}
            className="rounded border border-stone-300 bg-white px-2 py-1 text-xs
                       transition hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "提交中…" : "提交反馈"}
          </button>
          {submittedAt && (
            <span className="text-xs text-emerald-700">
              已记录 · {submittedAt}
            </span>
          )}
          {err && <span className="text-xs text-red-600">{err}</span>}
        </div>
      </div>
    </article>
  );
}
