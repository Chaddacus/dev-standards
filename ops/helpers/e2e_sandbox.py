#!/usr/bin/env python3
"""End-to-end test of the ops record pipeline against real GitHub (repo Chaddacus/ops-sandbox).

Drives scripted tasks through ghtask.py (records, state labels, updates, a real merged PR),
then runs formula.py on the sandbox and checks the report matches the script exactly.
No agents are involved: this tests the plumbing and the metric, not model behaviour.

  python ops/helpers/e2e_sandbox.py            # run, assert, close the scenario issues
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = "Chaddacus/ops-sandbox"
GHTASK, FORMULA = HERE / "ghtask.py", HERE / "formula.py"


def sh(args, input=None, check=True):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", input=input)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(map(str, args[:5]))}\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def gt(cmd, n, payload=None, *extra):
    return sh([sys.executable, str(GHTASK), cmd, REPO, str(n), *extra],
              input=json.dumps(payload) if payload is not None else None)


def issue(title):
    url = sh(["gh", "issue", "create", "-R", REPO, "--title", title, "--body", "e2e scenario"])
    return int(url.rsplit("/", 1)[1])


def rec(n, state, size=2, **extra):
    return {"task": f"ops-sandbox#{n}", "kind": "fix", "size": size, "size_at_triage": size,
            "origin": "owner", "state": state, "done_when": ["scenario step"], **extra}


def merged_pr(n, tag):
    """Create a branch with one file change, open a PR that closes #n, merge it."""
    branch = f"e2e/{tag}-{n}"
    base = sh(["gh", "api", f"repos/{REPO}/git/ref/heads/main", "-q", ".object.sha"])
    sh(["gh", "api", "-X", "POST", f"repos/{REPO}/git/refs", "-f", f"ref=refs/heads/{branch}", "-f", f"sha={base}"])
    sh(["gh", "api", "-X", "PUT", f"repos/{REPO}/contents/e2e/{tag}-{n}.txt", "-f", f"message=e2e {tag}",
        "-f", "content=ZTJl", "-f", f"branch={branch}"])
    url = sh(["gh", "pr", "create", "-R", REPO, "--head", branch, "--title", f"e2e {tag}", "--body", f"Closes #{n}"])
    sh(["gh", "pr", "merge", url, "-R", REPO, "--squash", "--delete-branch"])
    return url


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    start = datetime.now(timezone.utc) - timedelta(seconds=5)
    tag = start.strftime("%H%M%S")

    # A: clean task, shipped first time
    a = issue("e2e A clean")
    gt("set-record", a, rec(a, "ready"))
    gt("set-record", a, rec(a, "in-progress", model="sonnet", effort="medium", routing_reason="e2e"))
    gt("append", a, {"model": "sonnet", "started_at": datetime.now(timezone.utc).isoformat(),
                     "finished_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()}, "attempts")
    sh([sys.executable, str(GHTASK), "comment", REPO, str(a)], input="Plan: one-line change.")
    pr_a = merged_pr(a, tag)
    r = json.loads(gt("get-record", a))
    gt("set-record", a, dict(r, state="review"))
    gt("set-record", a, dict(r, state="done", pr=pr_a))

    # B: one fix round (review -> in-progress -> review), then shipped
    b = issue("e2e B fix round")
    gt("set-record", b, rec(b, "ready", size=3))
    gt("set-record", b, rec(b, "in-progress", size=3))
    gt("set-record", b, rec(b, "review", size=3))
    gt("bump", b, None, "fix_rounds")
    r = json.loads(gt("get-record", b))
    gt("set-record", b, dict(r, state="in-progress"))
    gt("set-record", b, dict(r, state="review"))
    pr_b = merged_pr(b, tag)
    gt("set-record", b, dict(r, state="done", pr=pr_b))

    # C: dropped (owner decides not to do it)
    c = issue("e2e C dropped")
    gt("set-record", c, rec(c, "ready"))
    gt("set-record", c, rec(c, "needs-owner"))
    gt("append", c, {"type": "decision", "cause": "intent"}, "owner_events")
    r = json.loads(gt("get-record", c))
    gt("set-record", c, dict(r, state="done"))  # no PR -> closed as not planned

    # D: stuck with the owner (open)
    d = issue("e2e D stuck")
    gt("set-record", d, rec(d, "ready"))
    gt("set-record", d, rec(d, "in-progress"))
    gt("set-record", d, rec(d, "needs-owner"))
    gt("append", d, {"type": "decision", "cause": "rescue"}, "owner_events")

    # Guards that must refuse
    refused = []
    for name, payload, args in (("skip review", rec(d, "done"), ()),):
        r2 = subprocess.run([sys.executable, str(GHTASK), "set-record", REPO, str(d)], input=json.dumps(payload),
                            capture_output=True, text=True, encoding="utf-8")
        refused.append((name, r2.returncode != 0))
    r = json.loads(gt("get-record", b))
    r3 = subprocess.run([sys.executable, str(GHTASK), "set-record", REPO, str(b)],
                        input=json.dumps(dict(r, size_at_triage=8)), capture_output=True, text=True, encoding="utf-8")
    refused.append(("resize after triage", r3.returncode != 0))

    # Window start from GitHub's clock (this PC's clock can be seconds off): the first issue's
    # creation time. Its record comes after it; earlier runs' records come before it.
    since = sh(["gh", "api", f"repos/{REPO}/issues/{a}", "-q", ".created_at"])
    # GitHub links merged PRs to issues and fills timelines a little later: poll until settled.
    for _ in range(12):
        time.sleep(10)
        out = json.loads(sh([sys.executable, str(FORMULA), "--repos", REPO, "--since", since, "--json"]))
        s, tasks = out["summary"], {t["task"].rsplit("#", 1)[1]: t for t in out["tasks"]}
        if s["outcomes"].get("shipped") == 2 and str(a) in tasks:
            break

    checks = {
        "four tasks in cohort": s["cohort"] == 4,
        "outcomes": s["outcomes"] == {"shipped": 2, "dropped": 1, "open": 1},
        "shipped points 2+3": s["shipped_points"] == 5,
        "A first-time right": tasks[str(a)]["ftr"] is True,
        "B fix round recorded and derived": tasks[str(b)]["fix_rounds"] == 1 and tasks[str(b)]["ftr"] is False,
        "Ac = 1 of 2 tasks; 2 of 5 points": s["Ac"]["k"] == 1 and s["Ac"]["n"] == 2 and s["Ac_points"] == 0.4,
        "manager comment does not count as owner": tasks[str(a)]["unknown_owner_events"] == 0,
        # C was dropped by an owner intent decision: left out of Au (v3.1). D is a rescue.
        "Au: A, B autonomous of A, B, D": s["Au"]["k"] == 2 and s["Au"]["n"] == 3,
        "stages sum to lead time": all(abs(sum(t["stages"].values()) - t["lead_h"]) < 1e-6 for t in tasks.values()),
        "worker time from attempts on A": tasks[str(a)]["worker_h_source"] == "attempts",
        "no fetch errors": not s["errors"],
        "refused: done without review": dict(refused)["skip review"],
        "refused: resize after triage": dict(refused)["resize after triage"],
    }
    for k, ok in checks.items():
        print(("ok   " if ok else "FAIL ") + k)
    for n in (d,):
        sh(["gh", "issue", "close", str(n), "-R", REPO, "--reason", "not planned"], check=False)
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        print(json.dumps(s, indent=1, default=str))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
