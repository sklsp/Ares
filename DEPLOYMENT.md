# Deployment & Operations

Production deployment, operations, and recovery procedures for the
E-Commerce Intelligence Platform.

**Data authority:** PostgreSQL is the business source of truth (catalog,
users, research jobs, opportunities, audit logs). Redis is transient queue
transport and rate-limit state: losing it loses nothing authoritative.

---

## Local development (no Docker)

```powershell
# Backend
cd backend
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m app.seed          # creates schema + demo catalog
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# Frontend (second terminal)
npm install
npm run dev
```

No Redis required: without `REDIS_URL` the app uses an inline queue backend.
No Ollama required for API/catalog features; set `LLM_PROVIDER=test` for
deterministic agent behavior without a model.

## Docker Compose (production-like)

```powershell
docker compose up --build
```

Starts PostgreSQL, Redis, a one-shot migration service, the API, and 2
workers with health-gated startup ordering. Scale:

```powershell
docker compose up --scale api=2 --scale worker=4
```

## Environment

Copy `.env.production.example` to `.env` and fill in real values. Required
in production: `DATABASE_URL` (PostgreSQL), `REDIS_URL`, `API_KEY`,
`EMBEDDED_WORKER=false`. Validate before starting:

```powershell
cd backend
python -m app.production_check    # exit 0 = ready, 1 = blocked with reasons
```

## Diagnostics

```powershell
cd backend
python ..\scripts\doctor.py       # environment report with fixes
python ..\scripts\test_queue.py --redis redis://localhost:6379/0
```

The doctor reports PASS/FAIL/SKIP per dependency and classifies the machine
as development-ready or production-blocked.

---

## Production architecture

```
Load balancer / reverse proxy (TLS)
        ↓
   API replicas (EMBEDDED_WORKER=false)      ← stateless, horizontally scalable
        ↓                        ↓
  PostgreSQL (source of truth)  Redis (queue + rate limits)
        ↑                        ↑
   Worker replicas (python -m app.worker)   ← claim jobs atomically
```

- **API replicas** serve HTTP; they never execute jobs in production.
- **Workers** claim from Redis atomically; run 2+ for throughput and failover.
- **PostgreSQL** holds all business state; scale vertically first.
- **Redis** is transient; restart-safe because jobs are re-delivered from
  durable DB rows on recovery.

### Migrations

```powershell
# Applied automatically by the compose `migrate` service, or manually:
cd backend
python -m alembic upgrade head
python -m app.seed --keep     # --keep preserves existing catalog data
```

Migration safety is covered by `tests/test_migrations.py`: fresh-database
chains, idempotent re-runs, and legacy-database upgrades with data
preservation.

### Scaling

| Component | Scale by | Notes |
|---|---|---|
| API | `--scale api=N` | Stateless; any replica serves any request |
| Workers | `--scale worker=N` | Atomic claiming prevents double execution |
| PostgreSQL | vertical / managed service | Connection pool per process |
| Redis | managed service with persistence | AOF enabled in compose |

---

## Health & observability

| Endpoint | Meaning | Fails when |
|---|---|---|
| `/live` | Process is running | never (liveness) |
| `/ready` | Can serve traffic | database unreachable |
| `/health` | Dependency detail | reports degraded + reason |
| `/metrics` | Prometheus exposition | - |

Worker health is observable through `/metrics` (`worker_jobs_total`,
`worker_job_duration_seconds`) and job rows (`worker_id`, `retry_count`).

**Traces:** set `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT` to
export OTLP spans (`POST /agent/run` → `research.enqueue` → worker
`research.execute`). Disabled by default; the app runs identically without
a collector.

**Correlation:** every response carries `X-Correlation-ID`; pass it back on
retries and include it in support requests.

---

## Backups

```powershell
# PostgreSQL (authoritative business data): nightly, plus before deploys
docker compose exec postgres pg_dump -U ecommerce ecommerce_agent > backup.sql

# Restore
docker compose exec -T postgres psql -U ecommerce ecommerce_agent < backup.sql
```

Application configuration lives in your secret store / `.env`: back it up
with your infrastructure secrets, never in the repository.

**Redis:** no backup required. On restore, workers re-poll and startup
recovery re-enqueues QUEUED/RUNNING jobs from PostgreSQL.

---

## Rollbacks

- **Bad application deploy:** redeploy the previous image tag. Migrations
  are additive; older code runs safely against a newer schema.
- **Failed migration:** `alembic downgrade <revision>` (reversible where
  practical: check the migration's `downgrade()` first), then redeploy.
- **Broken worker version:** stop workers, redeploy, restart. Jobs stay
  QUEUED in PostgreSQL and are reclaimed automatically.
- **Redis outage:** API returns a clear error on job submission and keeps
  the DB row; jobs run when Redis returns. Rate limiting degrades to
  per-replica limits rather than blocking traffic.
- **Database outage:** `/ready` returns 503 so the load balancer stops
  routing; `/live` stays 200 so the orchestrator does not restart pods.

---

## Troubleshooting

| Symptom | Diagnosis |
|---|---|
| `/ready` 503 | Database down or `DATABASE_URL` wrong: check `/health` detail |
| Jobs stay QUEUED | Workers not running, or `REDIS_URL` unreachable: `scripts/test_queue.py` |
| Jobs stuck RUNNING | Worker crashed; reclaim runs every 30s, or restart to recover |
| `llm.available: false` in `/health` | Ollama down or model missing: `ollama pull llama3.2` |
| 429 responses | Rate limit hit; shared via Redis across replicas |
| 401 after login worked | Session expired (12h) or user deactivated by an admin |
| Login fails with valid password | Check `/auth/audit` (manager+) for `login.failed` |
