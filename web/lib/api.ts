// Thin client for the FastAPI surface (src/api/main.py).
// Local app defaults to localhost:8000 unless NEXT_PUBLIC_API_BASE is set.

import { getFamilyAccessCode, saveFamilySession } from "./family-session";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// Phase 1 = single child. Hardcoding xiaoming keeps the UI free of a
// child picker (PRD §2.2: "no user management"). When the user wants
// 瑶瑶, they seed a child with id=yaoyao and override via env.
export const DEFAULT_CHILD_ID =
  process.env.NEXT_PUBLIC_CHILD_ID ?? "xiaoming";

export type EventOut = {
  id: string;
  child_id: string;
  timestamp: string;
  raw_text: string;
  summary: string;
  type: string;
  domains: string[];
  emotions: string[];
  context: string;
  model_used: string;
};

export type SignalOut = {
  id: string;
  child_id: string;
  signal_type: string;
  domains: string[];
  intensity: number;
  child_age_months: number;
  delta_from_last_period: number | null;
  confidence: number;
  first_seen_at: string;
  last_seen_at: string;
  evidence_event_ids: string[];
  status: string;
  notes: string;
};

export type HeatmapCell = {
  age_months: number;
  domain: string;
  intensity: number;
  event_count: number;
};

export type FamilyAuthOut = {
  family_id: string;
  family_name: string;
};

async function jsonFetch<T>(
  path: string,
  init?: RequestInit & { searchParams?: Record<string, string | number | string[]> }
): Promise<T> {
  const url = new URL(path, API_BASE);
  if (init?.searchParams) {
    for (const [k, v] of Object.entries(init.searchParams)) {
      if (Array.isArray(v)) {
        for (const item of v) url.searchParams.append(k, String(item));
      } else {
        url.searchParams.set(k, String(v));
      }
    }
  }
  const res = await fetch(url.toString(), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...familyHeader(),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      // body wasn't JSON — keep statusText
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

function familyHeader(): Record<string, string> {
  const code = getFamilyAccessCode();
  return code ? { "X-Family-Code": code } : {};
}

export async function authenticateFamily(access_code: string): Promise<FamilyAuthOut> {
  const family = await jsonFetch<FamilyAuthOut>("/auth/family", {
    method: "POST",
    body: JSON.stringify({ access_code }),
  });
  saveFamilySession({ ...family, access_code });
  return family;
}

export async function postEvent(args: {
  child_id: string;
  raw_text: string;
}): Promise<EventOut> {
  return jsonFetch<EventOut>("/events", {
    method: "POST",
    body: JSON.stringify(args),
  });
}

export async function listEvents(args: {
  child_id: string;
  limit?: number;
}): Promise<EventOut[]> {
  return jsonFetch<EventOut[]>("/events", {
    searchParams: { child_id: args.child_id, limit: args.limit ?? 100 },
  });
}

export async function listSignals(args: {
  child_id: string;
  status?: string;
  limit?: number;
}): Promise<SignalOut[]> {
  return jsonFetch<SignalOut[]>("/signals", {
    searchParams: {
      child_id: args.child_id,
      ...(args.status ? { status: args.status } : {}),
      limit: args.limit ?? 100,
    },
  });
}

export async function extractSignals(args: {
  child_id: string;
  window_days?: number;
}): Promise<SignalOut[]> {
  return jsonFetch<SignalOut[]>("/signals/extract", {
    method: "POST",
    searchParams: {
      child_id: args.child_id,
      window_days: args.window_days ?? 14,
    },
  });
}

export async function getHeatmap(args: {
  child_id: string;
  domains?: string[];
}): Promise<HeatmapCell[]> {
  return jsonFetch<HeatmapCell[]>("/heatmap", {
    searchParams: {
      child_id: args.child_id,
      ...(args.domains && args.domains.length
        ? { domains: args.domains }
        : {}),
    },
  });
}

// ---- Phase 2: weekly insights -------------------------------------------

export type InsightAxis =
  | "highlight"
  | "change_over_time"
  | "next_week_focus"
  | "open_questions";

export type InsightSectionOut = {
  axis: InsightAxis;
  title: string;
  body: string;
  sources_used: string[];
};

export type WeeklyInsightOut = {
  id: string;
  child_id: string;
  week_start: string;
  week_end: string;
  version: number;
  child_age_months: number;
  sections: InsightSectionOut[];
  open_questions: string[];
  sources_used: string[];
  backend: string;
  model_used: string;
  tokens_in: number;
  tokens_out: number;
  created_at: string;
};

export type FeedbackAccuracy = "accurate" | "inaccurate" | "unsure";
export type FeedbackValue = "inspiring" | "unhelpful" | "missed_point";

export type FeedbackOut = {
  id: string;
  insight_id: string;
  section_idx: number;
  accuracy: FeedbackAccuracy | null;
  value: FeedbackValue | null;
  free_text: string | null;
  created_at: string;
};

export async function generateInsight(args: {
  child_id: string;
  week_start: string; // YYYY-MM-DD (must be a Monday)
  backend?: "claude" | "local-fallback";
}): Promise<WeeklyInsightOut> {
  return jsonFetch<WeeklyInsightOut>("/insights/generate", {
    method: "POST",
    body: JSON.stringify({
      child_id: args.child_id,
      week_start: args.week_start,
      ...(args.backend ? { backend: args.backend } : {}),
    }),
  });
}

export async function listInsights(args: {
  child_id: string;
  limit?: number;
}): Promise<WeeklyInsightOut[]> {
  return jsonFetch<WeeklyInsightOut[]>("/insights", {
    searchParams: {
      child_id: args.child_id,
      limit: args.limit ?? 12,
    },
  });
}

export async function getInsight(insight_id: string): Promise<WeeklyInsightOut> {
  return jsonFetch<WeeklyInsightOut>(`/insights/${insight_id}`);
}

export async function postFeedback(args: {
  insight_id: string;
  section_idx: number;
  accuracy?: FeedbackAccuracy | null;
  value?: FeedbackValue | null;
  free_text?: string | null;
}): Promise<FeedbackOut> {
  const body: Record<string, unknown> = { section_idx: args.section_idx };
  if (args.accuracy) body.accuracy = args.accuracy;
  if (args.value) body.value = args.value;
  if (args.free_text) body.free_text = args.free_text;
  return jsonFetch<FeedbackOut>(`/insights/${args.insight_id}/feedback`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
