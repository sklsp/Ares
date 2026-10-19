# Agent evaluation

Ares ships with an offline eval suite for the agent engine. It runs 30 realistic
store-owner tasks through the real run loop and scores each outcome against what a
good answer looks like, so regressions in tool use or answer quality are caught
before a model change reaches production.

## Why

The agent's behavior depends on an LLM that is not under our control: unit tests
can verify the engine mechanics, but they cannot tell us whether the model picks
the right tools, stays within budget, and answers with real numbers instead of
invented ones. The eval suite closes that gap by measuring end-to-end outcomes.
It also gives every future model a common yardstick: run the same 30 tasks and
compare pass rates.

## The tasks

The task file is `backend/evals/tasks.json` (version 1). It holds 30 tasks in six
categories: inventory, sales, content, catalog, approval and safety. Each task has
a user prompt plus the expectations of a good run: which tools must or must not be
called (`expect_any`, `expect_all`, `forbid`), whether the run must stop for human
approval (`expect_approval`), phrases the answer must contain or avoid
(`answer_any`, `answer_none`) and a tool budget (`max_tool_calls`). Every task also
carries an oracle script: the ideal tool calls and answer, used by the built-in
oracle provider.

`load_tasks()` validates the file before any run starts: ids must be unique, every
tool name must exist in the live registry, and approval tasks must expect
`update_product`. The facts the answers rely on come from the demo seed
(`python -m app.seed`, fixed random seed 42), so a fresh database always yields the
same numbers.

## The checks

Each outcome is scored by eight checks, run in this order:

- `expect_any`: at least one of the listed tools was called; catches an agent that skips the lookup it needs.
- `expect_all`: every listed tool was called; catches a multi-step workflow left half done.
- `forbid`: none of the listed tools was called; catches off-task or overreaching calls, such as writing to the catalog on a read-only question.
- `approval`: write tasks must stop at `WAITING_FOR_APPROVAL` and read-only tasks must not; catches a write that skips the safety gate, or a false pause.
- `answer_any`: for non-approval tasks, the final answer contains at least one expected phrase; catches an empty or off-topic answer.
- `answer_none`: the final answer contains none of the forbidden phrases; catches naming a product the data does not support (for example GYM-003 on inv-04).
- `grounded`: every SKU-shaped token in the final answer must exist in the seeded store or appear in the prompt itself; catches invented product codes.
- `tool_budget`: total tool calls stay within `max_tool_calls`; catches an agent that loops on retries.

## The oracle and the lazy baseline

Two built-in providers need no model server:

- **oracle** (`--provider oracle`): replays each task's own script, calling exactly
  the tools in its `oracle` field with the recorded arguments (an `@id:SKU` token is
  replaced by that product's real id at run time) and answering with the recorded
  `oracle_answer`. It must score 100%; anything less means the harness or the
  scoring is broken, not the model.
- **lazy** (`--provider lazy`): never calls a tool and always answers "I am not
  sure." It should score near zero; if it passes tasks, the checks are too weak.

Together they bracket any real model: oracle is the ceiling the harness can reach,
lazy is the floor an unhelpful answer must not clear.

## Run it against a real model

Point the CLI at any OpenAI-compatible server or at Ollama:

```text
.venv/Scripts/python -m app.evals --provider openai --base-url http://localhost:1234/v1 --model <model name>
.venv/Scripts/python -m app.evals --provider ollama --model llama3.2
```

The first command targets LM Studio's OpenAI-compatible server; replace `<model
name>` with the model loaded in LM Studio. The run seeds a fresh temporary
database, swaps the three sandbox-escaping tools for offline stubs, runs all 30
tasks and writes JSON plus Markdown reports to `backend/evals/results/`.

## Results

Committed baselines from `backend/evals/results/`:

| provider | passed |
|---|---|
| oracle-scripted | 30/30 (100.0%) |
| lazy-scripted | 0/30 (0.0%) |

No real-model results are committed yet.

## Limits

- The suite has 30 tasks; it samples the agent's behavior, it does not cover every tool or edge case.
- Facts depend on the demo seed: answers are only correct against the seeded store, and a different seed changes the expected numbers.
- Answer checks are substring matches, so they catch missing or invented facts but cannot judge phrasing beyond the listed phrases.
- Each task runs once per provider, so a model's randomness is not measured; a flaky model can pass by luck or fail on an unlucky sample.
- The oracle proves the harness, not a model: its 100% score says the engine and scoring work end to end, nothing about any real LLM's quality.
