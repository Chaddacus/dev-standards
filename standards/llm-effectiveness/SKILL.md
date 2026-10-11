---
name: llm-effectiveness
description: Measure LLM coding effectiveness with E = Ac / Delta (working changes per hour), from autonomy, speed, accuracy and delta. Use when the user asks to track, time, benchmark or report on how well the LLM/agent performs, compare models or setups, find the bottleneck, or run a what-if; or says "track this task" / "log this run" / "effectiveness report".
---

# LLM Effectiveness

Formula: **E = Ac / Δ**, with **Δ = write + wait + test** (minutes per task).

- **write** = LLM producing the change (Speed: W/S)
- **wait** = time stopped for the human: approvals, questions, report-backs (Autonomy: (1−Au)·H)
- **test** = building, testing, validating (V)
- **Ac** = share of tasks that worked on the first try (no fix-up round)

Script: `python ~/.claude/skills/llm-effectiveness/effectiveness.py <command>`
Data: `~/.claude/llm-effectiveness/runs.json` (shared across projects).

## Tracking a task live

Only track when the user asked for tracking (for this task or this session). Then:

1. Task begins: `start "<short task name>" --setup <label>`
   Label = model + mode, e.g. `opus-bypass`, `sonnet-default`. Ask once per session if unknown; reuse it.
2. Before ending a turn to ask a question, request approval, or report back mid-task: `phase wait`.
3. First thing on the next user turn for that task: `phase write`.
4. Running builds/tests/validation: `phase test`; back to coding: `phase write`.
5. If the user reports it broken, or a fix round is needed after you said it was done: `fix`.
6. Task finished and verified: `done pass`. Abandoned or not working: `done fail`.

Phase calls are cheap; do them silently. Do not mention tracking each step to the user.
If several runs are open, pass `--id <id>`.

## Other commands

- `add "<task>" --write 6 --wait 10 --test 5 --stops 2 --result pass --setup X` — log a past run manually.
- `report [--setup X] [--last N] [--json]` — E, Ac, Δ breakdown, stops/task, bottleneck, ranking across setups.
- `whatif --wait 0.5` / `--ac 0.95` — projected E plus a single-lever comparison table.
- `list`, `delete --id X`.

## Reading the report

Lead with E and the bottleneck. Advice by bottleneck:
- **wait** biggest → raise autonomy (permissions, clearer CLAUDE.md/spec, fewer check-ins).
- **test** biggest → automate validation (tests, CI, scripted checks).
- **write** biggest → faster model/mode or smaller tasks.
- **Ac < 85%** → fix accuracy (context, specs, self-testing) before raising autonomy.

Need ~5+ runs per setup before comparing setups; say so if there are fewer.
