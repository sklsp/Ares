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

