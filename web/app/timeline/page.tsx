"use client";

// PRD §2.1#5 — /timeline: events + signals 混排，点 signal 看 evidence_events.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  activeChildId,
  extractSignals,
  listEvents,
  listSignals,
  type EventOut,
  type SignalOut,
} from "@/lib/api";

type Item =
  | { kind: "event"; at: string; event: EventOut }
  | { kind: "signal"; at: string; signal: SignalOut };

function fmtDate(iso: string): string {
  // "2026-05-19T10:00:00+08:00" → "2026-05-19 10:00"
  return iso.slice(0, 16).replace("T", " ");
}

export default function TimelinePage() {
  const [events, setEvents] = useState<EventOut[]>([]);
  const [signals, setSignals] = useState<SignalOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [analysisWarning, setAnalysisWarning] = useState<string | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [openSignalId, setOpenSignalId] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    setAnalysisWarning(null);
    try {
      const childId = activeChildId();
      if (!childId) {
        setEvents([]);
        setSignals([]);
        setError("还没有选择孩子。先去孩子档案创建或选择孩子。");
        return;
      }
      const e = await listEvents({ child_id: childId, limit: 100 });
      setEvents(e);
      try {
        const s = await listSignals({ child_id: childId, limit: 50 });
        setSignals(s);
      } catch (err) {
        setSignals([]);
        setAnalysisWarning(
          `事件已加载；信号分析暂不可用：${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function onExtract() {
    setExtracting(true);
    setError(null);
    setAnalysisWarning(null);
    try {
      const childId = activeChildId();
      if (!childId) {
        setError("还没有选择孩子。先去孩子档案创建或选择孩子。");
        return;
      }
      await extractSignals({ child_id: childId, window_days: 14 });
      await refresh();
    } catch (err) {
      setAnalysisWarning(
        `提取信号暂不可用：${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setExtracting(false);
    }
  }

  const items = useMemo<Item[]>(() => {
    const merged: Item[] = [
      ...events.map<Item>((e) => ({ kind: "event", at: e.timestamp, event: e })),
      ...signals.map<Item>((s) => ({
        kind: "signal",
        at: s.last_seen_at,
        signal: s,
      })),
    ];
    merged.sort((a, b) => (a.at < b.at ? 1 : -1));
    return merged;
  }, [events, signals]);

  const eventById = useMemo(() => {
    const m = new Map<string, EventOut>();
    for (const e of events) m.set(e.id, e);
    return m;
  }, [events]);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">时间轴</h1>
          <p className="mt-1 text-sm text-stone-500">
            events + signals 混排，最新在上。
          </p>
        </div>
        <button
          onClick={onExtract}
          disabled={extracting}
          className="rounded-md border border-stone-300 bg-white px-3 py-1.5 text-sm
                     transition hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {extracting ? "提取中…" : "提取信号 (14d)"}
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          {error}{" "}
          {error.includes("孩子") && (
            <Link href="/children" className="underline">
              去孩子档案
            </Link>
          )}
        </div>
      )}

      {analysisWarning && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          {analysisWarning}
        </div>
      )}

      {loading && <div className="text-sm text-stone-500">加载中…</div>}

      {!loading && items.length === 0 && (
        <div className="rounded-md border border-dashed border-stone-300 p-8 text-center text-sm text-stone-500">
          还没有事件。先去 <a href="/log" className="underline">记一笔</a>。
        </div>
      )}

      <ol className="space-y-3">
        {items.map((it) =>
          it.kind === "event" ? (
            <EventRow key={`e-${it.event.id}`} event={it.event} />
          ) : (
            <SignalRow
              key={`s-${it.signal.id}`}
              signal={it.signal}
              expanded={openSignalId === it.signal.id}
              onToggle={() =>
                setOpenSignalId(
                  openSignalId === it.signal.id ? null : it.signal.id
                )
              }
              eventById={eventById}
            />
          )
        )}
      </ol>
    </div>
  );
}

function EventRow({ event }: { event: EventOut }) {
  return (
    <li className="rounded-md border border-stone-200 bg-white p-4">
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-stone-500">{fmtDate(event.timestamp)}</span>
        <span className="rounded bg-stone-100 px-2 py-0.5 text-xs text-stone-600">
          {event.type}
        </span>
      </div>
      <p className="mt-2 text-sm text-stone-800">{event.summary}</p>
      <p className="mt-1 text-xs text-stone-500">{event.raw_text}</p>
      <div className="mt-2 flex flex-wrap gap-1">
        {event.domains.map((d) => (
          <span
            key={d}
            className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900"
          >
            {d}
          </span>
        ))}
        {event.emotions.map((e) => (
          <span
            key={e}
            className="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-900"
          >
            {e}
          </span>
        ))}
      </div>
    </li>
  );
}

function SignalRow({
  signal,
  expanded,
  onToggle,
  eventById,
}: {
  signal: SignalOut;
  expanded: boolean;
  onToggle: () => void;
  eventById: Map<string, EventOut>;
}) {
  return (
    <li className="rounded-md border border-emerald-300 bg-emerald-50 p-4">
      <button
        onClick={onToggle}
        className="flex w-full items-baseline justify-between text-left"
      >
        <div>
          <span className="text-xs text-emerald-700">
            {fmtDate(signal.last_seen_at)} · 信号
          </span>
          <h3 className="mt-1 text-sm font-semibold text-emerald-900">
            {signal.signal_type} · {signal.domains.join(" / ")}
          </h3>
          <p className="mt-1 text-xs text-emerald-800">
            强度 {signal.intensity.toFixed(2)} · 置信度{" "}
            {signal.confidence.toFixed(2)} · 月龄 {signal.child_age_months}
          </p>
        </div>
        <span className="ml-3 text-xs text-emerald-700">
          {expanded ? "收起 ▴" : "展开 ▾"}
        </span>
      </button>

      {expanded && (
        <div className="mt-3 space-y-2 border-t border-emerald-200 pt-3">
          {signal.notes && (
            <p className="text-xs text-emerald-900">{signal.notes}</p>
          )}
          <div>
            <p className="text-xs text-emerald-700">
              证据事件（{signal.evidence_event_ids.length}）:
            </p>
            <ul className="mt-1 space-y-1">
              {signal.evidence_event_ids.map((eid) => {
                const ev = eventById.get(eid);
                return (
                  <li key={eid} className="text-xs text-emerald-900">
                    {ev ? (
                      <>
                        <span className="text-emerald-700">
                          {fmtDate(ev.timestamp)}
                        </span>{" "}
                        — {ev.summary}
                      </>
                    ) : (
                      <span className="text-stone-500">{eid}（事件已不在最近 100 条中）</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}
    </li>
  );
}
