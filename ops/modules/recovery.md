# Recovery module

When a worker struggles or a module result fails its contract, diagnose before acting. Change
the approach or reassign before bringing anything to the owner.

## 1. Classify the failure

| Cause | Signs | Action |
|---|---|---|
| Unclear requirements | worker asks what is wanted; review fails on intent, not code | planning → triage again with the findings; if still unclear, escalate a question |
| Plan wrong or missing | interfaces do not fit, sub-tasks collide | planning → plan again (planning model) |
| Missing access or credentials | auth errors, permission denied, missing secret | escalate (a stronger model cannot fix this) |
| Environment or flaky infrastructure | network/timeouts, runner crash, flaky test confirmed by rerun | retry once; flaky test → its own `Flaky test` task |
| Capability | correct understanding, repeated wrong implementation | reassign one level up (sonnet → opus, effort +1) with the previous attempt's notes |
| Scope creep | the fix needs work outside the assignment | split: new task for the extra work, original continues |
| Dependency | waiting on another task | progress → block |

## 2. Budget

At most 3 worker attempts per task (record `attempts`), at most one model escalation. When the
budget is used, escalate.

## 3. Escalate (owner)

Progress → `needs-owner` and an `owner_events` entry whose `cause` says why: `access`,
`intent`, `required`, `policy`, or `rescue` (the agents could not do it). Do not label a rescue
as anything else; honest causes are what keep Au meaningful. Plus one comment (posted with
`ghtask.py comment`, so it carries the worker marker) in this format:
- **What happened:** one or two sentences with the evidence link
- **What I tried:** the attempts (model, effort, result)
- **Recommendation:** the next step you would take, and the alternatives
- **Needed from you:** the exact decision, access, or approval

## Output

`{"cause": ..., "action": "replan|retry|reassign|split|block|escalate", "detail": ...}`.
