# Architecture

Ares is an internal operations agent for an e-commerce catalog: a FastAPI backend runs an LLM agent that reads from the store, drafts changes and pauses for human approval before any write. A Next.js console provides login, run monitoring and admin views over the same API. The stack persists everything in PostgreSQL or SQLite through SQLAlchemy.

## Module map

| Module | Responsibility |
| --- | --- |
| backend/app/main.py | FastAPI application factory that wires middleware, routers, error handlers and the /metrics endpoint behind an optional global API key gate. |
| backend/app/agent/engine.py | Bounded agent loop that persists every step to the run row and pauses for human approval before any write tool executes. |
| backend/app/agent/runner.py | Worker pool that starts or resumes runs outside the request cycle so each run gets its own database session. |
| backend/app/agent/grounding.py | Tool-call state machine plus a policy that replaces fabricated operational facts with an honest safe-failure message when every tool call failed. |
| backend/app/tools/registry.py | Declarative tool registry providing pydantic-validated inputs, READ/WRITE access levels and the single execution path for all tools. |
| backend/app/tools/content.py | Content tools: deterministic product-content scoring plus LLM-drafted descriptions and titles that return proposals only. |
| backend/app/llm/base.py | Provider-agnostic LLM types (Message, ToolCall, LLMProvider) so the engine never imports a concrete model backend. |
| backend/app/llm/factory.py | Builds the configured provider (openai_compatible, ollama or test) with a process-wide override hook used by tests. |
| backend/app/intelligence/crawler.py | Robots-aware, SSRF-guarded HTTP crawler that fetches public pages and extracts products, links and metadata. |
| backend/app/jobs/queue.py | Durable database-backed job queue with atomic cross-process claiming, per-tenant idempotency keys and bounded retries. |
| backend/app/api/approvals.py | Approval endpoints that list pending decisions and hand approved or rejected runs back to a worker without mutating anything themselves. |
| backend/app/api/auth.py | Identity endpoints (register, login, logout, me, audit) with session tokens, shared API-key machine users and role-based access control. |
| backend/app/observability | Metrics counters, request instrumentation middleware and tracing helpers that expose Prometheus-format /metrics. |
| backend/app/evals/runner.py | Planned: executes the eval task suite against a provider and scores each task (see backend/evals/README.md). |
| backend/mcp_server.py | MCP stdio transport exposing the same registry tools to external clients, with update_product as a direct write for human-driven sessions. |
| src/app | Next.js console with login page, admin dashboard and auth context that talks to the FastAPI backend. |
