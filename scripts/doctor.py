"""Environment diagnostic for the E-Commerce Agent.

Reports what is available on this machine and what blocks each deployment
mode. Every check has an explicit status and an actionable fix; nothing is
silently skipped.

Usage:
    python scripts/doctor.py            # human-readable report
    python scripts/doctor.py --json     # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

OK = "PASS"
FAIL = "FAIL"
WARN = "WARN"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool | None, detail: str = "", fix: str = "") -> None:
        if ok is True:
            status = OK
        elif ok is False:
            status = FAIL
        else:
            status = WARN
        self.checks.append(Check(name, status, detail, fix))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]


def check_python(report: Report) -> None:
    version = sys.version_info
    ok = version >= (3, 12)
    report.add("Python", ok, f"{version.major}.{version.minor}.{version.micro}",
               "" if ok else "Install Python 3.12+")


def check_command(report: Report, name: str, command: str, fix: str,
                  required_for: str) -> bool:
    path = shutil.which(command)
    if path:
        report.add(name, True, path)
        return True
    report.add(name, False, "not found", f"{fix} (required for {required_for})")
    return False


def check_port_free(report: Report, port: int, label: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        in_use = sock.connect_ex(("127.0.0.1", port)) == 0
    if in_use:
        report.add(f"Port {port} ({label})", None, "in use, service may already be running",
                   "Stop the existing process or change the port")
    else:
        report.add(f"Port {port} ({label})", True, "available")


def check_redis_daemon(report: Report) -> bool:
    """A real networked Redis, not fakeredis."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        report.add("Redis daemon", None, "REDIS_URL not configured",
                   "Set REDIS_URL (e.g. redis://localhost:6379/0) or use Docker Compose")
        return False
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        report.add("Redis daemon", True, url)
        return True
    except ImportError:
        report.add("Redis daemon", False, "redis package not installed",
                   "pip install -r backend/requirements.txt")
        return False
    except Exception as exc:  # noqa: BLE001
        report.add("Redis daemon", False, f"unreachable at {url}: {exc}",
                   "Start Redis or correct REDIS_URL")
        return False


def check_ollama(report: Report) -> None:
    try:
        import httpx

        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        response = httpx.get(f"{base}/api/tags", timeout=2)
        models = [m.get("name") for m in response.json().get("models", [])]
        model = os.environ.get("OLLAMA_MODEL", "llama3.2")
        if any(model in (m or "") for m in models):
            report.add("Ollama", True, f"{base}; model '{model}' present")
        else:
            report.add("Ollama", None, f"reachable but model '{model}' not pulled",
                       f"ollama pull {model}")
    except Exception:  # noqa: BLE001
        report.add("Ollama", None, "unreachable, AI agent features unavailable",
                   "Install Ollama and run: ollama pull llama3.2 (optional for development)")


def _run_probe(python_exe: str, code: str) -> bool:
    """Run an import probe without shell redirection (Windows-safe)."""
    import subprocess

    result = subprocess.run(
        [python_exe, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        timeout=60,
    )
    return result.returncode == 0


def check_project(report: Report) -> dict[str, bool]:
    results: dict[str, bool] = {}

    # Dependencies importable from the backend venv.
    venv_python = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    code = _run_probe(python_exe, "import fastapi, sqlalchemy, alembic, pydantic")
    report.add("Backend dependencies", code, "" if code else "imports failed",
               "cd backend && pip install -r requirements-dev.txt")
    results["dependencies"] = code

    # Application imports.
    code = _run_probe(python_exe, "from app.main import app")
    report.add("Application import", code, "" if code else "app.main failed to import",
               "Run backend tests for the exact error: cd backend && pytest")
    results["app_import"] = code

    # Database reachable + migration state.
    try:
        sys.path.insert(0, str(BACKEND_DIR))
        from alembic.config import Config
        from alembic import script

        from app.config import settings as app_settings

        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", app_settings.database_url)
        script_dir = script.ScriptDirectory(
            str(BACKEND_DIR / "alembic"), config  # noqa: COM812
        )
        head = script_dir.get_current_head()
        report.add("Migrations present", head is not None, f"head={head}")
        results["migrations"] = head is not None
    except Exception as exc:  # noqa: BLE001
        report.add("Migrations present", False, str(exc)[:120],
                   "cd backend && python -m app.seed --keep")
        results["migrations"] = False
    finally:
        sys.path.pop(0)

    # Writable data directory.
    try:
        with tempfile.NamedTemporaryFile(dir=BACKEND_DIR, delete=True):
            report.add("Writable data directory", True, str(BACKEND_DIR))
    except OSError as exc:
        report.add("Writable data directory", False, str(exc),
                   "Grant write access to the backend directory")

    # Frontend dependencies.
    node_modules = REPO_ROOT / "node_modules"
    report.add("Frontend dependencies", node_modules.exists(),
               "" if node_modules.exists() else "node_modules missing",
               "npm install")

    # Environment examples.
    for example in (".env.example", ".env.production.example"):
        path = REPO_ROOT / example
        report.add(f"{example}", path.exists(), "" if path.exists() else "missing")

    return results


def hardware_summary() -> list[tuple[str, str]]:
    rows = [("CPU", platform.processor() or platform.machine()),
            ("RAM", _ram_human())]
    gpu = _gpu()
    if gpu:
        rows.append(("GPU", gpu))
    return rows


def _ram_human() -> str:
    try:
        import psutil

        return f"{psutil.virtual_memory().total / (1024 ** 3):.0f} GB"
    except ImportError:
        return "unknown (install psutil for this detail)"


def _gpu() -> str | None:
    if shutil.which("nvidia-smi"):
        return "NVIDIA (nvidia-smi detected)"
    return None


def classify(report: Report) -> str:
    names = {check.name for check in report.checks}
    docker_ok = any(c.name == "Docker" and c.status == OK for c in report.checks)
    redis_ok = any(c.name == "Redis daemon" and c.status == OK for c in report.checks)
    dev_blockers = [c for c in report.failures
                    if c.name not in {"Docker", "Docker Compose", "Redis daemon"}]
    if dev_blockers:
        return "NOT READY: resolve FAIL items above"
    if docker_ok and redis_ok:
        return "PRODUCTION VERIFICATION POSSIBLE on this machine"
    blocked = []
    if not docker_ok:
        blocked.append("Docker")
    if not redis_ok:
        blocked.append("Redis")
    return f"DEVELOPMENT READY: production verification BLOCKED by missing {', '.join(blocked)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="E-Commerce Agent environment doctor")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    report = Report()
    print("E-Commerce Agent Doctor\n", file=sys.stderr)

    check_python(report)
    has_docker = check_command(report, "Docker", "docker",
                               "Install Docker Desktop", "production verification")
    check_command(report, "Docker Compose", "docker",
                  "Included with Docker Desktop", "production verification")
    redis_ok = check_redis_daemon(report)
    check_ollama(report)

    project = check_project(report)
    check_port_free(report, 8000, "API")
    check_port_free(report, 3000, "frontend")

    for label, value in hardware_summary():
        report.add(f"Hardware · {label}", None, value)

    result = classify(report)

    if args.json:
        print(json.dumps({
            "result": result,
            "checks": [{"name": c.name, "status": c.status,
                        "detail": c.detail, "fix": c.fix}
                       for c in report.checks],
        }, indent=2))
    else:
        width = max(len(c.name) for c in report.checks)
        for check in report.checks:
            symbol = {"PASS": "+", "FAIL": "x", "WARN": "!"}[check.status]
            line = f"[{symbol}] {check.name.ljust(width)}  {check.status}"
            if check.detail:
                line += f"  {check.detail}"
            print(line)
            if check.status != OK and check.fix:
                print(f"      fix: {check.fix}")
        print(f"\nResult: {result}")

    return 1 if project.get("app_import") is False else 0


if __name__ == "__main__":
    sys.exit(main())
