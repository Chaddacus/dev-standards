# dev-standards

Shared engineering standards for the owner's repositories (personal and Van-Quest-Games).

- `.github/workflows/check.yml` — reusable CI: `python dev.py setup` + `python dev.py check`,
  with pip/npm caching. Pin callers to `@v1`; move the `v1` tag forward for compatible
  improvements, cut `v2` for breaking ones.
- `OPS-CLOUD.md` — instructions for the cloud ops worker routine.

The full standards live in the owner's Claude configuration (rapid-dev, repo-baseline, ops
skills); this repository holds the parts that must be reachable from GitHub and the cloud.
