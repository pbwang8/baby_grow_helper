"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { authenticateFamily } from "@/lib/api";
import {
  clearFamilySession,
  getFamilySession,
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
      setSession({ ...family, access_code: code.trim() });
      router.push("/log");
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
          {session ? `${session.family_name} 已连接` : "输入邀请访问码"}
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
    </div>
  );
}
