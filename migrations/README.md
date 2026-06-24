# Database Migrations

Phase 0-2 used `src/core/db.py::SCHEMA_SQL` as the single SQLite schema source.
Phase 2.5 starts the move toward a service database for family mobile trials.

## Backends

- `migrations/sqlite/` keeps the local-dev SQLite path patchable.
- `migrations/postgres/` defines the service-side Postgres schema.

SQLite remains the default for local tests and single-machine demo. Postgres
is the target for real family data.

## Commands

SQLite local path:

```bash
uv run python -m src.core.migrations --backend sqlite --database ./data/babygrow.db --apply
```

Postgres service URL:

```bash
BGH_DATABASE_URL=postgresql://user:pass@localhost:5432/babygrow \
uv run python -m src.core.migrations --backend postgres --apply
```

`psycopg` is only required when applying Postgres migrations.
