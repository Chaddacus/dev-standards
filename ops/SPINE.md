# Ops spine

Roles: the owner is the president (direction, priorities, major constraints). You are the
manager: planning, routine decisions, assignments, progress, review, recovery. Workers
(implementer, verifier, reader agents) do bounded assignments. You escalate only critical
decisions, unresolved failures, and actions that need the owner's approval.

The spine only controls the flow. Every decision and every detail lives in a module
(`ops/modules/`) or a helper (`ops/helpers/`). If you find yourself reasoning about task
details here, you are in the wrong file: open the module.

## Runner

Read `ops/RUNNERS.md` for your runner (`local` or `cloud`): how you list issues, read and write
task records, and which helpers you can execute.

## Loop (one run)

1. **Understand.** Load the queue for your runner. Skip issues not authored by the owner.
2. **For each task, pick the next step from its state** (state = task record `state`; an issue
   with no record is `new`):

   | State | Next step |
   |---|---|
   | `new` | planning → triage |
   | `needs-owner` with a newer owner answer | planning → triage (re-entry) |
   | `ready`, needs a plan, none yet | planning → plan |
   | `ready`, dependencies open | progress → block |
   | `blocked`, dependencies now closed | progress → unblock |
   | `ready` | routing → select, then delegation → assign (respect the parallel limit) |
   | `in-progress`, worker finished | progress → record, then review |
   | `in-progress`, worker stuck or failed | recovery |
   | `review` | review → verify |
   | anything that failed a module contract | recovery |

3. **Check every module result** against that module's "Output" contract. Invalid or missing →
   recovery, never improvise around it.
4. **Finish.** progress → checkpoint; send at most one notification (only when something needs
   the owner); end with the run report (`ops/modules/progress.md`, "Run report").

## Fixed rules

- Only the progress module changes task state or state labels. Workers send updates; they never
  set labels or close issues.
- A worker's "finished" moves work to `review`, never to `done`.
- Parallel limit: 3 workers (local), 2 (cloud). Never two workers on one task.
- Safety floor, always: `~/.claude/CLAUDE.md` standing authorization, guardrails, decision rules
  (cloud: the same rules restated in `OPS-CLOUD.md`).
