# Routing module

Chooses model, effort, and environment for each phase of a task, and records why. Model and
effort are separate decisions. A stronger model is an escalation option for capability problems
only (see recovery); it never fixes missing access or unclear requirements.

## Default policy

| Phase / work | Model | Effort |
|---|---|---|
| Fetching, extraction, formatting, status checks, triage, repo maps, digests | haiku | low |
| Planning, architecture, ambiguous requirements, difficult diagnosis | opus | high |
| Bounded implementation against a plan or a clear issue (size <= 3) | sonnet | medium |
| Bounded implementation, size 5 without a plan (should be rare) | sonnet | high |
| Review: docs, config, size <= 2, low consequence | sonnet | low |
| Review: logic changes, size >= 3 | sonnet | medium |
| Review: high consequence (data, deletion, auth/security, money, deploy, > ~300 lines) | opus | high, plus a Codex second opinion |

Overrides: `routing` in `~/.claude/ops/config.json` (task kind → model) wins for implementation;
the weekly improvement pass sets it from measured first-try accuracy. Fable 5.1 is on trial for
the planning phase when the config says `"plan": "fable"`.

## Environment

- `local` runner: anything needing this PC (Unreal, SSH deploys, vault, LFS-heavy repos).
- `cloud` runner: repositories marked `runner: cloud` in the ops config.
- Worktree: pool worktree when the repo has `worktree_pool`, else a fresh one.

## Output

Write into the task record: `model`, `effort`, `routing_reason` (one line: why this level is
justified, e.g. "bounded fix with a clear failing test"). The session may not support every
setting; record what was actually used.
