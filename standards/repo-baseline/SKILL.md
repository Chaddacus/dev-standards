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

GitHub-light (owner decision 2026-10-24): the hook reads every file fact (map, dev.py, FAST tier,
CI workflows, LFS config) from the local clone, and calls GitHub only for the settings GitHub alone
knows (labels, rulesets, auto-merge, agent access): 3-4 calls, cached 24 h per repository in
`~/.claude/repo-baseline/cache.json`. A session start with a fresh cache makes no GitHub call.
Tests: `python -m unittest discover -s ~/.claude/skills/repo-baseline -p "test_*.py"`.

## Closing gaps

- **No CI workflow:** add `.github/workflows/checks.yml` that runs the repo's canonical checks
  (`.claude/verification.json`, SPEC/README test commands, package scripts). Run them locally first.
  Land it through a feature branch and PR; merge once it passes.
- **CI exists but does not gate merges:** if nothing pushes straight to the default branch
  (no cron/sync tool commits to it; check recent history for non-merge commits from automation),
  run `baseline.py protect --full --check <job name>`. If automation pushes to it directly, leave
  basic protection and say so in the final summary.
- **GitHub-light CI** (a workflow triggered by `push` or `schedule`, using a matrix, or downloading
  Git LFS files): make it one job on `pull_request` only with LFS off. Checks that need LFS media
  run locally. Scheduled jobs that are the product (for example a daily scrape) move to a local
  scheduled task or stay only with the owner's say-so, recorded in the PR.
- **Git LFS repo without `.lfsconfig` `fetchexclude`:** add `.lfsconfig` with
  `[lfs] fetchexclude = <raw source art paths>` (for example `ArtSource/Meshy/**`), so clones and
  fetches skip raw sources; fetch them by path with `git lfs pull --include=<path>` when needed.
  A repo with no raw source art records `lfs_fetchexclude: "exempt: <why>"` in `~/.claude/ops/config.json`.
- **New repo:** run `baseline.py apply` after the first push, then close gaps as above.

## Other commands

- `audit [--all]` — report cwd repo or all owned repos.
- `apply [--all]` — apply label + basic ruleset.
