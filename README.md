# Ares

[![CI](https://github.com/sklsp/Ares/actions/workflows/ci.yml/badge.svg)](https://github.com/sklsp/Ares/actions/workflows/ci.yml)

An AI e-commerce operations console. You give it a task in plain language ("improve the three worst product descriptions"), and an LLM agent plans it, calls tools against your catalog, and pauses for human approval before any write. Every step is persisted so you can audit exactly what happened.

## What it does

- **Agent engine** (`backend/app/agent/engine.py`): a bounded loop of plan, call tool, observe result. Three independent limits (iterations, tool calls, wall clock) stop runaway runs; when a limit is hit the agent summarises what it already knows instead of truncating.
- **18 tools in six categories** (products, inventory, analytics, content, intelligence, agent). Each tool has Pydantic input and output schemas plus a read/write class. READ tools run immediately; WRITE tools create an approval request and pause the run until a human approves or rejects it in the UI.
- **Deterministic content scoring**: `analyze_product_content` scores descriptions with weighted Python rules (length, title quality, specifics, filler detection), so rankings are stable and testable. The LLM only writes replacement copy.
- **Market intelligence panel**: queues background jobs that discover public store URLs, crawl bounded pages (robots.txt, throttling, depth budget), extract product data, and persist scored opportunities with source evidence. Scraped text is treated as untrusted evidence, never instructions.
- **Auth and roles**: password sessions via `/auth/*`, server-side roles `admin`, `manager`, `analyst`, `viewer`, manager-only audit log at `/auth/audit`. Optional `X-API-Key` for machine clients when `API_KEY` is set.
- **Observability**: an ordered `agent_steps` trail per run, SSE event stream, Prometheus-style counters at `/metrics`, correlation IDs on responses.
- **MCP server** over stdio exposing the same tool registry (`python -m mcp_server`).

## Tech used

Backend: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite by default (PostgreSQL via `DATABASE_URL`), httpx, pytest.
Frontend: Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS v4.
AI: Ollama by default behind an `LLMProvider` interface; OpenAI-compatible backends supported.

## How to run it

Docker (full stack):

```bash
docker compose up --build
```

Starts PostgreSQL, the API on :8000 and the console on :3000. Migrations and a demo seed run on start. Ollama runs on the host: `ollama pull llama3.2` then `ollama serve`. Everything except answering agent prompts works without it.

Local without Docker (two terminals):

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m app.seed
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000

npm install
npm run dev
```

Open http://localhost:3000. On Windows, `scripts/start.ps1` does both halves and creates a local admin account for the console.

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest     # 170 tests (168 passed, 2 skipped), scripted LLM provider, no Ollama or network needed
npm run lint && npx tsc --noEmit && npm run build
npm test                                         # Playwright smoke, needs a running seeded app
```

## Known limits

- Small local models (e.g. `llama3.2`) occasionally make redundant tool calls; the loop limits contain it and a recovery path parses tool calls emitted as plain text.
- No token streaming: events stream, but the final answer arrives whole.
- Approvals are all-or-nothing: approve or reject, then re-run. You cannot edit the proposed text first.
- The catalog provider is a mock over seeded SQLite data. A Shopify provider would implement one interface, but it does not exist yet.
- Research depth is deliberately shallow: bounded crawls of the public web.
