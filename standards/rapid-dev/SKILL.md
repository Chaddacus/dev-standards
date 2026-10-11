---
name: rapid-dev
description: The rapid-development standard - how every project is built so a change goes from edit to running, tested and deployed with the smallest delta (standard dev.py commands, hot reload everywhere including Docker, hermetic fast tests, reproducible setup, image-based reversible deploys, Unreal fast-iteration patterns). Use when creating a project, adding a service or container, closing "rapid-dev" baseline gaps, or when a dev loop is slow.
---

# Rapid-development standard

Goal: the time from "edit saved" to "seen working", "tested", and "live" is as short as the stack
allows, and identical in shape across repositories so agents never rediscover how a project runs.
Apply it to new projects from the start and to existing ones through the baseline gaps.

## 1. One command interface

Every repository has `dev.py` at the root (template: `templates/dev.py`, stdlib only, works on
Windows without make). Standard tasks: `setup`, `dev`, `fast`, `test`, `check`, `logs`, `deploy`,
`rollback`. Tasks wrap the stack's real tools (npm scripts, make targets, uvicorn, UBT); keep
those, add the uniform entry point. `.claude/verification.json` `fast` runs `python dev.py fast`;
CI runs `python dev.py check`.

## 2. Hot reload everywhere

The `dev` task starts the app so saved changes appear without a manual restart or rebuild.

| Stack | Reload |
|---|---|
| Vite / React | `vite` HMR; API side `tsx watch` |
| Python HTTP / ASGI | in containers (Linux): `uvicorn --reload`; on Windows hosts: `python -m watchfiles --filter python "python -m uvicorn app:app" src` (uvicorn's own reload leaves the old worker serving on Windows, verified 2026-10-11) |
| Python servers without reload (MCP stdio/HTTP, workers, bots) | `python -m watchfiles "<start command>" src` |
| Static site generators | build watcher + local server with live reload |
| Containers | `compose.dev.yml` override with `develop.watch` (template `templates/compose.dev.yml`): `sync` for code the app reloads itself, `sync+restart` for config, `rebuild` for dependencies; multi-stage Dockerfile with a `dev` target. Run `docker compose -f compose.yml -f compose.dev.yml up --watch`. Never bind-mount over installed dependencies. |
| Unreal C++ | Live Coding in the editor for iteration; incremental UBT builds from persistent worktrees (ops `worktree_pool`) for agents |
| Unreal content and tuning | gameplay numbers in DataAssets / DataTables / config files, not code, so tuning needs no compile; reload them at runtime in development builds |
| CLIs and libraries | test watch: `vitest`, or `python -m watchfiles "python -m pytest -q -x <path>" src tests` |

## 3. Fast, hermetic feedback

- Put logic in an engine- or framework-free core with plain unit tests (Treasure Wake's
  deterministic C++ core plus host tests is the model). UI, engine and I/O stay thin.
- FAST tier under ~60 s: lint, types, unit tests. No network, no paid APIs, no real cloud.
- External services have fakes or recorded fixtures, selected by default in dev and tests (Van
  Quest's fake Meshy provider is the model). Real providers only behind explicit flags.
- Any single test file runs alone in seconds.

## 4. Reproducible setup

- Pin toolchains (`.python-version` or `requires-python`, `engines` / `.nvmrc`, engine version
  file) and commit lockfiles (`uv.lock` / `requirements*.txt` pins, `package-lock.json`).
- `python dev.py setup` works from a clean clone, is safe to re-run, and never needs secrets.
- `.env.example` lists every setting with safe development defaults; real secrets stay outside
  the repository.
- Local dependencies (databases, search, queues) run in compose with a seed task, so development
  never touches production data.

## 5. Fast, reversible delivery (structure; running production deploys needs owner approval)

- Build once, deploy the same artifact: container images tagged with the git SHA.
- `dev.py deploy`: build → push → switch → health check → automatic rollback to the previous SHA
  on failure. `dev.py rollback` returns to the previous release in one step.
- Database changes are backward compatible (expand, migrate, contract) so code and schema can
  ship and roll back independently. Migrations are reversible.
- New behavior can ship dark behind a config or environment flag and be switched without a
  deploy.
- Health endpoint (`/healthz`) on every service; the live monitor watches it.

## 6. Shared pieces and fast validation

- CI calls the shared reusable workflow `Chaddacus/dev-standards/.github/workflows/check.yml@v1`
  (`python dev.py setup` + `python dev.py check`, dependency caching). Improve CI there once.
- Interfaces between repositories (asset specs, API payloads, manifests) are versioned JSON
  schemas owned by the producing repository; both sides validate against the pinned version in
  CI, so a breaking change fails in the repository that made it.
- Web changes get a preview: GitHub Pages per-PR previews where the repository can use Pages,
  otherwise a preview build on the owner's host. The PR links it.
- Unreal: keep the engine's shared local DDC (`%LOCALAPPDATA%/UnrealEngine/Common/DerivedDataCache`,
  shared by every worktree; never point a project at a project-relative DDC); compile through
  persistent worktrees.
- Merges: after the verifier passes, enable GitHub auto-merge so the PR lands the moment CI is
  green. Repositories that opt in (`deploy: auto` in the ops config) deploy right after merge
  through `dev.py deploy` (health check + automatic rollback).

## 7. Small batches

Short-lived branches, small PRs, merge as soon as checks and the verifier pass. Split work that
cannot land in one small PR into independent issues.

## Applying it

- New project: start from the templates; the first PR already has `dev.py`, hot reload, FAST tier,
  pins, `.env.example`, `/healthz`, and CI running `dev.py check`.
- Existing project: close the gaps `baseline.py audit` reports under "rapid-dev", one small PR
  each, keeping the project's existing tools behind the standard commands.
- Record any stack-specific recipe learned while doing this back into this file (global-first).
