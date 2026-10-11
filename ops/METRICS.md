# Ops metrics v3 (protected file)

Changes to this file, `helpers/metrics.py`, `helpers/formula.py`, `helpers/taskrec.py`,
`helpers/ghtask.py` and the sizing rubric in `modules/planning.md` need the owner's go-ahead in
an interactive session. Scheduled runs propose changes in a `needs-owner` issue instead
(reason: the system being measured must not change how it is measured).

v3 replaces v2 after a six-reviewer critique (metric design, incentives, implementation,
industry practice, owner perspective, cross-model). It keeps the owner's four parts and the
original E = Ac ÷ Δ shape.

## Unit, cohort, outcomes

- **Task** = an issue with a manager-owned task record. **Leaf** = a task no other record names
  as `parent`. Only leaves carry points; parents are reported as "plan lead time".
- **Points** = `size_at_triage` (frozen; 1, 2, 3, 5, 8). Tasks with `origin: regression-fix`
  carry 0 points (rework is not output).
- **Cohort** = tasks accepted (record created) in a fixed calendar week or month. Every cohort
  task gets one outcome:
  - `shipped`: record `done`, issue closed as completed, PR merged, and live for deploy repos
  - `no_change`: done and closed as completed with `no_change: true` (an answer, no code)
  - `pending_live`: merged, not yet live (deploy repos)
  - `dropped`: closed as not planned or duplicate, or done without a merged PR
  - `open`: still open (reported with its age)

## Time

Each task's active time (opened → end, minus any closed-and-reopened gaps) is split into
stages that add up exactly. Every hour goes to the first matching stage:
`owner` (needs-owner) → `plan` (agent-planning) → `build` (agent-in-progress) →
`review` (agent-review) → `blocked` → `deploy` (merged, not yet live) → `queue` (everything
else). End = live time (deploy repos), else merge time, else close time, else now.

- **H** = active hours of all cohort tasks (dropped and open included: waste counts).
- **Worker hours** = sum of `attempts[].finished_at − started_at` (falls back to build-label
  time, flagged).

## Quality

- **First-time right (FTR)**: shipped, one attempt, no fix round (recorded or derived: review
  entered more than once), never reopened.
- **Escaped**: a later issue names the task in a `Caused by:` line (or its record's
  `caused_by`), or a revert or deploy rollback names its PR. A task's escape status is
  **final** only 30 days after it shipped; before that it is provisional.
- **Ac** = FTR points ÷ shipped points. **Escape rate** = escaped tasks ÷ mature shipped tasks.

## The numbers

| Number | Definition | Notes |
|---|---|---|
| **E** (headline efficiency) | FTR points ÷ H = Ac ÷ h, where h = H ÷ shipped points | v1's Ac ÷ Δ per point; h splits exactly by stage |
| **Au** | cohort tasks with no rescue owner event ÷ cohort tasks | policy, access and intent escalations do not count against it; unknown owner events count as rescue |
| **Owner minutes** | sum of owner events (fixed minutes per type), by cause | the burden, in minutes, not yes/no |
| **S** | shipped points ÷ worker hours, by size band | generation speed, not runner timing |
| **Δ** | lead time opened → end, P50 and P85, by size band | plus WIP, oldest open age |
| **Volume** | FTR points per week; opened vs shipped | capacity, bounded by demand |
| **Cost** | sum of `attempts[].cost_usd` ÷ FTR points | "not recorded" until workers log it |
| **Escape rate** | see Quality | mature tasks only |

Every rate shows n and a 95% Wilson interval; under n = 10 it shows counts ("7 of 9"), not a
percentage. Comparisons (models, repos) are made within a size band only.

## Derived beats recorded

Counters are `max(recorded, derived)`. Fix rounds derive from review entries; reopen events
count; owner comments without the worker marker count as owner events of unknown cause.
Missing data never counts as success. Until agents have their own GitHub identity, owner and
agent actions share one login, and derivation relies on the worker marker; the digest reports
record-vs-derived mismatches.

## Decisions from the numbers

- Routing changes need 20+ tasks per model in the band; switch only when the 95% interval
  clears the threshold (same rule in both directions); credit the first attempt's model; trial
  by alternating tasks before switching.
- Each improvement states the metric it targets, the expected effect, and a judge date; changes
  that show no effect beyond normal variation by then are reverted.

## Calibration (owner-dependent, small)

- Size calibration: the owner sizes 20 past issues once; triage re-sizes them blind monthly.
- Seeded-defect check: monthly, the verifier must catch at least 80% of known injected bugs.
- Canaries: 1-2 issues a month that should produce a question; at least 90% must.
- Owner sample: 5 autonomous tasks a week, one question: "should I have been asked?"
