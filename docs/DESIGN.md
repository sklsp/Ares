# Design decisions and trade-offs

Why Ares is built the way it is, where each decision lives in the code, and what it costs.

## In one minute

Ares is an operations console for a web shop: you ask for something in plain language, and an LLM agent plans it,
calls tools against the catalog and stops for human approval before it changes anything. The safety boundary is
one field on each tool, read or write, enforced by the engine and nowhere else. Every step is stored, so a run can
be audited and resumed after an approval. The agent is tested without a model through a scripted provider, and
judged with an eval set of 30 tasks whose own oracle must score 30 out of 30.

## How a request becomes an answer

1. `start_run` in `backend/app/api/agent.py` receives POST /agent/run behind a per-minute rate limit, calls `create_run` and then `submit_run`, and returns HTTP 202 with the run's id immediately; the LLM work never happens inside the request.
2. `create_run` in `backend/app/agent/runner.py` persists an AgentRun row with status RUNNING and empty message and pending-tool-call lists, so the run survives a crash before it even starts.
3. `submit_run` hands the run id to a small ThreadPoolExecutor (or runs inline when agent_run_inline is set for tests); each worker's `_with_run` opens its own database session and calls `engine.start(run)`, because sessions are never shared across threads.
4. `start` in `backend/app/agent/engine.py` builds the conversation from the system prompt plus the user request, records it as steps, and enters `_loop`.
5. Each iteration of `_loop` asks the LLM provider (built by `build_llm_provider`); when it returns no tool calls, the answer goes through the grounding gate (`classify_response` in `backend/app/agent/grounding.py`) and either completes the run via `_complete` with status COMPLETED or is rejected and fed back into the tool loop until retries are exhausted, at which point `_fail` runs.
6. When it does return tool calls, `_drain_pending` executes every READ tool at once through `_execute` (the registry's single execution path) and records each outcome with `_finish_call`; any WRITE tool goes to `_request_approval`, which stores an ApprovalRequest row, flips the run to WAITING_FOR_APPROVAL and stops the loop until a human resolves it.
7. After approval or rejection, `submit_resume` in `backend/app/agent/runner.py` puts the run back on the pool and `engine.resume` continues `_loop` with the stored result; meanwhile every step is committed as an AgentStep row by `_record`, which is what lets `_event_stream` in `backend/app/api/agent.py` poll the table and stream progress over SSE until the run finishes or pauses.

## Decisions

### 1. One safety boundary: read or write

**Decision:** Every tool declares exactly one access class, READ or WRITE, on its registry entry, and only WRITE tools are held for human approval before they execute.
**Why:** A single field checked in one place is easy to audit and hard to bypass, so no tool can change store data without a person saying yes first.
**Trade-off:** The classification is binary: a tool that both reads and writes must be WRITE, so even mostly-read operations pause for approval.
**Where:** `backend/app/tools/registry.py` (ToolAccess), `backend/app/agent/engine.py` (_drain_pending).

### 2. A bounded loop with an explicit stop

**Decision:** The engine's _loop runs at most agent_max_iterations LLM turns per run; when the cap is hit it records a DECISION step saying it is stopping early and generates one final answer with no tools offered, completing the run instead of failing it.
**Why:** A model that keeps calling tools must not be able to spin forever or burn budget; ending with an honest "stopping now" answer keeps the run COMPLETED and auditable rather than leaving a dangling FAILED state.
**Trade-off:** The cap is global per run, not per task: a legitimately long multi-tool job can hit it before finishing its work, so the number has to be chosen against the longest expected chain (the default is 10).
**Where:** `backend/app/agent/engine.py` (_loop), `backend/app/config.py` (agent_max_iterations).

### 3. Run state lives in the database, not in memory

**Decision:** An AgentRun row carries status, message and pending tool-call lists; every action is an AgentStep row with a per-run sequence number; approvals are ApprovalRequest rows; all of it is committed as it happens, so a run can be resumed from the database after a crash or a restart.
**Why:** The LLM work runs on a thread pool that dies with the process; if state lived in memory, every restart would orphan running and waiting runs, and there would be nothing to audit afterwards.
**Trade-off:** Every step is a write, so a chatty run touches the database often, and resuming requires re-reading the stored message list rather than keeping it in a variable.
**Where:** `backend/app/db/models.py` (AgentRun, AgentStep, ApprovalRequest), `backend/app/agent/engine.py` (_record).

### 4. Content quality is scored by Python, not by the model

**Decision:** analyze_product_content scores each product's description with deterministic rules in content_quality.py (length, structure, keyword coverage) and returns a ranked list; generate_product_description then asks the LLM to rewrite only the weakest one, and update_product applies it behind approval.
**Why:** A rule-based score is stable, cheap and explainable: the agent can say "this description scored 0.4 because it has no structure" without asking a model to grade itself, which would drift between runs.
**Trade-off:** The rules measure surface features (length, headings, keywords), not whether the copy actually sells; a long, well-structured but boring description scores high.
**Where:** `backend/app/tools/content.py` (analyze_product_content, generate_product_description), `backend/app/services/content_quality.py`.

### 5. One LLMProvider interface, built by one factory

**Decision:** The engine only ever calls an LLMProvider with chat(messages, tools); build_llm_provider in llm/factory.py decides which concrete provider to return from settings (Ollama or OpenAI-compatible), and tests swap the whole thing out through set_llm_override.
**Why:** Keeping the model behind one small interface means the agent loop, its tests and its evals never care which model is running, and a new backend is a new file plus one factory branch.
**Trade-off:** The interface only exposes what the engine needs: provider-specific features such as streaming or function-calling quirks have to be handled inside each provider rather than surfaced to callers.
**Where:** `backend/app/llm/base.py` (LLMProvider), `backend/app/llm/factory.py` (build_llm_provider).

### 6. A scripted FakeLLM in the tests, so the suite never needs a model

**Decision:** The test suite drives the real agent engine with a scripted FakeLLM that returns queued responses in order; conftest installs it process-wide through set_llm_override, so no model server is ever started.
**Why:** Agent behaviour such as tool chains, approval pauses and grounding rejections can be asserted deterministically, and failure modes like an unreachable model are testable by flipping one flag on the fake.
**Trade-off:** The fake only replays what a test queues; it cannot catch a prompt that a real model would misread, so prompt quality still needs live runs to judge.
**Where:** `backend/tests/fakes.py` (FakeLLM), `backend/tests/conftest.py`.

### 7. Crawled text is evidence, never instructions

**Decision:** The system prompt tells the agent to treat scraped text as untrusted data and never as instructions, and the pipeline keeps it that way by turning crawled pages into structured product records instead of passing free page text along in the conversation.
**Why:** Crawled pages are controlled by whoever runs the shop being researched; if their words could steer the agent, a malicious product page could issue tool calls on its own.
**Trade-off:** The guard is prompt-level plus structuring: no code check blocks a tool call merely because crawled text suggested it, so a model that obeys page instructions over the system prompt would still act.
**Where:** `backend/app/agent/prompts.py` (system_prompt), `backend/app/intelligence/extraction.py`.

### 8. An ordered step trail per run, used for the audit view and the event stream

**Decision:** Every action of a run is appended as an AgentStep row with a per-run sequence number and committed as it happens; the run detail endpoint returns that trail for auditing, and the SSE stream polls the same table for new rows.
**Why:** One append-only record serves two consumers at once: people reviewing what happened after the fact, and clients following along live without any in-memory state on the server.
**Trade-off:** The stream is a database poll with a cursor on step id rather than a push channel, so event latency is bounded by the poll interval and each client holds its own cursor for the life of the connection.
**Where:** `backend/app/agent/engine.py` (_record), `backend/app/api/agent.py` (_event_stream).

### 9. A recovery path for small models that write tool calls as plain text

**Decision:** When Ollama returns no structured tool calls but its message contains one, _recover_tool_call_from_text parses JSON blocks from the text and accepts a block whose tool name is in the registry; the recovered call replaces the text so the engine sees an ordinary tool decision.
**Why:** Smaller local models often write the call as prose or a code fence instead of using the tool-call field, and recovering it keeps those models usable rather than failing the whole run.
**Trade-off:** Recovery exists only in the Ollama provider: the same behaviour through an OpenAI-compatible backend is not recovered, and a block that names a real tool but carries bad arguments still goes through normal validation like any other call.
**Where:** `backend/app/llm/ollama.py` (_recover_tool_call_from_text).

### 10. Agent evals with an oracle script per task, so the eval set itself is tested

**Decision:** Each of the 30 tasks in backend/evals/tasks.json records its expectations plus an oracle script (the ideal tool calls and answer), and load_tasks validates every task against the live tool registry before it can be used.
**Why:** The eval set is data that drifts as tools change; validating it in code makes the suite fail on unknown tool names, duplicate ids or approval tasks that never touch update_product instead of silently scoring them wrong.
**Trade-off:** Validation only checks structure: nothing proves an oracle answer still matches the seeded store facts, so a stale expected phrase passes loading and only fails when the task is actually run.
**Where:** `backend/app/evals/tasks.py` (load_tasks), `backend/tests/test_eval_tasks.py`.
