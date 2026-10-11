# Delegation module

Turns a routed `ready` task into a worker assignment and runs the worker lifecycle.

## assign

1. Check dependencies (`ghtask.py deps`, or the cloud equivalent). Any open → progress → block.
2. Prepare the worktree (routing module decides pool or fresh; branch
   `claude/issue-<n>-<slug>` from `origin/<default>`).
3. Build the assignment with `taskrec.py render-assignment` (record, repo, worktree, merge rule).
   The assignment carries everything the worker needs, so it does not rediscover the
   architecture: done_when, plan link and the sub-task's slice of it, interfaces it must keep.
4. progress → transition the task to `in-progress`.
5. Launch the worker: `implementer` agent with the routed model and effort. Launch all of a
   run's assignments in one message so they run in parallel (parallel limit in the spine).

## Worker lifecycle

- `started` update when work begins.
- `milestone` updates at meaningful points (tests written and failing, implementation passing,
  PR opened), and at least every 30 minutes on long work (the agreed checkpoint).
- `blocked` / `stuck` updates immediately, with the blocker.
- `finished` when the PR is open with evidence. Finished means "ready for review"; the worker
  does not merge.
Each update: task ID, status, completed, next, blocker, output/test links (`taskrec.py
render-update` validates it).

## Output

Per launched worker: task ID, worker kind, model/effort, worktree, start time. On return: the
worker's final update JSON, handed to progress → record.
