export type FamilySession = {
  family_id: string;
  family_name: string;
  access_code: string;
};

const STORAGE_KEY = "bgh.family_session.v1";

export function getFamilySession(): FamilySession | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<FamilySession>;
    if (!parsed.family_id || !parsed.family_name || !parsed.access_code) {
      return null;
    }
    return {
      family_id: parsed.family_id,
      family_name: parsed.family_name,
      access_code: parsed.access_code,
    };
  } catch {
    return null;
  }
}

export function getFamilyAccessCode(): string | null {
  return getFamilySession()?.access_code ?? null;
}

export function saveFamilySession(session: FamilySession): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

export function clearFamilySession(): void {
  window.localStorage.removeItem(STORAGE_KEY);
}
