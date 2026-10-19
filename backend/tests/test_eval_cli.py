"""The ``python -m app.evals`` command."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, out_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.evals", *args, "--out", str(out_dir)],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_oracle_cli_writes_reports(tmp_path) -> None:
    result = _run_cli("--provider", "oracle", out_dir=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "oracle-scripted: 30/30 passed" in result.stdout
    assert (tmp_path / "oracle-scripted.json").is_file()
    assert (tmp_path / "oracle-scripted.md").is_file()


def test_lazy_cli_fails_min_pass(tmp_path) -> None:
    result = _run_cli("--provider", "lazy", "--min-pass", "0.9", out_dir=tmp_path)
    assert result.returncode == 1
    assert "below --min-pass" in result.stdout
