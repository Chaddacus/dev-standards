# Review module

Turns a worker's "finished" into evidence-backed done, or sends it back. Runs on tasks in
`review`.

## verify

1. Pick the reviewer level with the routing module (phase `review`) from facts about the diff:
   lines changed and paths touched (data, deletion, auth/security, money, deploy). Never from
   the task's size, which the system itself assigned.
2. Launch a `verifier` agent (it did not write the change) with: issue, `done_when`, plan link,
   worktree, PR. It runs:
   - each criterion → evidence (test run, live run of the real thing)
   - `standards/accuracy/fail_then_pass.py`: the new tests fail without the change and pass with it
   - changed-line coverage where the stack supports it
   - regressions next to the change (callers, config, docs)
   - high consequence: a Codex second opinion
3. Integration checks (manager): the PR's CI is green; dependent tasks' interfaces still hold
   (contract schemas, consumers named in the plan); the parent plan is still valid.

## Outcomes

- `PASS` and integration OK → merge per the merge rule: `gh pr merge --auto --squash
  --delete-branch` (or `pr-ready` + owner merge where the repository reserves it) → progress →
  `done` once merged. Deploy-on-merge repositories deploy afterwards (local runner).
- `FAIL` → progress: `bump fix_rounds`, state `in-progress`, findings sent to the same worker
  (one more round). A second `FAIL` → recovery.

Escapes: any issue for a bug that a merged agent PR caused gets the `agent-regression` label and
a line `Caused by: <PR URL>` in its body. That line is how ops/METRICS.md links escapes; without
it the escape is not counted.

## Output

`{"verdict": "PASS" | "FAIL", "evidence": [...], "findings": [...]}`.
