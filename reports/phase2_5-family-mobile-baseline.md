# Phase 2.5 Family Mobile MVP Baseline

Date: 2026-06-24
Scope: invited family trial, up to 10 families

## 1. Current Gate Status

| Gate | Status | Evidence |
|---|---:|---|
| Family access-code auth | ✅ | `POST /auth/family`, `X-Family-Code` route guard |
| Family-scoped child discovery | ✅ | `GET /children`, login stores first visible child id |
| Event write/read on SQLite runtime | ✅ | existing local tests + API tests |
| Event write/read on Postgres runtime adapter | ✅ | `src/core/runtime_store.py`, fake Postgres tests |
| Family admin create/list | ✅ | SQLite + Postgres-backed paths |
| Child admin create/assign | ✅ | `family_admin create-child`, `assign-child` |
| Mobile login shell | ✅ | `/login`, browser-local family session |
| PWA manifest | ✅ | `web/public/manifest.webmanifest` |
| Docker Compose scaffold | ✅ | `deploy/docker-compose.family-trial.yml` |
| Docker runtime smoke on this machine | ⚠️ | Not run: `docker` command is unavailable locally |

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
225 passed, 5 deselected
Total coverage: 78.44%
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
2. Operator creates the child with `family_admin create-child`.
3. Family opens `/login` on mobile.
4. Family enters access code.
5. App stores the family session in browser local storage.
6. `/log` writes structured events for that family's child.
7. `/timeline` can read events through the active child id.

## 4. Remaining Before Real Family Trial

1. Run Docker Compose on a machine with Docker installed.
2. Pull Ollama model inside the `ollama` container:
   `ollama pull qwen2.5:3b-instruct`.
3. Run a phone smoke test over LAN or HTTPS domain.
4. Decide whether the first family trial exposes only `/log` + `/timeline`, or
   waits for Postgres runtime migration of `/signals`, `/heatmap`, `/weekly`.
5. Add HTTPS for any remote family outside the same Wi-Fi.

## 5. Current Boundary

This is still an invited inner test, not a public service:

- no public registration
- no payments
- no app-store package
- no real-user data in Git
- no open internet deployment without HTTPS
