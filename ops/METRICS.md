# Ops metrics v3.1 (protected file)

Changes to this file, the helpers (`metrics`, `formula`, `taskrec`, `ghtask`), the modules
(`planning`, `review`, `recovery`, `progress`, `routing`), the agents, hooks and ops skills need
the owner's go-ahead in an interactive session; an edit hook blocks them otherwise and logs
unlocked edits, and `~/.claude/hooks/protect.py` fingerprints them for the daily digest. Scheduled runs propose changes in a `needs-owner` issue instead
(reason: the system being measured must not change how it is measured).

v3 replaced v2 after a six-reviewer critique (metric design, incentives, implementation,
industry practice, owner perspective, cross-model); v3.1 applies the second review round. It
keeps the owner's four parts and the original E = Ac ÷ Δ shape.

## Unit, cohort, outcomes

- **Task** = an issue with a manager-owned task record. **Leaf** = a task no other record names
  as `parent`. Only leaves carry points; parents are reported as "plan lead time".
- **Points** = `size_at_triage` (frozen; 1, 2, 3, 5, 8). Tasks with `origin: regression-fix`
  carry 0 points (rework is not output). A record missing `size_at_triage` or `origin` scores 0
  and is flagged: missing data never counts as success. Parents are found from a child's `parent`
  (short id or URL) or the parent's own `children`.
- **Acceptance** = the earliest valid record comment (later copies do not move a task between
  cohorts).
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

- **H** (work hours) = stage hours of all cohort tasks, parents included (their time before the
  split), dropped and open included (waste counts), except: `blocked` time (waiting on another
  task is lead time, not work), `no_change` answers, and pending deploys beyond 72 hours (a health
  problem, reported separately). Merge and live times split stages; after merge in a deploy
  repository the time is `deploy` unless the owner holds it. A task ends at its latest merge.
- **Worker hours** = sum of `attempts[].finished_at − started_at` (falls back to build-label
  time, flagged).

## Quality

- **First-time right (FTR)**: shipped, exactly one recorded attempt (none recorded is not FTR),
  no failed attempt, no fix round (recorded or derived: review entered more than once), never
  reopened.
- **Escaped**: an issue filed within 30 days of shipping names the task or its PR on a
  `Caused by` line (markdown prefixes and a missing colon are fine) or in its record's
  `caused_by`. Escape status is **final** 30 days after shipping. (Revert and rollback detection
  is not built yet.)
- **Ac** = FTR tasks ÷ shipped tasks (each task is one trial; the interval uses tasks).
  **Ac_points** = FTR points ÷ shipped points (no interval). **Escape rate** = escaped ÷ mature
  shipped tasks.

## The numbers

| Number | Definition | Notes |
|---|---|---|
| **E** (headline efficiency) | FTR points ÷ H = Ac_points ÷ h, where h = H ÷ shipped points | v1's Ac ÷ Δ per point; h splits exactly by stage; also reported without queue time |
| **Au** | ended tasks with no rescue owner event ÷ ended tasks (plus open tasks that already had one) | policy, access, required and intent causes do not count unless the task struggled first (fix round or failed attempt), then they are rescues; unknown causes and unmarked owner comments count as rescue; owner-intent drops are left out; open tasks give bounds |
| **Owner minutes** | sum of owner events (fixed minutes per type), by cause | the burden, in minutes, not yes/no |
| **S** | shipped points ÷ worker hours, by size band | generation speed, not runner timing |
| **Δ** | lead time opened → end, P50 and P85, by size band | plus WIP, oldest open age |
| **Volume** | FTR points per week; opened vs shipped | capacity, bounded by demand |
| **Cost** | sum of `attempts[].cost_usd` ÷ FTR points | "not recorded" until workers log it |
| **Escape rate** | see Quality | mature tasks only |

Every rate shows n and a 95% Wilson interval; under n = 10 it shows counts ("7 of 9"), not a
percentage. Comparisons (models, repos) are made within a size band only. A cohort's numbers are
PROVISIONAL until all its tasks have ended; quality decisions read the cohort from 5 to 8 weeks
ago. Raw data is saved weekly so any cohort can be recomputed after a definition change.

## Derived beats recorded

Counters are `max(recorded, derived)`. Fix rounds derive from review entries; reopen events
count; owner comments without the worker marker, between acceptance and the task's end, count as
owner events of unknown cause unless a recorded owner event lies within 6 hours; `owner_touches`
above the recorded events adds unknown events. Every manager comment therefore goes through
`ghtask.py comment` (or ends with the marker in the cloud).
Missing data never counts as success. Agents act as `chaddacus-agent` (since 2026-10-12), so in
local-runner repositories records come from that account and any comment by the owner's account
is an owner action. Cloud-runner repositories still share the owner's login there; the worker
marker tells them apart. Records the owner's account wrote before that switch still count: the
ops config lists them in `legacy_manager_logins` (`login`, `until`), and `metrics.is_manager_record`
judges the comment's creation time, not its last edit. The digest reports record-vs-derived mismatches.

## Decisions from the numbers

- Routing changes need 20+ tasks per model in the band (`routing_table`: success = FTR and,
  for mature tasks, not escaped); switch only when the 95% interval clears the threshold (same
  rule in both directions); credit the first attempt's model; trial by alternating tasks before
  switching.
- Each improvement states the metric it targets, the expected effect, and a judge date; changes
  that show no effect beyond normal variation by then are reverted.

## Calibration (owner-dependent, small)

- Size calibration: the owner sizes 20 past issues once; triage re-sizes them blind monthly.
- Seeded-defect check: monthly, the verifier must catch at least 80% of known injected bugs.
- Canaries: 1-2 issues a month that should produce a question; at least 90% must.
- Owner sample: 5 autonomous tasks a week, one question: "should I have been asked?"
