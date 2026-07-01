# Phase 2.5 Family Mobile MVP Baseline

Date: 2026-06-24; refreshed 2026-06-30
Scope: invited family trial, up to 10 families

## 1. Current Gate Status

| Gate | Status | Evidence |
|---|---:|---|
| Family access-code auth | ✅ | `POST /auth/family`, `X-Family-Code` route guard |
| Family-scoped child discovery | ✅ | `GET /children`, login stores first visible child id |
| Family child self-service | ✅ | `POST /children`, `/children` mobile page |
| Event write/read on SQLite runtime | ✅ | existing local tests + API tests |
| Event write/read on Postgres runtime adapter | ✅ | `src/core/runtime_store.py`, fake Postgres tests |
| Heatmap on Postgres runtime adapter | ✅ | `/heatmap` now uses family-scoped runtime store |
| Family admin create/list | ✅ | SQLite + Postgres-backed paths |
| Child admin create/assign | ✅ | `family_admin create-child`, `assign-child` |
| Readable per-family access code | ✅ | generated `bgh-xxxx-xxxx-xxxx`; explicit `--access-code` still supported |
| Mobile login shell | ✅ | `/login`, browser-local family session |
| Product feedback entry | ✅ | `POST /feedback`, `/feedback` mobile page, `trial_feedback` migration |
| Postgres automatic backup | ✅ | daily macOS LaunchAgent at 03:15 + login self-check; `pg_dump -Fc` verified via `pg_restore -l` |
| PWA manifest | ✅ | `web/public/manifest.webmanifest` |
| Docker Compose scaffold | ✅ | `deploy/docker-compose.family-trial.yml` |
| Docker runtime smoke on this machine | ✅ | API/Web rebuilt and restarted; Postgres migration `0002` applied |

## 2. Test Baseline

Last verified command set:

```text
uv run --no-sync ruff check src tests
uv run --no-sync mypy
uv run --no-sync pytest -m "not integration"
cd web && npm run typecheck
cd web && npm run build
```

Result:

```text
230 passed, 5 deselected
Total coverage: 78.40%
Next.js production build: passed
```

Coverage note: overall percentage is pulled down by the one-shot
`src/scripts/cloud_smoke.py` ops script, which remains intentionally outside
the normal local test path. Core Phase 2.5 modules are covered:

- `src/core/runtime_store.py`: 90%
- `src/scripts/family_admin.py`: 81%
- `src/api/main.py`: 91%

## 3. What A Family Can Try Now

For each invited family:

1. Operator creates a family invite with `family_admin create`.
2. Family opens `/login` on mobile.
3. Family enters access code.
4. If needed, family creates/selects the child on `/children`.
5. App stores the family session and active child in browser local storage.
6. `/log` writes structured events for that family's child.
7. `/timeline` can read events through the active child id, even if signal
   analysis is temporarily unavailable.
8. `/heatmap` renders age-month × domain cells from family-scoped events.
9. `/feedback` captures product-level trial feedback for iteration.

## 4. Remaining Before Real Family Trial

1. Run a phone smoke test over the current HTTPS tunnel.
2. Move from temporary Cloudflare Quick Tunnel to the chosen stable HTTPS route.
3. Finish Postgres runtime migration for `/signals` extraction and `/weekly`.
4. Add export/delete admin API before sharing with non-author households.
5. Implement the private remote backup hook for encrypted off-machine storage
   before expanding beyond the author's tightly controlled inner test.
6. Keep `/log` + `/timeline` as the first stable family trial surface until
   analysis pages are fully migrated.

## 5. Current Boundary

This is still an invited inner test, not a public service:

- no public registration
- no payments
- no app-store package
- no real-user data in Git
- no open internet deployment without HTTPS
