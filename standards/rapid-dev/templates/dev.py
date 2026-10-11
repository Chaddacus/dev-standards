#!/usr/bin/env python3
"""Standard task runner (rapid-dev standard). Same commands in every repository, on every OS.

  python dev.py setup     install toolchain dependencies from lockfiles (idempotent)
  python dev.py dev       run the app with hot reload
  python dev.py fast      FAST checks: lint, types, unit tests (seconds; pre-push hook runs this)
  python dev.py test      full test suite
  python dev.py check     everything CI runs
  python dev.py logs      follow the running app's logs
  python dev.py deploy    build, release, health-check, roll back on failure (owner-approved)
  python dev.py rollback  return to the previous release

Edit TASKS below. A task is a list of shell commands run in order from the repository root;
the first failure stops it. Use "@other" to run another task. Remove tasks that do not apply.
"{py}" expands to the project's .venv Python (created by setup) or, before setup, this Python.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

TASKS = {
    "setup": ["@venv", "{py} -m pip install -q -e .[dev]"],
    "venv": [f"\"{sys.executable}\" -m venv .venv" if not VENV_PY.exists() else "echo .venv ok"],
    "dev": ["{py} -m watchfiles \"{py} -m app\" src"],
    "fast": ["{py} -m ruff check .", "{py} -m pytest -q -x tests/unit"],
    "test": ["{py} -m pytest -q"],
    "check": ["@fast", "@test"],
    "logs": ["docker compose logs -f --tail 200"],
    "deploy": ["echo 'deploy: define build, release, health check and rollback for this project' && exit 1"],
    "rollback": ["echo 'rollback: define for this project' && exit 1"],
}


def run(name, seen=()):
    if name not in TASKS:
        sys.exit(f"unknown task '{name}'. Tasks: {', '.join(TASKS)}")
    if name in seen:
        sys.exit(f"task loop at '{name}'")
    env = {**os.environ, "PYTHONUTF8": "1"}
    for cmd in TASKS[name]:
        if cmd.startswith("@"):
            run(cmd[1:], seen + (name,))
            continue
        py = f"\"{VENV_PY}\"" if VENV_PY.exists() else f"\"{sys.executable}\""
        cmd = cmd.replace("{py}", py)
        print(f"[{name}] {cmd}", flush=True)
        code = subprocess.call(cmd, shell=True, cwd=ROOT, env=env)
        if code != 0:
            sys.exit(code)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "--list"):
        print(__doc__.strip())
        print("\nTasks:", ", ".join(TASKS))
        sys.exit(0)
    for task in sys.argv[1:]:
        run(task)
