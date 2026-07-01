"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { authenticateFamily } from "@/lib/api";
import {
  clearFamilySession,
  getFamilySession,
  saveFamilySession,
  type FamilySession,
} from "@/lib/family-session";

export default function LoginPage() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [session, setSession] = useState<FamilySession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSession(getFamilySession());
  }, []);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const family = await authenticateFamily(code.trim());
      const nextSession = {
        family_id: family.family_id,
        family_name: family.family_name,
        access_code: code.trim(),
        child_id: family.children[0]?.id ?? null,
      };
      saveFamilySession(nextSession);
      setSession(nextSession);
      router.push(family.children[0] ? "/log" : "/children");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function onClear() {
    clearFamilySession();
    setSession(null);
    setCode("");
  }

  return (
    <div className="mx-auto max-w-md space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">家庭访问</h1>
        <p className="mt-1 text-sm text-stone-500">
          {session
            ? `${session.family_name} 已连接${session.child_id ? ` · ${session.child_id}` : ""}`
            : "输入邀请访问码"}
        </p>
      </div>

      <form
        onSubmit={onSubmit}
        className="rounded-md border border-stone-200 bg-white p-4 shadow-sm"
      >
        <label className="block text-sm font-medium text-stone-700">
          访问码
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoComplete="one-time-code"
            inputMode="text"
            className="mt-2 block min-h-12 w-full rounded-md border border-stone-300 px-3 text-base
                       focus:border-stone-600 focus:outline-none"
            placeholder="family-code"
            disabled={busy}
          />
        </label>

        {error && (
          <div className="mt-3 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <button
            type="submit"
            disabled={!code.trim() || busy}
            className="min-h-11 rounded-md bg-stone-900 px-4 text-sm text-white
                       disabled:cursor-not-allowed disabled:bg-stone-400"
          >
            {busy ? "验证中…" : "进入"}
          </button>
          {session && (
            <button
              type="button"
              onClick={onClear}
              className="min-h-11 rounded-md border border-stone-300 bg-white px-4 text-sm"
            >
              清除本机访问码
            </button>
          )}
        </div>
      </form>

      <section className="rounded-md border border-stone-200 bg-white p-4 text-sm text-stone-600">
        <h2 className="font-semibold text-stone-800">首次使用</h2>
        <ol className="mt-2 list-decimal space-y-1 pl-5">
          <li>向邀请人领取家庭访问码。</li>
          <li>输入访问码进入家庭空间。</li>
          <li>第一次进入后，在“孩子”页创建孩子档案。</li>
          <li>之后就可以在“记一笔”记录日常，并到时间轴、热度图查看变化。</li>
        </ol>
        <p className="mt-3 text-xs text-stone-500">
          当前是邀请制内测，暂不开放自行创建家庭，避免陌生人误入和占用名额。
        </p>
      </section>
    </div>
  );
}
