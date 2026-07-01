"use client";

// PRD §2.1#5 — /log: textarea + 记一笔 → POST /events → 结构化结果回显.

import { useEffect, useState } from "react";
import Link from "next/link";
import { DEFAULT_CHILD_ID, activeChildId, postEvent, type EventOut } from "@/lib/api";
import { getFamilySession } from "@/lib/family-session";

type DatePreset = "today" | "yesterday" | "last_week" | "last_month" | "custom";

function localDateFromOffset(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${month}-${day}`;
}

function presetDate(preset: DatePreset): string {
  if (preset === "today" || preset === "custom") return localDateFromOffset(0);
  if (preset === "yesterday") return localDateFromOffset(-1);
  if (preset === "last_week") return localDateFromOffset(-7);
  return localDateFromOffset(-30);
}

const DATE_PRESETS: Array<{ key: DatePreset; label: string }> = [
  { key: "today", label: "今天" },
  { key: "yesterday", label: "昨天" },
  { key: "last_week", label: "上周" },
  { key: "last_month", label: "上月" },
  { key: "custom", label: "自定义" },
];

export default function LogPage() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EventOut | null>(null);
  const [childId, setChildId] = useState(DEFAULT_CHILD_ID);
  const [needsChild, setNeedsChild] = useState(false);
  const [datePreset, setDatePreset] = useState<DatePreset>("today");
  const [occurredDate, setOccurredDate] = useState(localDateFromOffset(0));

  useEffect(() => {
    const nextChildId = activeChildId();
    setChildId(nextChildId);
    setNeedsChild(Boolean(getFamilySession()) && !nextChildId);
  }, []);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!text.trim() || busy || !childId) return;
    setBusy(true);
    setError(null);
    try {
      const historicalDate =
        datePreset === "today" ? undefined : occurredDate.trim();
      const ev = await postEvent({
        child_id: childId,
        raw_text: text.trim(),
        ...(historicalDate ? { occurred_at: historicalDate } : {}),
      });
      setResult(ev);
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function selectPreset(preset: DatePreset) {
    setDatePreset(preset);
    if (preset !== "custom") {
      setOccurredDate(presetDate(preset));
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">记一笔</h1>
        <p className="mt-1 text-sm text-stone-500">
          一句话描述刚刚发生的事，系统会整理成成长事件并放入时间轴。
          {childId && (
            <>
              {" "}
              当前孩子：<code className="text-stone-700">{childId}</code>
            </>
          )}
        </p>
      </div>

      {needsChild && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          这个家庭还没有选择孩子。先去{" "}
          <Link href="/children" className="underline">
            孩子档案
          </Link>{" "}
          创建或选择孩子。
        </div>
      )}

      <form onSubmit={onSubmit} className="space-y-3">
        <section className="rounded-md border border-stone-200 bg-white p-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-sm font-semibold text-stone-700">发生时间</h2>
              <p className="mt-1 text-xs text-stone-500">
                补历史里程碑时，先点一个历史时间，再写当时发生了什么。
              </p>
            </div>
            <span className="text-xs text-stone-500">
              {datePreset === "today" ? "按当前时间记录" : `记录到 ${occurredDate}`}
            </span>
          </div>
          <div className="mt-3 grid grid-cols-5 gap-2">
            {DATE_PRESETS.map((preset) => {
              const active = datePreset === preset.key;
              return (
                <button
                  key={preset.key}
                  type="button"
                  onClick={() => selectPreset(preset.key)}
                  className={`min-h-10 rounded-md border px-2 text-sm ${
                    active
                      ? "border-stone-900 bg-stone-900 text-white"
                      : "border-stone-300 bg-white text-stone-700"
                  }`}
                >
                  {preset.label}
                </button>
              );
            })}
          </div>
          {datePreset === "custom" && (
            <label className="mt-3 block text-sm text-stone-700">
              选择发生日期
              <input
                type="date"
                value={occurredDate}
                onChange={(e) => setOccurredDate(e.target.value)}
                className="mt-1 block min-h-11 w-full rounded-md border border-stone-300 px-3"
              />
            </label>
          )}
        </section>

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="例：今天孩子在小区追蝴蝶追了 20 分钟，笑得停不下来；或：补历史，瑶瑶第一次自己扶栏杆上楼梯"
          className="block w-full rounded-md border border-stone-300 bg-white p-3 text-base
                     focus:border-stone-500 focus:outline-none"
          rows={4}
          maxLength={4000}
          disabled={busy}
        />
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={busy || !text.trim() || !childId}
            className="min-h-11 rounded-md bg-stone-800 px-4 py-2 text-sm text-white
                       transition disabled:cursor-not-allowed disabled:bg-stone-400"
          >
            {busy ? "处理中…" : "记一笔"}
          </button>
          <span className="text-xs text-stone-400">
            {text.length}/4000
          </span>
        </div>
      </form>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          失败：{error}
        </div>
      )}

      {result && (
        <section className="rounded-md border border-stone-200 bg-white p-4">
          <h2 className="text-sm font-semibold text-stone-700">结构化结果</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            <Link
              href="/timeline"
              className="inline-flex min-h-10 items-center rounded-md bg-stone-900 px-3 text-sm text-white"
            >
              去时间轴查看
            </Link>
            <Link
              href="/feedback"
              className="inline-flex min-h-10 items-center rounded-md border border-stone-300 px-3 text-sm text-stone-700"
            >
              反馈这个体验
            </Link>
          </div>
          <dl className="mt-3 grid grid-cols-[6rem_1fr] gap-y-2 text-sm">
            <dt className="text-stone-500">摘要</dt>
            <dd>{result.summary}</dd>
            <dt className="text-stone-500">发生时间</dt>
            <dd className="text-stone-700">{result.timestamp}</dd>
            <dt className="text-stone-500">type</dt>
            <dd>
              <span className="rounded bg-stone-100 px-2 py-0.5 text-xs">
                {result.type}
              </span>
            </dd>
            <dt className="text-stone-500">domain</dt>
            <dd className="space-x-1">
              {result.domains.map((d) => (
                <span
                  key={d}
                  className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900"
                >
                  {d}
                </span>
              ))}
            </dd>
            <dt className="text-stone-500">emotion</dt>
            <dd className="space-x-1">
              {result.emotions.length === 0 && (
                <span className="text-xs text-stone-400">—</span>
              )}
              {result.emotions.map((e) => (
                <span
                  key={e}
                  className="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-900"
                >
                  {e}
                </span>
              ))}
            </dd>
            <dt className="text-stone-500">context</dt>
            <dd className="text-stone-700">{result.context || "—"}</dd>
            <dt className="text-stone-500">model</dt>
            <dd className="text-xs text-stone-500">{result.model_used}</dd>
            <dt className="text-stone-500">id</dt>
            <dd className="text-xs text-stone-400">{result.id}</dd>
          </dl>
        </section>
      )}
    </div>
  );
}
