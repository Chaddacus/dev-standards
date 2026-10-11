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
| Review: diff under ~50 lines, docs or config, no sensitive paths | sonnet | low |
| Review: logic changes | sonnet | medium |
| Review: high consequence (data, deletion, auth/security, money, deploy, > ~300 lines) | opus | high, plus a second-model review (Codex, or Fable while Codex is unavailable) |

Overrides: `routing` in `~/.claude/ops/config.json` (task kind → model) wins for implementation.
The weekly improvement pass changes it only through `metrics.routing_decision` (ops/METRICS.md):
20+ tasks per model within a size band, a 95% interval that clears the threshold, the same rule
in both directions, credit to the first attempt's model, and an alternating trial before any
switch. While the ops config has a `trial` for a size band, alternate its two models by issue
number (even → first, odd → second) and record the reason "trial". Review strength is chosen from
the diff, never from the task's size. Fable 5.1 is on trial for
the planning phase when the config says `"plan": "fable"`.

## Environment

- `local` runner: anything needing this PC (Unreal, SSH deploys, vault, LFS-heavy repos).
- `cloud` runner: repositories marked `runner: cloud` in the ops config.
- Worktree: pool worktree when the repo has `worktree_pool`, else a fresh one.

## Output

Write into the task record: `model`, `effort`, `routing_reason` (one line: why this level is
justified, e.g. "bounded fix with a clear failing test"). The session may not support every
setting; record what was actually used.
