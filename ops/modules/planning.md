# Planning module

Owns scope, acceptance criteria, sizing, dependencies, and the front-loaded plan. Keeps planning
proportional: a small bounded task gets triage only; multi-part work gets a plan first.

## triage (state `new`, or re-entry after an owner answer)

Done by one `reader` agent (Haiku, effort low) for a batch of issues. For each issue it returns:
- `kind`: fix | ci | docs | config | feature | design | debug | chore
- `size`: 1, 2, 3, 5 or 8 points (1 = one-line change, 3 = a focused change with tests,
  8 = several modules or an unknown approach)
- `done_when`: 2-6 checkable acceptance criteria
- `needs_plan`: true when size >= 5, more than one module or repository changes, an interface
  changes, or the approach is unclear
- or `question`: when criteria cannot be written without guessing the owner's intent (exact
  question, options, recommendation)
- or `skip`: notes or ideas without a goal

Output: a task record proposal per issue (`taskrec.py` schema) with state `ready` (or `planning`
when `needs_plan`), or a question for recovery → escalate, or skip.

## plan (state `planning`)

Done by one planning agent: model and effort from the routing module, phase `plan` (Opus by
default). Inputs: the issue, the repository's SPEC / AGENTS.md / CLAUDE.md, relevant code via
`reader` agents. It produces, as a comment on the issue:
- approach and the main decision with its reason
- interfaces that change (signatures, schemas, file formats) and who depends on them
- dependencies on other tasks or repositories
- risks and how each is checked
- the split: bounded sub-tasks of size <= 3, each with its own `done_when`, in dependency order

Then create one issue per sub-task (title, `done_when`, `parent: <this task>`, `depends_on`), each
entering at `ready`. The parent's record gets `plan: <comment link>` and moves to `blocked` on its
sub-tasks; it closes after the last one passes review.

Output: plan comment link, list of created sub-task issues, parent record update.

## Rules

- Never plan a size-1 or size-2 task; never skip planning for size 8.
- A plan that changes another repository's interface also creates the consumer task there
  (contract first; see the rapid-dev standard, section 6).
- Planning does not change project scope; anything beyond the issue becomes a question.
