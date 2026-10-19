"""Loading and validation of offline evaluation tasks (evals/tasks.json).

Each task is a user request plus what a good run looks like: which tools must
or must not be called, whether the run must stop for human approval, which
phrases the answer must contain, a tool budget, and an oracle script - the
ideal tool calls and answer (see evals/README.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OracleCall:
    """One ideal tool call in a task's oracle script."""

    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class EvalTask:
    """A user request plus what a good run looks like."""

    id: str
    category: str
    prompt: str
    expect_any: tuple[str, ...]
    expect_all: tuple[str, ...]
    forbid: tuple[str, ...]
    expect_approval: bool
    answer_any: tuple[str, ...]
    answer_none: tuple[str, ...]
    max_tool_calls: int
    oracle: tuple[OracleCall, ...]
    oracle_answer: str


_FIELD_ORDER = tuple(f.name for f in fields(EvalTask))


def load_tasks(path: Path, known_tools: set[str]) -> list[EvalTask]:
    """Read ``{"tasks": [...]}`` from *path* and validate every task.

    Raises ValueError naming the offending task id when a field is missing or
    an id repeats, when a tool name in expect_any/expect_all/forbid or an
    oracle call is not in *known_tools*, or when an approval task does not
    expect update_product.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks: list[EvalTask] = []
    seen_ids: set[str] = set()
    for raw in data["tasks"]:
        task_id = raw.get("id", "<no id>")
        for field_name in _FIELD_ORDER:
            if field_name not in raw:
                raise ValueError(f"{task_id}: missing field '{field_name}'")
        if raw["id"] in seen_ids:
            raise ValueError(f"duplicate task id '{raw['id']}'")
        seen_ids.add(raw["id"])

        for name in (*raw["expect_any"], *raw["expect_all"], *raw["forbid"]):
            if name not in known_tools:
                raise ValueError(f"{task_id}: unknown tool '{name}'")
        oracle = tuple(OracleCall(tool=call["tool"], args=dict(call["args"])) for call in raw["oracle"])
        for call in oracle:
            if call.tool not in known_tools:
                raise ValueError(f"{task_id}: unknown tool '{call.tool}'")

        expect_all = tuple(raw["expect_all"])
        if raw["expect_approval"] and "update_product" not in expect_all:
            raise ValueError(f"{task_id}: an approval task needs update_product in expect_all")

        tasks.append(
            EvalTask(
                id=raw["id"],
                category=raw["category"],
                prompt=raw["prompt"],
                expect_any=tuple(raw["expect_any"]),
                expect_all=expect_all,
                forbid=tuple(raw["forbid"]),
                expect_approval=raw["expect_approval"],
                answer_any=tuple(raw["answer_any"]),
                answer_none=tuple(raw["answer_none"]),
                max_tool_calls=raw["max_tool_calls"],
                oracle=oracle,
                oracle_answer=raw["oracle_answer"],
            )
        )
    return tasks
