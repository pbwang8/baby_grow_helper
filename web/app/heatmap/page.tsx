"use client";

// PRD §2.1#5 — /heatmap: X = child_age_months (NOT calendar date), Y = domain,
// color = intensity. The whole point of this page is that 月龄 axis lets us
// compare different time periods on equal footing.

import { useEffect, useMemo, useState } from "react";
import {
  activeChildId,
  getHeatmap,
  type HeatmapCell,
} from "@/lib/api";

function intensityColor(intensity: number): string {
  // 5-stop ramp from cold neutral to warm amber. Pure inline so no
  // tailwind safelist gymnastics for dynamic class names.
  if (intensity <= 0.0) return "#f5f5f4"; // stone-100
  if (intensity < 0.2) return "#fef3c7"; // amber-100
  if (intensity < 0.4) return "#fde68a"; // amber-200
  if (intensity < 0.6) return "#fcd34d"; // amber-300
  if (intensity < 0.8) return "#f59e0b"; // amber-500
  return "#b45309"; // amber-700
}

export default function HeatmapPage() {
  const [cells, setCells] = useState<HeatmapCell[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getHeatmap({ child_id: activeChildId() });
        if (!cancelled) setCells(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const { ages, domains, byKey } = useMemo(() => {
    const ageSet = new Set<number>();
    const domSet = new Set<string>();
    const map = new Map<string, HeatmapCell>();
    for (const c of cells) {
      ageSet.add(c.age_months);
      domSet.add(c.domain);
      map.set(`${c.age_months}__${c.domain}`, c);
    }
    return {
      ages: Array.from(ageSet).sort((a, b) => a - b),
      domains: Array.from(domSet).sort(),
      byKey: map,
    };
  }, [cells]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">热度图</h1>
        <p className="mt-1 text-sm text-stone-500">
          横轴 = <strong>孩子月龄</strong>（不是日历日期 — 跨时间段才能对比），
          纵轴 = domain，色深 = 强度。
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          热度图分析暂不可用：{error}。你仍然可以先在「记一笔」和「时间轴」
          记录、回看事件。
        </div>
      )}

      {loading && <div className="text-sm text-stone-500">加载中…</div>}

      {!loading && cells.length === 0 && (
        <div className="rounded-md border border-dashed border-stone-300 p-8 text-center text-sm text-stone-500">
          还没有事件 — 热度图为空。
        </div>
      )}

      {!loading && cells.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-stone-200 bg-white p-4">
          <table className="border-separate border-spacing-1">
            <thead>
              <tr>
                <th className="px-2 py-1 text-left text-xs font-medium text-stone-500">
                  domain \ 月龄
                </th>
                {ages.map((a) => (
                  <th
                    key={a}
                    className="px-2 py-1 text-center text-xs font-medium text-stone-500"
                  >
                    {a}m
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {domains.map((d) => (
                <tr key={d}>
                  <td className="px-2 py-1 text-right text-xs text-stone-700">
                    {d}
                  </td>
                  {ages.map((a) => {
                    const cell = byKey.get(`${a}__${d}`);
                    const intensity = cell?.intensity ?? 0;
                    const count = cell?.event_count ?? 0;
                    return (
                      <td
                        key={`${d}-${a}`}
                        title={
                          cell
                            ? `${d} · ${a} 月龄 · ${count} 条 · 强度 ${intensity.toFixed(2)}`
                            : `${d} · ${a} 月龄 · 无数据`
                        }
                        className="h-9 w-12 rounded text-center align-middle text-[10px] font-medium"
                        style={{
                          backgroundColor: intensityColor(intensity),
                          color: intensity > 0.6 ? "#fff" : "#1c1917",
                        }}
                      >
                        {count > 0 ? count : ""}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>

          <div className="mt-4 flex items-center gap-2 text-xs text-stone-500">
            <span>低</span>
            {[0, 0.2, 0.4, 0.6, 0.8, 1.0].map((v) => (
              <span
                key={v}
                className="h-4 w-6 rounded"
                style={{ backgroundColor: intensityColor(v) }}
              />
            ))}
            <span>高</span>
          </div>
        </div>
      )}
    </div>
  );
}
