---
name: repo-baseline
description: Keep every owned GitHub repo at the autonomy baseline (needs-owner label, protected default branch, CI that gates merges). Use when a "Repo baseline" session notice lists gaps, when creating a new repo, or when the user asks to audit or apply the baseline.
---

# Repo baseline

Script: `python ~/.claude/skills/repo-baseline/baseline.py <command>`
Owners covered: `~/.claude/repo-baseline/config.json` (`owners` list).

A SessionStart hook runs `baseline.py hook` in every session. It auto-applies the label and the
basic ruleset (no deletion, no force-push of the default branch) and reports remaining gaps.
Close gaps as routine work; do not ask the user.

## Closing gaps

- **No CI workflow:** add `.github/workflows/checks.yml` that runs the repo's canonical checks
  (`.claude/verification.json`, SPEC/README test commands, package scripts). Run them locally first.
  Land it through a feature branch and PR; merge once it passes.
- **CI exists but does not gate merges:** if nothing pushes straight to the default branch
  (no cron/sync tool commits to it; check recent history for non-merge commits from automation),
  run `baseline.py protect --full --check <job name>`. If automation pushes to it directly, leave
  basic protection and say so in the final summary.
- **New repo:** run `baseline.py apply` after the first push, then close gaps as above.

## Other commands

- `audit [--all]` — report cwd repo or all owned repos.
- `apply [--all]` — apply label + basic ruleset.
