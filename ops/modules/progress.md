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
- **counters**: increment `fix_rounds` when review sends work back; increment `owner_touches`
  when the owner had to act (answer, approval, merge); append to `attempts`
  (`{"model","effort","result"}`) on every worker run.
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
