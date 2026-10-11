# Runners

The spine and modules are the same everywhere; only these adapters differ.

## local (the owner's Windows PC, scheduled task "Ops worker", hourly)

- Repositories: git checkouts under `~/code` owned by the owners in
  `~/.claude/repo-baseline/config.json`, except `runner: cloud` / `none` in
  `~/.claude/ops/config.json`.
- Queue: `python ~/.claude/skills/ops-worker/precheck.py` (also reports `deploy_pending`).
- Helpers: `python <dev-standards>/ops/helpers/taskrec.py`, `ghtask.py` (gh CLI), `formula.py`;
  `<dev-standards>` = `~/code/personal/dev-standards` (pull it at the start of each run).
- Workers: `implementer`, `verifier`, `reader` agents from `~/.claude/agents/`.
- Extra duties: lock file, pending deploys (`deploy_pending` → `python dev.py deploy`, record
  the SHA in `~/.claude/ops/deployed.json`), ops log `~/.claude/ops/log.jsonl`.
- Rules: `~/.claude/CLAUDE.md`. Hooks enforce guardrails and pre-push checks.

## cloud (routine "Cloud ops worker (issues opened + hourly)")

- Repositories: the ones checked out in the session (except dev-standards).
- Queue and records: the GitHub tools available in the session (list issues, read/write
  comments, labels). Before every state change run `python ops/helpers/taskrec.py transition`
  and render records/updates with `taskrec.py` so the format is identical to local.
- Helpers: `taskrec.py` (pure Python, no network). `ghtask.py` and `formula.py` need the gh CLI;
  use the GitHub tools instead.
- Workers: the agent definitions in `standards/agents/` (paste the relevant one into the Agent
  prompt; the cloud has no `~/.claude/agents`).
- No deploys, no SSH, no lock file (duplicate-work rule in `OPS-CLOUD.md` instead).
- Rules: `OPS-CLOUD.md` restates the safety floor; standards under `standards/`.
