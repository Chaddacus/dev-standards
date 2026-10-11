#!/usr/bin/env python3
"""Formula v2 from GitHub (system of record for local and cloud work). Local runner; uses gh.

  formula.py --repos owner/a owner/b [--days 30] [--json]

Per closed task (issue with an ops task record):
  lead time Δ   = opened -> closed, split into stages from label timestamps:
                  queue (opened -> first agent-in-progress), build (in-progress -> agent-review),
                  verify (agent-review -> closed), owner wait (time carrying needs-owner)
  points        = record size (1,2,3,5,8)
  autonomous    = never carried needs-owner and owner_touches == 0
  correct       = fix_rounds == 0 and no agent-regression issue references the task
Totals:
  E  = points of correct tasks / calendar hours in the window
  Au = autonomous tasks / tasks       Ac = correct tasks / tasks
  S  = points / build hours           Δ  = median lead time (and median per stage)
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone

STAGE_LABELS = {"agent-in-progress", "agent-review", "needs-owner"}


def gh_json(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])}: {r.stderr.strip()}")
    return json.loads(r.stdout or "null")


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def hours(a, b):
    return max((b - a).total_seconds() / 3600, 0.0)


def record_of(repo, n):
    for c in gh_json("api", f"repos/{repo}/issues/{n}/comments?per_page=100"):
        if "<!-- ops-task-record -->" in c["body"]:
            m = re.search(r"```json\s*(\{.*?\})\s*```", c["body"], re.S)
            if m:
                return json.loads(m.group(1))
    return None


def label_intervals(repo, n, closed_at):
    """{label: [(start, end), ...]} from the issue timeline."""
    events = gh_json("api", f"repos/{repo}/issues/{n}/timeline?per_page=100",
                     "-H", "Accept: application/vnd.github+json")
    open_at, out = {}, {}
    for e in events or []:
        lab = (e.get("label") or {}).get("name")
        if lab not in STAGE_LABELS:
            continue
        if e["event"] == "labeled":
            open_at.setdefault(lab, ts(e["created_at"]))
        elif e["event"] == "unlabeled" and lab in open_at:
            out.setdefault(lab, []).append((open_at.pop(lab), ts(e["created_at"])))
    for lab, start in open_at.items():
        out.setdefault(lab, []).append((start, closed_at))
    return out


def regressions(repo):
    issues = gh_json("issue", "list", "-R", repo, "--label", "agent-regression", "--state", "all",
                     "-L", "500", "--json", "body,title") or []
    return " ".join(i["title"] + " " + (i["body"] or "") for i in issues)


def analyse(repos, days):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    tasks = []
    for repo in repos:
        reg_text = regressions(repo)
        closed = gh_json("issue", "list", "-R", repo, "--state", "closed", "-L", "500",
                         "--search", f"closed:>={since.date().isoformat()}",
                         "--json", "number,createdAt,closedAt") or []
        for i in closed:
            rec = record_of(repo, i["number"])
            if not rec:
                continue
            opened, closed_at = ts(i["createdAt"]), ts(i["closedAt"])
            iv = label_intervals(repo, i["number"], closed_at)
            prog = iv.get("agent-in-progress", [])
            rev = iv.get("agent-review", [])
            first_prog = min((s for s, _ in prog), default=None)
            first_rev = min((s for s, _ in rev), default=None)
            owner = sum(hours(s, e) for s, e in iv.get("needs-owner", []))
            ref = re.compile(rf"(#{i['number']}\b|{re.escape(rec.get('pr') or '~none~')})")
            tasks.append({
                "task": f"{repo}#{i['number']}", "points": rec["size"], "kind": rec["kind"],
                "model": rec.get("model"),
                "lead_h": hours(opened, closed_at),
                "queue_h": hours(opened, first_prog) if first_prog else None,
                "build_h": sum(hours(s, e) for s, e in prog),
                "verify_h": hours(first_rev, closed_at) if first_rev else None,
                "owner_h": owner,
                "autonomous": not iv.get("needs-owner") and not rec.get("owner_touches"),
                "correct": not rec.get("fix_rounds") and not ref.search(reg_text),
            })
    return tasks, since


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def summarise(tasks, since):
    window_h = hours(since, datetime.now(timezone.utc))
    n = len(tasks)
    build_h = sum(t["build_h"] for t in tasks)
    pts = sum(t["points"] for t in tasks)
    return {
        "tasks": n, "points": pts, "window_hours": round(window_h, 1),
        "E_points_per_hour": round(sum(t["points"] for t in tasks if t["correct"]) / window_h, 3) if window_h else None,
        "Au": round(sum(t["autonomous"] for t in tasks) / n, 3) if n else None,
        "Ac": round(sum(t["correct"] for t in tasks) / n, 3) if n else None,
        "S_points_per_build_hour": round(pts / build_h, 3) if build_h else None,
        "delta_median_h": med(t["lead_h"] for t in tasks),
        "stages_median_h": {k: med(t[f"{k}_h"] for t in tasks) for k in ("queue", "build", "verify", "owner")},
    }


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--repos", nargs="+", required=True)
    a.add_argument("--days", type=int, default=30)
    a.add_argument("--json", action="store_true")
    args = a.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    tasks, since = analyse(args.repos, args.days)
    out = {"summary": summarise(tasks, since), "tasks": tasks}
    if args.json:
        print(json.dumps(out, indent=1, default=str))
        return
    s = out["summary"]
    print(f"{s['tasks']} tasks, {s['points']} points in the last {args.days} days")
    print(f"E  {s['E_points_per_hour']} correct points/hour   Au {s['Au']}   Ac {s['Ac']}   "
          f"S {s['S_points_per_build_hour']} points/build-hour")
    print(f"Δ  median {s['delta_median_h']} h; stages (median h): {s['stages_median_h']}")


if __name__ == "__main__":
    main()
