#!/usr/bin/env python3
"""Scaffold a new project that follows the rapid-dev standard from the first commit.

  scaffold.py <name> --stack python-service|python-lib|web [--owner Chaddacus] [--dir ~/code/personal]
              [--create] [--public]

Writes the project, runs `python dev.py setup` and `python dev.py check`, commits, and with
--create makes the GitHub repository (private unless --public), pushes, and applies the repo
baseline. Prints the project path.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent / "rapid-dev" / "templates"
BASELINE = HERE.parent / "repo-baseline" / "baseline.py"
SHARED_CI = "Chaddacus/dev-standards/.github/workflows/check.yml@v1"


def sh(cmd, cwd, check=True, env=None):
    """Run a shell command. Pass extra variables through env, never as a `VAR=x cmd` prefix
    (that form fails in cmd.exe, the Windows shell)."""
    print(f"$ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, cwd=cwd, env={**os.environ, **env} if env else None)
    if check and r.returncode != 0:
        sys.exit(f"failed: {cmd}")
    return r.returncode


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.lstrip("\n"), encoding="utf-8", newline="\n")


def dev_py(tasks):
    s = (TEMPLATES / "dev.py").read_text(encoding="utf-8")
    body = "TASKS = {\n" + "".join(f"    {json.dumps(k)}: {json.dumps(v)},\n" for k, v in tasks.items()) + "}"
    return re.sub(r"TASKS = \{.*?\n\}", lambda _: body, s, count=1, flags=re.S)


def ci_yaml(owner, public, python=None, node=None, setup=None, check=None):
    if owner.lower() == "chaddacus" and not public:
        withs = []
        if python:
            withs.append(f'      python-version: "{python}"')
        if node:
            withs.append(f'      node-version: "{node}"')
        if setup:
            withs.append(f"      setup-command: {setup}")
        if check:
            withs.append(f"      check-command: {check}")
        return f"""
name: checks

# Minimal by design: one job, pull requests only (main changes only through checked PRs).
on:
  pull_request:

jobs:
  checks:
    uses: {SHARED_CI}
    with:
{chr(10).join(withs)}
"""
    steps = ["      - uses: actions/checkout@v4"]
    if python:
        steps += ["      - uses: actions/setup-python@v5", f'        with: {{python-version: "{python}"}}']
    if node:
        steps += ["      - uses: actions/setup-node@v4", f'        with: {{node-version: "{node}", cache: npm}}']
    steps += [f"      - run: {setup or 'python dev.py setup'}", f"      - run: {check or 'python dev.py check'}"]
    return f"""
# Inline copy of the shared check (this repository cannot call {SHARED_CI.split('/.github')[0]}).
name: checks

# Minimal by design: one job, pull requests only (main changes only through checked PRs).
on:
  pull_request:

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    env:
      PYTHONUTF8: "1"
    steps:
{chr(10).join(steps)}
"""


COMMON_GITIGNORE = """
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
.ruff_cache/
.coverage
coverage.xml
node_modules/
dist/
.env
.env.*
!.env.example
"""


def python_project(root, name, pkg, service):
    deps = '["starlette>=0.47", "uvicorn>=0.35"]' if service else "[]"
    # ruff pinned exactly: rule sets change between releases, so a range lets local and CI disagree
    dev = '["pytest>=8", "ruff==0.17.0", "watchfiles>=1.1"' + (', "httpx>=0.28"' if service else "") + "]"
    write(root, "pyproject.toml", f"""
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = {deps}

[project.optional-dependencies]
dev = {dev}

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{pkg}"]

[tool.ruff]
line-length = 110
src = ["src", "tests"]

[tool.ruff.lint]
# Windows git cannot track the executable bit, so EXE001 fails on Linux CI for new shebang scripts.
extend-ignore = ["EXE001"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
""")
    write(root, ".python-version", "3.12\n")
    write(root, f"src/{pkg}/__init__.py", f'"""{name}."""\n')
    if service:
        write(root, f"src/{pkg}/app.py", '''
"""HTTP app. Keep logic in plain modules; this file only wires routes."""
import os

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


async def healthz(request):
    return JSONResponse({"status": "ok", "env": os.environ.get("APP_ENV", "development")})


app = Starlette(routes=[Route("/healthz", healthz)])
''')
        write(root, "tests/unit/test_health.py", f'''
from starlette.testclient import TestClient

from {pkg}.app import app


def test_healthz_reports_ok():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
''')
        write(root, "Dockerfile", f"""
FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

FROM base AS dev
RUN pip install --no-cache-dir watchfiles
CMD ["uvicorn", "{pkg}.app:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--app-dir", "src"]

FROM base AS prod
CMD ["uvicorn", "{pkg}.app:app", "--host", "0.0.0.0", "--port", "8000"]
""")
        write(root, "compose.yml", f"""
services:
  app:
    build:
      context: .
      target: prod
    image: {name}:${{GIT_SHA:-local}}
    ports: ["8000:8000"]
    env_file: [{{path: .env, required: false}}]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"]
      interval: 10s
      retries: 3
""")
        cd = (TEMPLATES / "compose.dev.yml").read_text(encoding="utf-8")
        cd = cd.replace("./src\n          target: /app/src", "./src\n          target: /app/src")
        write(root, "compose.dev.yml", cd)
        write(root, ".env.example", "APP_ENV=development\n")
        tasks = {
            "setup": ["@venv", "{py} -m pip install -q -e .[dev]"],
            "venv": [],
            # watchfiles restarts the whole server; uvicorn --reload hangs on Windows (old worker keeps serving).
            "dev": [f"{{py}} -m watchfiles --filter python \"{{py}} -m uvicorn {pkg}.app:app --app-dir src --port 8000\" src"],
            "dev-docker": ["docker compose -f compose.yml -f compose.dev.yml up --watch"],
            "fast": ["{py} -m ruff check .", "{py} -m pytest -q -x tests/unit"],
            "test": ["{py} -m pytest -q"],
            "check": ["@fast", "@test"],
            "logs": ["docker compose logs -f --tail 200"],
            "deploy": ["fail: build image tagged with the git SHA, release, check /healthz, roll back on failure"],
            "rollback": ["fail: re-release the previous SHA"],
        }
    else:
        write(root, f"src/{pkg}/core.py", '''
"""Core logic: plain functions, no I/O."""


def greet(name: str) -> str:
    return f"hello, {name}"
''')
        write(root, "tests/unit/test_core.py", f'''
from {pkg}.core import greet


def test_greet():
    assert greet("world") == "hello, world"
''')
        tasks = {
            "setup": ["@venv", "{py} -m pip install -q -e .[dev]"],
            "venv": [],
            "dev": ["{py} -m watchfiles \"{py} -m pytest -q -x tests/unit\" src tests"],
            "fast": ["{py} -m ruff check .", "{py} -m pytest -q -x tests/unit"],
            "test": ["{py} -m pytest -q"],
            "check": ["@fast", "@test"],
        }
    s = dev_py(tasks)
    s = s.replace('    "venv": [],', '    "venv": [f"\\"{sys.executable}\\" -m venv .venv" if not VENV_PY.exists() else "say: .venv ok"],')
    write(root, "dev.py", s)
    return {"python": "3.12"}


def web_project(root, name):
    sh("npm create vite@7 . -- --template react-ts --no-interactive", cwd=root)
    pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
    pkg["name"] = name
    pkg.setdefault("scripts", {}).update({"typecheck": "tsc -b --noEmit", "test": "vitest run --passWithNoTests"})
    pkg.setdefault("devDependencies", {}).update({"vitest": "^3.2.0"})
    pkg["engines"] = {"node": ">=24"}
    (root / "package.json").write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    write(root, ".nvmrc", "24\n")
    write(root, "src/core.ts", "export const greet = (name: string): string => `hello, ${name}`;\n")
    write(root, "src/core.test.ts", 'import { expect, test } from "vitest";\nimport { greet } from "./core";\n\n'
          'test("greet", () => {\n  expect(greet("world")).toBe("hello, world");\n});\n')
    tasks = {
        "setup": ["npm install"],
        "dev": ["npm run dev"],
        "fast": ["npm run lint", "npm run typecheck", "npm test"],
        "test": ["npm test"],
        "check": ["@fast", "npm run build"],
        "deploy": ["fail: build and publish dist/, verify, roll back on failure"],
        "rollback": ["fail: re-publish the previous build"],
    }
    write(root, "dev.py", dev_py(tasks))
    return {"node": "24", "setup": "npm ci"}


def main():
    a = argparse.ArgumentParser()
    a.add_argument("name")
    a.add_argument("--stack", required=True, choices=["python-service", "python-lib", "web"])
    a.add_argument("--owner", default="Chaddacus")
    a.add_argument("--dir", default=str(Path.home() / "code" / "personal"))
    a.add_argument("--create", action="store_true")
    a.add_argument("--public", action="store_true")
    args = a.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.name):
        sys.exit("name: lowercase letters, digits, - and _")
    root = Path(args.dir).expanduser() / args.name
    if root.exists():
        sys.exit(f"{root} already exists")
    root.mkdir(parents=True)
    pkg = args.name.replace("-", "_")
    if args.stack == "web":
        ci = web_project(root, args.name)
    else:
        ci = python_project(root, args.name, pkg, service=args.stack == "python-service")
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    write(root, ".gitignore", existing + COMMON_GITIGNORE)
    # LF everywhere: Windows checkouts with core.autocrlf=true otherwise change file bytes, which breaks
    # content hashes, golden files and shebangs (history-toons, 2026-10-14)
    if not (root / ".gitattributes").exists():
        write(root, ".gitattributes", "* text=auto eol=lf\n*.png binary\n*.jpg binary\n*.wav binary\n"
              "*.mp3 binary\n*.mp4 binary\n*.zip binary\n")
    write(root, "tests/test_dev_py.py", (TEMPLATES / "test_dev_py.py").read_text(encoding="utf-8"))
    devpy = (root / "dev.py").read_text(encoding="utf-8")
    smoke = "{py} tests/test_dev_py.py" if args.stack != "web" else "python tests/test_dev_py.py"
    devpy = devpy.replace('"check": ["@fast"', f'"check": ["{smoke}", "@fast"', 1)
    (root / "dev.py").write_text(devpy, encoding="utf-8", newline="\n")
    write(root, ".claude/verification.json", json.dumps({
        "fast": [{"name": "fast", "command": "python dev.py fast"}],
        "full": [{"name": "check", "command": "python dev.py check"}]}, indent=2) + "\n")
    write(root, ".github/workflows/checks.yml", ci_yaml(args.owner, args.public, **ci))
    write(root, "CLAUDE.md", f"""
# {args.name}

Stack: {args.stack}. Follows the rapid-dev standard (`~/.claude/skills/rapid-dev/SKILL.md`).

| Task | Command |
|---|---|
| Install | `python dev.py setup` |
| Run with hot reload | `python dev.py dev` |
| Fast checks (pre-push) | `python dev.py fast` |
| Everything CI runs | `python dev.py check` |

Layout: logic in plain modules with unit tests under `tests/unit` (Python) or `*.test.ts` (web);
framework and I/O code stays thin.
""")
    write(root, "README.md", f"# {args.name}\n\nSee CLAUDE.md for commands.\n")
    # Official CloudWarriors Task issue form, fetched so every new repository uses the current version
    tmpl = subprocess.run("gh api repos/CloudWarriors-ai/.github/contents/.github/ISSUE_TEMPLATE/task.yml "
                          "-H \"Accept: application/vnd.github.raw\"", shell=True, capture_output=True)
    if tmpl.returncode == 0 and tmpl.stdout.startswith(b"name: Task"):
        p = root / ".github/ISSUE_TEMPLATE/task.yml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(tmpl.stdout)
    else:
        print("WARNING: could not fetch the CloudWarriors Task issue template; add it later", flush=True)
    sh("python dev.py setup", cwd=root)
    sh("python dev.py check", cwd=root)
    # dev.py has a shebang; Windows checkouts lose the executable bit and ruff EXE001 fails on Linux CI
    sh("git init -q -b main && git add -A && git update-index --chmod=+x dev.py && "
       "git commit -q -m \"Scaffold from the rapid-dev standard\"", cwd=root)
    if args.create:
        vis = "--public" if args.public else "--private"
        owner_dir = json.loads((Path.home() / ".claude/ops/config.json").read_text(encoding="utf-8")).get(
            "owner_gh_config_dir")
        owner_env = {"GH_CONFIG_DIR": owner_dir} if owner_dir else None
        sh(f"gh repo create {args.owner}/{args.name} {vis} --source . --push", cwd=root, env=owner_env)
        # Auto-merge and branch cleanup: the standing merge flow uses `gh pr merge --auto`.
        sh(f"gh api -X PATCH repos/{args.owner}/{args.name} -F allow_auto_merge=true "
           f"-F delete_branch_on_merge=true --silent", cwd=root, check=False, env=owner_env)
        agent = json.loads((Path.home() / ".claude/ops/config.json").read_text(encoding="utf-8")).get("agent_login")
        if agent:  # the agent works in every new project with write access, never admin
            sh(f"gh api -X PUT repos/{args.owner}/{args.name}/collaborators/{agent} -f permission=push",
               cwd=root, check=False, env=owner_env)
            # accept as the agent (default gh login); the invite can take a few seconds to appear
            for _ in range(6):
                invites = subprocess.run("gh api user/repository_invitations --jq .[].id", shell=True, cwd=root,
                                         capture_output=True, text=True).stdout.split()
                for inv in invites:
                    sh(f"gh api -X PATCH user/repository_invitations/{inv}", cwd=root, check=False)
                push = subprocess.run(f"gh api repos/{args.owner}/{args.name} --jq .permissions.push", shell=True,
                                      cwd=root, capture_output=True, text=True).stdout.strip()
                if push == "true":
                    break
                time.sleep(5)
            else:
                print(f"WARNING: {agent} has no push access yet; accept the invite before pushing")
        sh(f"python \"{BASELINE}\" apply --repo {args.owner}/{args.name}", cwd=root, check=False)
    print(root)


if __name__ == "__main__":
    main()
