"use client";

import { useEffect, useState } from "react";
import {
  activeChildId,
  submitTrialFeedback,
  type TrialFeedbackCategory,
} from "@/lib/api";
import { getFamilySession, type FamilySession } from "@/lib/family-session";

const CATEGORY_OPTIONS: { value: TrialFeedbackCategory; label: string }[] = [
  { value: "bug", label: "出错/打不开" },
  { value: "confusing", label: "看不懂/不好用" },
  { value: "idea", label: "想要功能" },
  { value: "other", label: "其他" },
];

export default function FeedbackPage() {
  const [session, setSession] = useState<FamilySession | null>(null);
  const [page, setPage] = useState("/log");
  const [category, setCategory] = useState<TrialFeedbackCategory>("confusing");
  const [message, setMessage] = useState("");
  const [contact, setContact] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    setSession(getFamilySession());
  }, []);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!message.trim() || busy) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const childId = activeChildId();
      const saved = await submitTrialFeedback({
        child_id: childId || null,
        page,
        category,
        message: message.trim(),
        contact: contact.trim() || null,
      });
      setDone(`已收到反馈 ${saved.id.slice(0, 8)}`);
      setMessage("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">内测反馈</h1>
        <p className="mt-1 text-sm text-stone-500">
          哪一步卡住、哪里看不懂、想要什么，都可以直接写。我们按家庭内测反馈迭代。
          {session && (
            <span className="block pt-1 text-xs">
              {session.family_name} · 当前孩子 {session.child_id || "未选择"}
            </span>
          )}
        </p>
      </div>

      <form
        onSubmit={onSubmit}
        className="space-y-4 rounded-md border border-stone-200 bg-white p-4"
      >
        <label className="block text-sm font-medium text-stone-700">
          发生在哪个页面
          <select
            value={page}
            onChange={(e) => setPage(e.target.value)}
            className="mt-1 block min-h-11 w-full rounded-md border border-stone-300 bg-white px-3"
            disabled={busy}
          >
            <option value="/login">家庭访问</option>
            <option value="/children">孩子档案</option>
            <option value="/log">记一笔</option>
            <option value="/timeline">时间轴</option>
            <option value="/heatmap">热度图</option>
            <option value="/weekly">周报</option>
            <option value="other">其他</option>
          </select>
        </label>

        <div>
          <p className="text-sm font-medium text-stone-700">反馈类型</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {CATEGORY_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => setCategory(o.value)}
                disabled={busy}
                className={`min-h-10 rounded-md border px-3 text-sm ${
                  category === o.value
                    ? "border-stone-900 bg-stone-900 text-white"
                    : "border-stone-300 bg-white text-stone-700"
                }`}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        <label className="block text-sm font-medium text-stone-700">
          具体说明
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="例：我记完一笔后，不知道去哪里看刚刚记录的内容。"
            rows={5}
            maxLength={2000}
            className="mt-1 block w-full rounded-md border border-stone-300 px-3 py-2"
            disabled={busy}
          />
        </label>

        <label className="block text-sm font-medium text-stone-700">
          联系方式（可选）
          <input
            value={contact}
            onChange={(e) => setContact(e.target.value)}
            placeholder="微信昵称 / 电话 / 备注"
            maxLength={120}
            className="mt-1 block min-h-11 w-full rounded-md border border-stone-300 px-3"
            disabled={busy}
          />
        </label>

        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}
        {done && (
          <div className="rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-800">
            {done}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !message.trim()}
          className="min-h-11 rounded-md bg-stone-900 px-4 text-sm text-white
                     disabled:cursor-not-allowed disabled:bg-stone-400"
        >
          {busy ? "提交中…" : "提交反馈"}
        </button>
      </form>
    </div>
  );
}
