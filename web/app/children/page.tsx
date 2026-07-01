"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  createChild,
  listChildren,
  type ChildOut,
} from "@/lib/api";
import {
  getFamilySession,
  setSessionChildId,
  type FamilySession,
} from "@/lib/family-session";

export default function ChildrenPage() {
  const router = useRouter();
  const [session, setSession] = useState<FamilySession | null>(null);
  const [children, setChildren] = useState<ChildOut[]>([]);
  const [name, setName] = useState("");
  const [birthday, setBirthday] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const s = getFamilySession();
      setSession(s);
      if (!s) {
        setChildren([]);
        return;
      }
      setChildren(await listChildren());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  function selectChild(childId: string) {
    const next = setSessionChildId(childId);
    setSession(next);
    router.push("/log");
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (saving || !name.trim() || !birthday.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const child = await createChild({
        name: name.trim(),
        birthday: birthday.trim(),
      });
      setSession(setSessionChildId(child.id));
      setChildren((rows) => [...rows, child]);
      setName("");
      setBirthday("");
      router.push("/log");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  if (!session && !loading) {
    return (
      <div className="mx-auto max-w-lg rounded-md border border-stone-200 bg-white p-5">
        <h1 className="text-xl font-semibold">孩子档案</h1>
        <p className="mt-2 text-sm text-stone-500">
          先输入家庭访问码，再维护孩子档案。
        </p>
        <a
          href="/login"
          className="mt-4 inline-flex min-h-11 items-center rounded-md bg-stone-900 px-4 text-sm text-white"
        >
          去家庭访问
        </a>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">孩子档案</h1>
        <p className="mt-1 text-sm text-stone-500">
          {session
            ? `${session.family_name} · 当前孩子 ${session.child_id || "未选择"}`
            : "加载中…"}
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="rounded-md border border-stone-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-stone-700">已有孩子</h2>
        {loading && <p className="mt-3 text-sm text-stone-500">加载中…</p>}
        {!loading && children.length === 0 && (
          <p className="mt-3 text-sm text-stone-500">
            这个家庭还没有孩子档案。先在下面创建一个。
          </p>
        )}
        <div className="mt-3 grid gap-2">
          {children.map((child) => {
            const active = child.id === session?.child_id;
            return (
              <button
                key={child.id}
                type="button"
                onClick={() => selectChild(child.id)}
                className={`flex min-h-12 items-center justify-between rounded-md border px-3 text-left text-sm ${
                  active
                    ? "border-stone-900 bg-stone-900 text-white"
                    : "border-stone-200 bg-stone-50 text-stone-800"
                }`}
              >
                <span>
                  <span className="font-medium">{child.name}</span>
                  <span className="ml-2 text-xs opacity-70">
                    {child.birthday}
                  </span>
                </span>
                <span className="text-xs opacity-70">
                  {active ? "当前" : "选择"}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <form
        onSubmit={onSubmit}
        className="rounded-md border border-stone-200 bg-white p-4"
      >
        <h2 className="text-sm font-semibold text-stone-700">创建孩子档案</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm text-stone-700">
            昵称
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例：宝宝"
              maxLength={40}
              className="mt-1 block min-h-11 w-full rounded-md border border-stone-300 px-3"
              disabled={saving}
            />
          </label>
          <label className="text-sm text-stone-700">
            生日
            <input
              value={birthday}
              onChange={(e) => setBirthday(e.target.value)}
              placeholder="YYYY-MM-DD"
              inputMode="numeric"
              maxLength={10}
              className="mt-1 block min-h-11 w-full rounded-md border border-stone-300 px-3"
              disabled={saving}
            />
          </label>
        </div>
        <button
          type="submit"
          disabled={saving || !name.trim() || !birthday.trim()}
          className="mt-4 min-h-11 rounded-md bg-stone-900 px-4 text-sm text-white
                     disabled:cursor-not-allowed disabled:bg-stone-400"
        >
          {saving ? "保存中…" : "保存并开始记录"}
        </button>
      </form>
    </div>
  );
}
