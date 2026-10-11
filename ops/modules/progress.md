# Progress module

The only writer of task state. Keeps the authoritative task record (one manager-owned issue
comment, `taskrec.py` schema) and the state label in sync; worker updates are separate
append-only comments.

## Operations

- **create / update record**: local `ghtask.py set-record` (validates the transition, rewrites
  the record comment, swaps the state label, closes on `done`); cloud: the same steps with the
  GitHub tools, after `taskrec.py transition` says `ok`.
- **record** (worker update arrives): validate with `taskrec.py parse-update`; post it if the
  worker could not; then: `finished` → state `review`; `blocked` → `blocked` (or recovery when the
  blocker is not a dependency); `stuck` → recovery.
- **block / unblock**: dependency-driven moves between `ready` and `blocked`.
- **counters** (never rewrite them by hand; `set-record` refuses decreases):
  - review sends work back → `ghtask.py bump <repo> <n> fix_rounds`
  - every worker run → `ghtask.py append <repo> <n> attempts` with
    `{"model","effort","started_at","finished_at","result"}` (+ `cost_usd` when known)
  - every owner action → `ghtask.py append <repo> <n> owner_events` with
    `{"type": answer|merge|review|decision|access, "cause": policy|required|access|intent|rescue,
    "at"}`. `policy` = repository rules reserve it (owner merges); `required` = spending,
    security, deletion, deploy approvals; `intent` = only the owner knows what they want;
    `rescue` = the agents could not do the work. Only `rescue` (and anything unknown) lowers Au.
- **merge and live**: when the PR merges, write `pr` (URL) and `merged_at`; for deploy repos the
  deploy step runs `ghtask.py live <repo> <n> <sha>` after the health check passes.
- **every manager comment** (plans, questions, escalations, skips, notes) goes through
  `ghtask.py comment` (cloud: end the text with `<!-- ops-worker -->`). Unmarked comments from
  the shared login count as owner actions.
- **close**: `done` with a merged PR, `no_change: true`, or `children` closes as completed;
  anything else closes as not planned (it counts as dropped).
- **checkpoint** (end of run): every task touched this run has a record matching its label; no
  task is `in-progress` without a live worker (orphans → `ready` with a note).

States and allowed moves are defined once in `taskrec.py` (`TRANSITIONS`); review is mandatory
between `in-progress` and `done`.

## Run report

Plain text, short: tasks moved to done (with PR links), tasks waiting on the owner (one line
each: what is needed), tasks in review or progress, failures and what recovery did. The ops log
line per task (`~/.claude/ops/log.jsonl`, local runner) is written here.

## Output

Every operation returns `{"ok": true, "state": ...}` or the helper's refusal (→ recovery).
