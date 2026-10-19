# Agent evaluation tasks

`tasks.json` holds 30 tasks for the Ares agent. Each task is a user request plus what a good run looks like:
which tools must or must not be called, whether the run must stop for human approval, which phrases the answer
must contain, and a tool budget. Every task also carries an oracle script: the ideal tool calls and answer. Running
the suite with the oracle provider must score 30 out of 30, which proves the tasks and the scoring agree.

The facts in the expected answers come from the demo seed (`python -m app.seed`). See docs/EVALS.md.
