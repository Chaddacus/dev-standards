#!/usr/bin/env python3
"""Repository baseline: keep every owned GitHub repo at the autonomy safety floor.

Baseline per repo:
  - ops labels: needs-owner, agent-ready, agent-in-progress
  - default branch ruleset: no deletion, no force-push
  - a CI workflow (reported; Claude adds it, since checks are repo-specific)

Commands:
  hook            SessionStart hook: check the repo in the session cwd (cached 24h),
                  apply what is safe, and tell Claude about remaining gaps.
  audit  [--all]  report the cwd repo, or every owned repo
  apply  [--all]  apply label + basic ruleset to the cwd repo, or every owned repo
  protect --full --check NAME [--repo OWNER/NAME]
                  upgrade to PR-required + required status check NAME
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

STATE = Path.home() / ".claude" / "repo-baseline"
CONFIG = STATE / "config.json"
CACHE = STATE / "cache.json"
CACHE_TTL = 24 * 3600
DEFAULT_CONFIG = {"owners": ["Chaddacus", "Van-Quest-Games"]}
LABELS = [
    ("needs-owner", "B60205", "Blocked on an owner decision or access; agent continues other work"),
    ("agent-ready", "0E8A16", "Approved for the autonomous ops worker to pick up"),
    ("agent-in-progress", "FBCA04", "The ops worker is working on this now"),
    ("agent-planning", "1D76DB", "The manager is planning this task before work starts"),
    ("agent-review", "5319E7", "Work finished; the manager is reviewing it before done"),
    ("blocked", "000000", "Waiting on another task or dependency"),
    ("agent-regression", "D93F0B", "Bug traced to a merged agent change; fix test-first and add a global guard"),
    ("ops-skip", "EDEDED", "Triage skipped: umbrella or not agent work; the ops worker ignores it"),
]
ACTIONS_APP_ID = 15368


def owner_env():
    """Admin work (rulesets, labels, audits across every repo) runs as the owner, not the agent."""
    try:
        cfg = json.loads((Path.home() / ".claude" / "ops" / "config.json").read_text(encoding="utf-8"))
        d = cfg.get("owner_gh_config_dir")
    except Exception:
        d = None
    env = dict(os.environ)
    if d:
        env["GH_CONFIG_DIR"] = d
    return env


def gh(*args, input=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=input, timeout=60, env=owner_env(),
                       encoding="utf-8")
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def gh_json(*args):
    code, out, _ = gh(*args)
    if code != 0 or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def owners():
    cfg = load(CONFIG, None)
    if cfg is None:
        save(CONFIG, DEFAULT_CONFIG)
        cfg = DEFAULT_CONFIG
    return [o.lower() for o in cfg.get("owners", [])]


def repo_from_cwd(cwd):
    try:
        url = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except Exception:
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def all_repos():
    out = []
    for owner in owners():
        data = gh_json("repo", "list", owner, "-L", "500", "--json", "nameWithOwner,isArchived,isFork") or []
        out += [r["nameWithOwner"] for r in data if not r["isArchived"] and not r["isFork"]]
    return out


def audit(repo):
    info = gh_json("api", f"repos/{repo}")
    if not info:
        return {"repo": repo, "skip": "not reachable"}
    if info.get("archived") or not info.get("permissions", {}).get("admin"):
        return {"repo": repo, "skip": "archived or no admin access"}
    branch = info.get("default_branch") or "main"
    rules = gh_json("api", f"repos/{repo}/rules/branches/{branch}") or []
    types = {r.get("type") for r in rules}
    have = {l["name"] for l in (gh_json("api", f"repos/{repo}/labels?per_page=100") or []) if isinstance(l, dict)}
    wf = gh_json("api", f"repos/{repo}/contents/.github/workflows?ref={branch}") or []
    root = gh_json("api", f"repos/{repo}/contents?ref={branch}") or []
    names = {f.get("name") for f in root if isinstance(f, dict)}
    containers, compose_watch = container_state(repo, branch, root)
    vcode, vout, _ = gh("api", f"repos/{repo}/contents/.claude/verification.json?ref={branch}",
                        "-H", "Accept: application/vnd.github.raw")
    fast_cmds = ""
    if vcode == 0:
        try:
            fast_cmds = " ".join(c.get("command", "") for c in (json.loads(vout).get("fast") or [])
                                 if isinstance(c, dict))
        except Exception:
            fast_cmds = ""
    return {
        "repo": repo, "branch": branch, "empty": info.get("size", 0) == 0,
        "label": all(n in have for n, _, _ in LABELS),
        "auto_merge": bool(info.get("allow_auto_merge")),
        "agent_push": agent_can_push(repo),
        "basic_protection": {"deletion", "non_fast_forward"} <= types,
        "full_protection": {"pull_request", "required_status_checks"} <= types,
        "map": bool(names & {"CLAUDE.md", "AGENTS.md"}),
        "dev_py": "dev.py" in names,
        "containers": containers,
        "compose_watch": compose_watch,
        "fast_checks": vcode == 0,
        "static_checks": bool(re.search(STATIC_RE, fast_cmds + " " + dev_fast_tasks(repo, branch, fast_cmds), re.I)),
        "ci": any(str(f.get("name", "")).endswith((".yml", ".yaml")) for f in wf if isinstance(f, dict)),
    }


STATIC_RE = r"ruff|flake8|pylint|mypy|pyright|eslint|tsc|typecheck|lint|clippy|golangci|vet"


def dev_fast_tasks(repo, branch, fast_cmds):
    """When the FAST tier is `python dev.py fast`, look inside dev.py's fast task (the standard)."""
    if "dev.py" not in fast_cmds:
        return ""
    code, body, _ = gh("api", f"repos/{repo}/contents/dev.py?ref={branch}", "-H", "Accept: application/vnd.github.raw")
    if code != 0:
        return ""
    m = re.search(r'"fast"\s*:\s*\[(.*?)\]', body, re.S)
    return m.group(1) if m else ""


COMPOSE = re.compile(r"^(docker-)?compose(\.[\w-]+)?\.ya?ml$")


def container_state(repo, branch, root):
    """Does the repo use containers, and does any compose file define develop.watch?"""
    files = [f for f in root if isinstance(f, dict)]
    for d in ("deploy", "docker"):
        if any(f.get("name") == d and f.get("type") == "dir" for f in files):
            files += [f for f in (gh_json("api", f"repos/{repo}/contents/{d}?ref={branch}") or [])
                      if isinstance(f, dict)]
    names = [f.get("path") for f in files if f.get("type") == "file"]
    uses = any(n.rsplit("/", 1)[-1] == "Dockerfile" or COMPOSE.match(n.rsplit("/", 1)[-1]) for n in names)
    watch = False
    for n in names:
        if COMPOSE.match(n.rsplit("/", 1)[-1]):
            code, body, _ = gh("api", f"repos/{repo}/contents/{n}?ref={branch}", "-H", "Accept: application/vnd.github.raw")
            if code == 0 and re.search(r"^\s*develop:\s*$", body, re.M) and "watch:" in body:
                watch = True
                break
    return uses, watch


def apply(state):
    repo, done = state["repo"], []
    if state.get("skip"):
        return done
    if not state["label"]:
        ok = all(gh("label", "create", n, "-R", repo, "--color", c, "--description", d, "--force")[0] == 0
                 for n, c, d in LABELS)
        if ok:
            state["label"] = True
            done.append("labels")
    if not state["basic_protection"] and not state["empty"]:
        body = {"name": "baseline-protect-default", "target": "branch", "enforcement": "active",
                "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]}
        if gh("api", "-X", "POST", f"repos/{repo}/rulesets", "--input", "-", input=json.dumps(body))[0] == 0:
            state["basic_protection"] = True
            done.append("basic protection")
    if not state.get("auto_merge", True) and not state["empty"]:
        # CLAUDE.md: the agent enables GitHub auto-merge on its PR once the verifier passes; the repo must allow it
        # (2026-10-16: history-toons refused `gh pr merge --auto`, "Auto merge is not allowed for this repository")
        if gh("api", "-X", "PATCH", f"repos/{repo}", "-F", "allow_auto_merge=true")[0] == 0:
            state["auto_merge"] = True
            done.append("auto-merge allowed")
    return done


def agent_login():
    return load(Path.home() / ".claude" / "ops" / "config.json", {}).get("agent_login")


def agent_can_push(repo):
    """Managed repos (ops config) need the agent account with write (never admin). Granting access is
    the owner's decision: the baseline only reports the gap (2026-10-12, after an over-broad grant was
    reverted)."""
    login = agent_login()
    managed = load(Path.home() / ".claude" / "ops" / "config.json", {}).get("repos", {})
    if not login or repo not in managed:
        return True
    code, out, _ = gh("api", f"repos/{repo}/collaborators/{login}/permission", "--jq", ".permission")
    return code == 0 and out in ("write", "maintain", "admin")


def direct_push(repo):
    cfg = load(Path.home() / ".claude" / "ops" / "config.json", {})
    return bool(cfg.get("repos", {}).get(repo, {}).get("direct_push"))


def gaps(state):
    if state.get("skip"):
        return []
    g = []
    if not state.get("agent_push", True):
        g.append("agent account cannot push to this managed repo (owner grants access; see Identities)")
    if not state["label"]:
        g.append("missing ops labels (apply failed)")
    if not state["basic_protection"] and not state["empty"]:
        g.append("default branch not protected against deletion/force-push (apply failed)")
    if not state.get("auto_merge", True) and not state["empty"]:
        g.append("auto-merge not allowed, so `gh pr merge --auto` fails (apply failed)")
    if not state.get("map", True) and not state["empty"]:
        g.append("no repo map (CLAUDE.md or AGENTS.md with key commands, layout, checks); have the reader agent draft one")
    if not state.get("fast_checks", True) and not state["empty"]:
        g.append("no .claude/verification.json with a `fast` tier (the pre-push hook has nothing to run)")
    elif not state.get("static_checks", True) and not state["empty"]:
        g.append("FAST tier has no linter or type checker (add ruff/pyright, tsc/eslint, or the stack's equivalent)")
    if not state.get("dev_py", True) and not state["empty"]:
        g.append("rapid-dev: no dev.py standard task runner (setup/dev/fast/test/check/logs/deploy/rollback)")
    exempt = load(Path.home() / ".claude" / "ops" / "config.json", {}).get("repos", {}).get(
        state["repo"], {}).get("compose_watch", "")
    if state.get("containers") and not state.get("compose_watch", True) and not str(exempt).startswith("exempt"):
        g.append("rapid-dev: containers without a hot-reload compose override (develop.watch)")
    if not state["ci"]:
        g.append("no CI workflow")
    elif not state["full_protection"] and not direct_push(state["repo"]):
        g.append("CI exists but merges do not require it (no PR + required-check ruleset)")
    return g


def cmd_hook(_):
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    repo = repo_from_cwd(data.get("cwd") or os.getcwd())
    if not repo or repo.split("/")[0].lower() not in owners():
        return
    cache = load(CACHE, {})
    hit = cache.get(repo)
    if hit and time.time() - hit["at"] < CACHE_TTL:
        state, done = hit["state"], []
    else:
        state = audit(repo)
        done = apply(state)
        cache[repo] = {"at": time.time(), "state": state}
        save(CACHE, cache)
    g = gaps(state)
    if not g and not done:
        return
    lines = [f"Repo baseline for {repo}:"]
    if done:
        lines.append("- applied automatically: " + ", ".join(done))
    for x in g:
        lines.append("- gap: " + x)
    if g:
        lines.append("Close gaps as routine work under the repo-baseline skill (no need to ask the user).")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": "\n".join(lines)}}))


def targets(a):
    if a.all:
        return all_repos()
    repo = a.repo or repo_from_cwd(os.getcwd())
    if not repo:
        sys.exit("Not in a GitHub repo; pass --repo or --all.")
    return [repo]


def cmd_audit(a, do_apply=False):
    cache = load(CACHE, {})
    for repo in targets(a):
        state = audit(repo)
        done = apply(state) if do_apply else []
        cache[repo] = {"at": time.time(), "state": state}
        if state.get("skip"):
            print(f"skip  {repo}: {state['skip']}")
            continue
        g = gaps(state)
        print(f"{'ok  ' if not g else 'gap '}  {repo}" + (f"  applied: {', '.join(done)}" if done else "")
              + ("".join(f"\n        - {x}" for x in g)))
    save(CACHE, cache)


def check_names(repo):
    """Check names that have run recently: the default branch's latest commit plus the jobs of the
    last few workflow runs (pull-request-only checks never run on the default branch)."""
    names = set()
    code, out, _ = gh("api", f"repos/{repo}/commits/HEAD/check-runs", "--jq", ".check_runs[].name")
    names |= set(out.splitlines()) if code == 0 else set()
    code, out, _ = gh("api", f"repos/{repo}/actions/runs?per_page=5", "--jq", ".workflow_runs[].id")
    for run_id in (out.splitlines() if code == 0 else []):
        code, jobs, _ = gh("api", f"repos/{repo}/actions/runs/{run_id}/jobs", "--jq", ".jobs[].name")
        names |= set(jobs.splitlines()) if code == 0 else set()
    return names - {""}


def cmd_protect(a):
    repo = targets(a)[0]
    seen = check_names(repo)
    missing = [c for c in a.check if c not in seen]
    if missing:  # a required check that never reports blocks every pull request (2026-10-12)
        print(f"{repo}: not protected; no check named {missing} ran recently. "
              f"Names that ran: {sorted(seen) or 'none'}")
        sys.exit(1)
    body = {"name": "baseline-require-ci", "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            "rules": [
                {"type": "pull_request", "parameters": {
                    "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False, "require_last_push_approval": False,
                    "required_review_thread_resolution": False}},
                {"type": "required_status_checks", "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [{"context": c, "integration_id": ACTIONS_APP_ID} for c in a.check]}}]}
    code, out, err = gh("api", "-X", "POST", f"repos/{repo}/rulesets", "--input", "-", input=json.dumps(body))
    print(f"{repo}: full protection {'added' if code == 0 else 'FAILED: ' + err}")
    cache = load(CACHE, {})
    cache.pop(repo, None)
    save(CACHE, cache)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook").set_defaults(fn=cmd_hook)
    for name in ("audit", "apply"):
        s = sub.add_parser(name)
        s.add_argument("--all", action="store_true")
        s.add_argument("--repo")
        s.set_defaults(fn=(lambda a: cmd_audit(a)) if name == "audit" else (lambda a: cmd_audit(a, True)))
    s = sub.add_parser("protect")
    s.add_argument("--full", action="store_true", required=True)
    s.add_argument("--check", action="append", required=True, help="required status check name (job name)")
    s.add_argument("--repo")
    s.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_protect)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a hook must never break a session
        if len(sys.argv) > 1 and sys.argv[1] == "hook":
            sys.exit(0)
        raise
