"""Markdown and JSON reports for eval suites."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from app.evals.runner import SuiteReport


def _join(values: list[str]) -> str:
    return ", ".join(values) if values else "-"


def write_suite_report(
    report: SuiteReport, out_dir: Path, meta: dict[str, str]
) -> tuple[Path, Path]:
    """Write ``{name}.json`` and ``{name}.md`` for one suite run."""
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = asdict(report)
    payload.update(meta)
    json_path = out_dir / f"{report.name}.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [f"# Agent eval: {report.name}", ""]
    lines += [
        "| setting | value |",
        "|---|---|",
        f"| provider | {report.provider} |",
        f"| model | {report.model} |",
    ]
    lines += [f"| {key} | {value} |" for key, value in meta.items()]
    lines.append("")
    lines += ["| category | passed |", "|---|---|"]
    lines += [
        f"| {category} | {passed}/{total} |"
        for category, (passed, total) in report.by_category.items()
    ]
    lines.append(
        f"| **total** | **{report.passed}/{report.total} ({report.pass_rate * 100:.1f}%)** |"
    )
    lines += [
        "",
        "## Tasks",
        "",
        "| task | category | result | failed checks | tools called |",
        "|---|---|---|---|---|",
    ]
    for result in report.results:
        verdict = "pass" if result.passed else "FAIL"
        lines.append(
            f"| {result.task_id} | {result.category} | {verdict}"
            f" | {_join(result.failures)} | {_join(result.tools_called)} |"
        )

    md_path = out_dir / f"{report.name}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
