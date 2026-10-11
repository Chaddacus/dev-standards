---
name: new-project
description: Start a new project that follows the rapid-dev standard from the first commit - dev.py commands, hot reload, FAST checks, CI, baseline, GitHub repo. Use whenever the user wants a new app, service, library, tool or site.
---

# New project

`python ~/.claude/skills/new-project/scaffold.py <name> --stack <stack> [--owner X] [--dir D] --create`

Stacks (all verified 2026-10-11: setup and check pass from a clean scaffold; the service's hot
reload was checked live):
- `python-service` — Starlette + uvicorn, `/healthz`, unit test, multi-stage Dockerfile (dev stage
  reloads), `compose.yml` + `compose.dev.yml` with `develop.watch`, `.env.example`.
- `python-lib` — package + core module + unit test; `dev` watches and reruns tests.
- `web` — Vite + React + TypeScript (`npm create vite@7`), vitest, typecheck, lint.

Every stack gets: `dev.py`, `.claude/verification.json` (FAST = `python dev.py fast`), CI
(shared `dev-standards` workflow where the repository can call it, inline copy otherwise),
CLAUDE.md map, pinned toolchain, `.gitignore`.

`--create` makes a private GitHub repository (standing authorization), pushes, and applies the
repo baseline. After the first CI run passes, add full protection:
`python ~/.claude/skills/repo-baseline/baseline.py protect --full --check "checks / check" --repo <owner>/<name>`
(inline CI: `--check check`).

Other stacks (MCP server, Unreal plugin, static site): start from the closest stack, then follow
`~/.claude/skills/rapid-dev/SKILL.md`; when one is built twice, add it to `scaffold.py`
(global-first).
