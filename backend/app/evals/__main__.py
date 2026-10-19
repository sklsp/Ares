"""Command-line entry point for eval suites: ``python -m app.evals``.

Arguments are parsed and the environment configured BEFORE anything from
``app`` is imported, so a fresh temporary SQLite database is guaranteed even
when the surrounding shell has its own settings.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections.abc import Callable


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="app.evals", description="Run an Ares agent eval suite.")
    parser.add_argument("--provider", required=True, choices=["oracle", "lazy", "ollama", "openai"])
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--only", default="", help="Comma-separated task ids to run")
    parser.add_argument("--out", default="evals/results")
    parser.add_argument("--min-pass", type=float, default=0.0)
    parser.add_argument("--tasks", default="evals/tasks.json")
    return parser.parse_args(argv)


def _setup_env(args: argparse.Namespace) -> None:
    db_file = os.path.join(tempfile.mkdtemp(prefix="ares-evals-"), "eval.db").replace("\\", "/")
    env = {
        "DATABASE_URL": f"sqlite:///{db_file}",
        "AGENT_RUN_INLINE": "true",
        "EMBEDDED_WORKER": "false",
        "LOG_LEVEL": "WARNING",
    }
    if args.provider == "ollama":
        env["LLM_PROVIDER"] = "ollama"
        if args.model:
            env["OLLAMA_MODEL"] = args.model
        if args.base_url:
            env["OLLAMA_BASE_URL"] = args.base_url
    elif args.provider == "openai":
        env["LLM_PROVIDER"] = "openai_compatible"
        if args.model:
            env["OPENAI_MODEL"] = args.model
        if args.base_url:
            env["OPENAI_BASE_URL"] = args.base_url
        env["OPENAI_API_KEY"] = args.api_key or "not-needed"
    for key, value in env.items():
        os.environ[key] = value


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    _setup_env(args)

    from pathlib import Path  # noqa: E402 - app imports must come after _setup_env

    from app.config import settings  # noqa: E402
    from app.db.base import SessionLocal  # noqa: E402
    from app.evals.providers import LazyProvider, OracleProvider  # noqa: E402
    from app.evals.report import write_suite_report  # noqa: E402
    from app.evals.runner import run_suite, sku_resolver  # noqa: E402
    from app.evals.tasks import EvalTask, load_tasks  # noqa: E402
    from app.llm.base import LLMProvider  # noqa: E402
    from app.llm.factory import build_llm_provider  # noqa: E402
    from app.seed import ensure_schema  # noqa: E402
    from app.tools import get_registry  # noqa: E402

    ensure_schema()
    db = SessionLocal()
    try:
        known_tools = {tool.name for tool in get_registry().list()}
        tasks = load_tasks(Path(args.tasks), known_tools)
        if args.only:
            wanted = {part.strip() for part in args.only.split(",") if part.strip()}
            tasks = [task for task in tasks if task.id in wanted]

        make_provider: Callable[[EvalTask], LLMProvider]
        if args.provider == "oracle":
            make_provider = lambda task: OracleProvider(task, sku_resolver(db))  # noqa: E731
        elif args.provider == "lazy":
            make_provider = lambda task: LazyProvider()  # noqa: E731
        else:
            shared = build_llm_provider(settings)
            make_provider = lambda task: shared  # noqa: E731

        name = _safe_name(f"{args.provider}-{args.model or 'scripted'}")
        report = run_suite(db, tasks, make_provider, name=name)
    finally:
        db.close()

    write_suite_report(report, Path(args.out), {"tasks": str(report.total)})
    rate = report.pass_rate
    print(f"{name}: {report.passed}/{report.total} passed ({rate:.1%})")
    if rate < args.min_pass:
        print(f"pass rate {rate:.1%} is below --min-pass {args.min_pass:.1%}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
