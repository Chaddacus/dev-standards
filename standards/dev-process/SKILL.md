---
name: dev-process
description: Generated development-process harness - experiment loop, evidence ladder E0-E4, work classes with gates, phases, playtest requests - from one spec. Use when setting up or changing how a repository plans, proves and merges work, or when adding the harness to a new game repository.
---

# Development process harness

One spec drives the process for every agent. The generic part is here and in
dev-standards (`standards/dev-process/`). Each repository adds an overlay with its
own phases and class gates, and generates its files from both.

Grounding: the 2026-10-30 research report on game-studio phase gates, playcentric
design, games user research and hypothesis-driven development (vault note
`wiki/game-studio/development-process.md`).

## Parts

- `process.base.json`: the loop (Question, Predict, Build, Measure, Decide), the
  evidence ladder (E0 tests, E1 one bot, E2 bot personas, E3 recorded play, E4
  owner judgment), the experiment card, the playtest request, the generic work
  classes (`experiment`, `tuning`, `content`, `feature`, `fix`, `infra`) and the
  PR checklist.
- `generate_process.py`: merges base and overlay and writes the process doc, the
  merged spec, the Experiment and Playtest issue forms, the PR template, and a
  short block between `<!-- dev-process:begin ... -->` and `<!-- dev-process:end -->`
  in the agent file. `--check` exits 1 on drift.
- `check_evidence.py`: CI check of a PR body against its class gate (class,
  rung, required sections, evidence lines that name a real file or a link).
- `test_dev_process.py`: `python -m unittest test_dev_process`.

## Adopt in a repository

1. Copy the three tool files into one folder, e.g. `scripts/process/` (vendored; record the dev-standards
   commit in the overlay's `"vendored_from"`).
2. Write `process.overlay.json` next to them: `project`, `agents_file`,
   `process_doc`, `merged_spec`, `check_command`, `start_pr` (the first PR the
   check applies to), `phases` (id, title, entry, exit, locks), `current_phase`,
   `ladder_tools`, and `classes` (`gate_extra`, `sections_extra`, `min_rung`).
3. Put the two markers in the agent file, then run
   `python scripts/process/generate_process.py`.
4. Add `generate_process.py --check` to the FAST tier, and in CI (pull_request
   `opened, synchronize, reopened, edited`):
   `python scripts/process/check_evidence.py --spec <merged_spec> --event "$GITHUB_EVENT_PATH"`.

## Change it

Generic rule: edit here, run the tests, run `python ~/.claude/ops/sync_standards.py`,
then re-vendor into each repository. Project rule: edit the overlay and regenerate.
Never hand-edit generated files; the drift check fails.
