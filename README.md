# dev-standards

Shared engineering standards for the owner's repositories (personal and Van-Quest-Games).

- `.github/workflows/check.yml` — reusable CI: `python dev.py setup` + `python dev.py check`,
  with pip/npm caching. Pin callers to `@v1`; move the `v1` tag forward for compatible
  improvements, cut `v2` for breaking ones.
- `ops/` — the manager: `SPINE.md` (flow only), `modules/` (planning, routing, delegation,
  progress, review, recovery), `helpers/` (task record and state machine, GitHub adapter,
  formula v2), `RUNNERS.md` (local vs cloud).
- `OPS-CLOUD.md` — cloud routine entry point (safety floor + pointer to the spine).
- `standards/` — mirror of the owner's standards (rapid-dev, repo-baseline, accuracy,
  new-project, agents), published by `~/.claude/ops/sync_standards.py`.

The full standards live in the owner's Claude configuration (rapid-dev, repo-baseline, ops
skills); this repository holds the parts that must be reachable from GitHub and the cloud.
