#!/usr/bin/env python3
"""Ops metrics v3 report (definitions: ops/METRICS.md; computation: metrics.py).

  formula.py --repos owner/a owner/b [--since ISO] [--until ISO] [--json]
             [--dump-raw FILE] [--from-json FILE]

Fetches from GitHub with the gh CLI (paginated), then calls the pure metrics.compute().
--dump-raw saves the fetched data; --from-json replays it without network (for tests and
reproducible reports). Errors per issue are collected and reported, not fatal.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics  # noqa: E402

OPS_LABELS = {"agent-ready", "agent-in-progress", "agent-planning", "agent-review", "needs-owner", "blocked"}
CONFIG = Path.home() / ".claude" / "ops" / "config.json"


class FetchError(Exception):
    pass


def gh_paged(path):
    r = subprocess.run(["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True,
                       encoding="utf-8")
    if r.returncode != 0:
        raise FetchError(f"{path}: {r.stderr.strip()[:200]}")
    pages = json.loads(r.stdout or "[]")
    return [item for page in pages for item in (page if isinstance(page, list) else [page])]


def gh_json(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise FetchError(f"{' '.join(args[:4])}: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout or "null")


def fetch_task(repo, issue):
    n = issue["number"]
    comments = [{"author": (c.get("user") or {}).get("login"), "created_at": c["created_at"], "body": c.get("body") or ""}
                for c in gh_paged(f"repos/{repo}/issues/{n}/comments?per_page=100")]
    if not any(metrics.RECORD_MARK in c["body"] for c in comments):
        return None
    events = []
    for e in gh_paged(f"repos/{repo}/issues/{n}/timeline?per_page=100"):
        if e.get("event") in ("labeled", "unlabeled", "closed", "reopened"):
            events.append({"event": e["event"], "label": (e.get("label") or {}).get("name"),
                           "created_at": e["created_at"]})
    prs = {}
    view = gh_json("issue", "view", str(n), "-R", repo, "--json", "closedByPullRequestsReferences")
    refs = [p.get("url") for p in (view or {}).get("closedByPullRequestsReferences") or []]
    rec = metrics.parse_record(comments, ME)
    if rec and rec.get("pr"):
        refs.append(str(rec["pr"]))
    for ref in refs:
        if not ref:
            continue
        try:
            p = gh_json("pr", "view", ref, "-R", repo, "--json", "number,url,mergedAt")
            prs[p["number"]] = {"number": p["number"], "url": p["url"], "merged_at": p.get("mergedAt")}
        except FetchError as e:
            if "not found" not in str(e).lower() and "could not resolve" not in str(e).lower():
                raise
    return {"repo": repo, "number": n, "created_at": issue["created_at"], "closed_at": issue.get("closed_at"),
            "state": issue["state"].upper(), "state_reason": (issue.get("state_reason") or "").upper() or None,
            "comments": comments, "events": events, "prs": list(prs.values())}


def fetch(repos, since):
    raws, regs, errors = [], [], []
    for repo in repos:
        try:
            issues = [i for i in gh_paged(f"repos/{repo}/issues?state=all&per_page=100&since={since}")
                      if "pull_request" not in i]
        except FetchError as e:
            errors.append(str(e))
            continue
        for issue in issues:
            labels = {l["name"] for l in issue.get("labels", [])}
            if "agent-regression" in labels:
                regs.append({"repo": repo, "number": issue["number"], "body": issue.get("body") or "",
                             "created_at": issue["created_at"]})
            try:
                t = fetch_task(repo, issue)
                if t:
                    raws.append(t)
            except FetchError as e:
                errors.append(f"{repo}#{issue['number']}: {e}")
    return raws, regs, errors


def load_ctx(now):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    deploy = [r for r, v in cfg.get("repos", {}).items() if v.get("deploy") == "auto"]  # paused = not deploying
    return {"manager_login": ME, "owner_login": ME, "deploy_repos": deploy, "now": now}


def main():
    global ME
    a = argparse.ArgumentParser()
    a.add_argument("--repos", nargs="*", default=[])
    a.add_argument("--since")
    a.add_argument("--until")
    a.add_argument("--json", action="store_true")
    a.add_argument("--dump-raw")
    a.add_argument("--from-json")
    args = a.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now(timezone.utc)
    until = args.until or now.isoformat()
    since = args.since or (now - timedelta(days=7)).isoformat()
    if not args.from_json and not args.repos:
        a.error("--repos is required (or --from-json)")
    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        ME = data["ctx"]["manager_login"]
        raws, regs, errors, ctx = data["raws"], data["regressions"], data.get("errors", []), data["ctx"]
    else:
        ME = gh_json("api", "user")["login"]
        # Fetch issues updated since the cohort start minus the escape window, so escapes link.
        fetch_since = (datetime.fromisoformat(since) - timedelta(days=metrics.MATURE_DAYS)).isoformat()
        raws, regs, errors = fetch(args.repos, fetch_since)
        ctx = load_ctx(now.isoformat())
    if args.dump_raw:
        Path(args.dump_raw).write_text(json.dumps({"ctx": ctx, "raws": raws, "regressions": regs, "errors": errors},
                                                  indent=1), encoding="utf-8")
    tasks, s = metrics.compute(raws, regs, ctx, since, until)
    s["errors"] = errors
    if args.json:
        print(json.dumps({"summary": s, "tasks": tasks}, indent=1, default=str))
        return
    print(f"Cohort {since[:10]} to {until[:10]}: {s['cohort']} tasks ({s['parents']} parents excluded); outcomes {s['outcomes']}")
    print(f"Ended {s['ended']}{'  (PROVISIONAL)' if s['provisional'] else ''}; pending live {s['pending_live']}")
    print(f"E {s['E']} FTR points per task-hour (= Ac {s['Ac_points']} by points / {s['h_per_point']} h per point); "
          f"E without queue {s['E_service']}")
    print(f"Ac {s['Ac']['show']} tasks ci {s['Ac']['ci95']}")
    print(f"Au {s['Au']['show']} ci {s['Au']['ci95']} bounds {s['Au']['bounds']} ({s['Au']['undetermined']} undetermined)")
    print(f"Owner minutes {s['owner_minutes']} {s['owner_minutes_by_cause']}; unknown-cause events {s['unknown_owner_events']}")
    print(f"Escapes {s['escape_rate']['show']} (mature), {s['escape_rate']['provisional']} still provisional")
    print(f"Hours per point by stage {s['h_by_stage_per_point']}")
    print(f"By size band {s['by_band']}   WIP {s['wip']}  oldest open {s['oldest_open_h']} h")
    print(f"S {s['S']} ({s['S_source']})   cost per FTR point {s['cost_per_ftr_point']}   "
          f"self-made {s['self_made_share']['show']}   record mismatches {s['record_mismatches']}   "
          f"bad records {s['bad_records']}")
    if errors:
        print(f"{len(errors)} fetch errors (first: {errors[0]})")


ME = None

if __name__ == "__main__":
    main()
