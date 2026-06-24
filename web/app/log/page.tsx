"use client";

// PRD §2.1#5 — /log: textarea + 记一笔 → POST /events → 结构化结果回显.

import { useEffect, useState } from "react";
import { DEFAULT_CHILD_ID, activeChildId, postEvent, type EventOut } from "@/lib/api";

export default function LogPage() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EventOut | null>(null);
  const [childId, setChildId] = useState(DEFAULT_CHILD_ID);

  useEffect(() => {
    setChildId(activeChildId());
  }, []);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const ev = await postEvent({
        child_id: childId,
        raw_text: text.trim(),
      });
      setResult(ev);
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">记一笔</h1>
        <p className="mt-1 text-sm text-stone-500">
          一句话描述刚刚发生的事 — 模型会拆出 type / domain / emotion.
          孩子 id：<code className="text-stone-700">{childId}</code>
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-3">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="例：今天孩子在小区追蝴蝶追了 20 分钟，笑得停不下来"
          className="block w-full rounded-md border border-stone-300 bg-white p-3 text-base
                     focus:border-stone-500 focus:outline-none"
          rows={4}
          maxLength={4000}
          disabled={busy}
        />
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={busy || !text.trim()}
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
          <dl className="mt-3 grid grid-cols-[6rem_1fr] gap-y-2 text-sm">
            <dt className="text-stone-500">摘要</dt>
            <dd>{result.summary}</dd>
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
