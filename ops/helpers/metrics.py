"""Ops metrics v3: pure functions over fetched GitHub data (see ops/METRICS.md).

No network and no clock: callers pass `now`. formula.py fetches; tests use fixtures.

Raw task shape:
  {"repo": "owner/name", "number": 7, "created_at": iso, "closed_at": iso | None,
   "state": "OPEN" | "CLOSED", "state_reason": "COMPLETED" | "NOT_PLANNED" | "DUPLICATE" | None,
   "comments": [{"author": login, "created_at": iso, "body": str}],
   "events": [{"event": "labeled" | "unlabeled" | "closed" | "reopened", "label": str | None,
               "created_at": iso}],
   "prs": [{"number": 12, "url": str, "merged_at": iso | None}]}
Context: {"manager_login": str, "owner_login": str, "deploy_repos": [..], "now": iso}
"""
import json
import math
import re
import statistics
from datetime import datetime, timedelta

RECORD_MARK = "<!-- ops-task-record -->"
WORKER_MARK = "<!-- ops-worker -->"
STAGE_ORDER = ["owner", "plan", "build", "review", "blocked", "deploy", "queue"]
STAGE_LABEL = {"needs-owner": "owner", "agent-planning": "plan", "agent-in-progress": "build",
               "agent-review": "review", "blocked": "blocked"}
OWNER_MINUTES = {"answer": 4, "merge": 1, "review": 8, "decision": 6, "access": 10, "unknown": 4}
NOT_AGAINST_AU = {"policy", "required", "access", "intent"}
MATURE_DAYS = 30
SMALL_N = 10


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def hours(a, b):
    return (b - a).total_seconds() / 3600


# ---------- parsing ----------

def parse_record(comments, manager_login):
    """The latest manager-authored task record, or None. Records by anyone else are ignored."""
    found = None
    for c in comments:
        body = c.get("body") or ""
        if c.get("author") != manager_login or RECORD_MARK not in body:
            continue
        if not body.lstrip().startswith("**Task record**"):
            continue  # quoted or pasted text, not a record
        m = re.search(r"```json\s*(\{.*?\})\s*```", body, re.S)
        if not m:
            continue
        try:
            rec = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(rec.get("size"), int) and rec.get("state"):
            found = dict(rec, _created_at=c["created_at"])
    return found


REF_RE = re.compile(r"(?<![\w/.])(?:([\w.-]+/[\w.-]+))?#(\d+)\b(?!\.\d)|github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")


def parse_refs(text, default_repo):
    out = set()
    for m in REF_RE.finditer(text or ""):
        if m.group(2):
            out.add(((m.group(1) or default_repo).lower(), int(m.group(2))))
        else:
            out.add((m.group(3).lower(), int(m.group(4))))
    return out


def norm_id(ref, repo):
    """'owner/repo#4', 'repo#4' or '#4' -> ('owner/repo', 4)."""
    m = re.fullmatch(r"\s*(?:([\w.-]+/)?([\w.-]+))?#(\d+)\s*", ref or "")
    if not m:
        return None
    owner = repo.split("/")[0]
    if m.group(2) and not m.group(1):
        return (f"{owner}/{m.group(2)}".lower(), int(m.group(3)))
    if m.group(1):
        return (f"{m.group(1)}{m.group(2)}".lower(), int(m.group(3)))
    return (repo.lower(), int(m.group(3)))


def escape_links(regressions):
    """Set of (repo, number) for tasks or PRs named on a 'Caused by:' line or in caused_by."""
    linked = set()
    for r in regressions:
        lines = [ln for ln in (r.get("body") or "").splitlines() if re.match(r"\s*caused by\s*:", ln, re.I)]
        text = " ".join(lines + [r.get("caused_by") or ""])
        linked |= parse_refs(text, r["repo"].lower())
    return linked


# ---------- time ----------

def label_timeline(events, start, end):
    """Change points: list of (time, frozenset(active labels), closed_flag), clipped to [start, end]."""
    active, closed, points = set(), False, [(start, frozenset(), False)]
    for e in sorted(events, key=lambda e: e["created_at"]):
        t = ts(e["created_at"])
        if e["event"] == "labeled" and e.get("label"):
            active.add(e["label"])
        elif e["event"] == "unlabeled" and e.get("label"):
            active.discard(e["label"])
        elif e["event"] == "closed":
            closed = True
        elif e["event"] == "reopened":
            closed = False
        else:
            continue
        points.append((min(max(t, start), end), frozenset(active), closed))
    return points


def stage_of(labels, merged, deploy_repo):
    for lab, stage in STAGE_LABEL.items():
        if lab in labels:
            return stage
    if merged and deploy_repo:
        return "deploy"
    return "queue"


def partition(raw, rec, ctx):
    """Exact split of the task's active hours by stage, plus the end time."""
    start = ts(raw["created_at"])
    now = ts(ctx["now"])
    deploy_repo = raw["repo"] in ctx.get("deploy_repos", [])
    merged_at = min((ts(p["merged_at"]) for p in raw.get("prs", []) if p.get("merged_at")), default=None)
    live_at = ts((rec or {}).get("live_at"))
    if deploy_repo:
        end = live_at or (now if raw["state"] == "OPEN" or merged_at else ts(raw["closed_at"]) or now)
    else:
        end = merged_at or ts(raw["closed_at"]) or now
    end = max(end, start)
    points = label_timeline(raw.get("events", []), start, end)
    stages = {s: 0.0 for s in STAGE_ORDER}
    for (t0, labels, closed), (t1, _, _) in zip(points, points[1:] + [(end, None, None)]):
        after_merge = bool(merged_at and t0 >= merged_at)
        if t1 <= t0 or (closed and not (deploy_repo and after_merge)):
            continue  # closed-and-reopened gaps are not task time; deploy time after close is
        is_merged = bool(merged_at and t0 >= merged_at)
        stages[stage_of(labels, is_merged, deploy_repo)] += hours(t0, t1)
    return stages, end, merged_at, live_at


def review_entries(events):
    n, on = 0, False
    for e in sorted(events, key=lambda e: e["created_at"]):
        if e.get("label") != "agent-review":
            continue
        if e["event"] == "labeled" and not on:
            n, on = n + 1, True
        elif e["event"] == "unlabeled":
            on = False
    return n


# ---------- per task ----------

def build_task(raw, rec, ctx, parents, escaped):
    repo = raw["repo"].lower()
    key = (repo, raw["number"])
    stages, end, merged_at, live_at = partition(raw, rec, ctx)
    deploy_repo = raw["repo"] in ctx.get("deploy_repos", [])
    closed_completed = raw["state"] == "CLOSED" and (raw.get("state_reason") or "").upper() == "COMPLETED"
    if raw["state"] == "OPEN":
        outcome = "pending_live" if merged_at and deploy_repo else "open"
    elif (raw.get("state_reason") or "").upper() in ("NOT_PLANNED", "DUPLICATE"):
        outcome = "dropped"
    elif rec.get("state") == "done" and closed_completed and merged_at:
        outcome = "shipped" if (live_at or not deploy_repo) else "pending_live"
    elif rec.get("state") == "done" and closed_completed and rec.get("no_change"):
        outcome = "no_change"
    else:
        outcome = "dropped"

    is_parent = key in parents
    size = rec.get("size_at_triage", rec.get("size"))
    points = 0 if is_parent or rec.get("origin") == "regression-fix" else size
    derived_fix = max(0, review_entries(raw.get("events", [])) - 1)
    fix_rounds = max(rec.get("fix_rounds") or 0, derived_fix)
    attempts = rec.get("attempts") or []
    reopened = sum(1 for e in raw.get("events", []) if e["event"] == "reopened")

    owner_events = list(rec.get("owner_events") or [])
    rec_created = ts(rec["_created_at"])
    for c in raw.get("comments", []):
        if (c.get("author") == ctx["owner_login"] and WORKER_MARK not in (c.get("body") or "")
                and ts(c["created_at"]) > rec_created):
            if not any(abs(hours(ts(o["at"]), ts(c["created_at"]))) < 0.05 for o in owner_events if o.get("at")):
                owner_events.append({"type": "answer", "cause": "unknown", "at": c["created_at"]})
    if stages["owner"] > 0 and not owner_events:
        owner_events.append({"type": "decision", "cause": "unknown"})  # missing data is not success
    owner_min = sum(o.get("est_min", OWNER_MINUTES.get(o.get("type"), 4)) for o in owner_events)
    rescue = any(o.get("cause") not in NOT_AGAINST_AU for o in owner_events)

    pr_keys = {(repo, p["number"]) for p in raw.get("prs", [])}
    worker_h = sum(hours(ts(a["started_at"]), ts(a["finished_at"])) for a in attempts
                   if a.get("started_at") and a.get("finished_at"))
    now = ts(ctx["now"])
    ship_time = live_at or merged_at
    return {
        "task": f"{raw['repo']}#{raw['number']}", "outcome": outcome, "parent": is_parent,
        "points": points if outcome == "shipped" else 0, "size": size, "kind": rec.get("kind"),
        "origin": rec.get("origin", "owner"), "band": "S" if size <= 2 else ("M" if size <= 3 else "L"),
        "model": (attempts[0].get("model") if attempts else rec.get("model")),
        "stages": stages, "active_h": sum(stages.values()),
        "lead_h": hours(ts(raw["created_at"]), end),
        "worker_h": worker_h if worker_h > 0 else None,
        "worker_h_source": "attempts" if worker_h > 0 else "labels",
        "fix_rounds": fix_rounds, "attempts": len(attempts), "reopened": reopened,
        "ftr": outcome == "shipped" and len(attempts) <= 1 and fix_rounds == 0 and reopened == 0,
        "escaped": bool((key in escaped) or (pr_keys & escaped)),
        "mature": bool(ship_time and (now - ship_time) >= timedelta(days=MATURE_DAYS)),
        # Undetermined until the task ends: open tasks count only once they carry an owner event.
        "autonomous": None if (outcome == "open" and not owner_events) else (not rescue and outcome != "dropped"),
        "owner_min": owner_min,
        "owner_by_cause": _by_cause(owner_events),
        "cost_usd": sum(a.get("cost_usd") or 0 for a in attempts) or None,
        "mismatch": derived_fix > (rec.get("fix_rounds") or 0),
        "age_h": hours(ts(raw["created_at"]), now) if outcome == "open" else None,
    }


def _by_cause(events):
    out = {}
    for o in events:
        c = o.get("cause", "unknown")
        out[c] = out.get(c, 0) + o.get("est_min", OWNER_MINUTES.get(o.get("type"), 4))
    return out


# ---------- statistics ----------

def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - h), 3), round(min(1.0, c + h), 3))


def rate(k, n):
    out = {"k": k, "n": n, "ci95": wilson(k, n)}
    out["value"] = round(k / n, 3) if n else None
    out["show"] = (f"{k} of {n}" if n < SMALL_N else f"{round(100 * k / n)}%") if n else "no data"
    return out


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (i - lo), 2)


# ---------- cohort ----------

def compute(raw_tasks, regressions, ctx, since, until):
    """Returns (tasks, summary) for the cohort accepted in [since, until)."""
    s, u = ts(since), ts(until)
    recs = {}
    for raw in raw_tasks:
        rec = parse_record(raw.get("comments", []), ctx["manager_login"])
        if rec:
            recs[(raw["repo"].lower(), raw["number"])] = (raw, rec)
    parents = set()
    for (repo, _), (_, rec) in recs.items():
        if rec.get("parent"):
            p = norm_id(rec["parent"], repo)
            if p:
                parents.add(p)
    escaped = escape_links(regressions)
    tasks = []
    for (_, _), (raw, rec) in recs.items():
        accepted = ts(rec["_created_at"])
        if s <= accepted < u:
            tasks.append(build_task(raw, rec, ctx, parents, escaped))
    return tasks, summarise(tasks, s, u)


def summarise(tasks, since, until):
    leaves = [t for t in tasks if not t["parent"]]
    shipped = [t for t in leaves if t["outcome"] == "shipped"]
    pts = sum(t["points"] for t in shipped)
    ftr_pts = sum(t["points"] for t in shipped if t["ftr"])
    H = sum(t["active_h"] for t in leaves)
    stage_h = {st: round(sum(t["stages"][st] for t in leaves), 2) for st in STAGE_ORDER}
    weeks = max(hours(since, until) / 168, 1e-9)
    mature = [t for t in shipped if t["mature"]]
    worker = [t for t in shipped if t["worker_h"]]
    costs = [t["cost_usd"] for t in leaves if t["cost_usd"]]
    outcomes = {}
    for t in leaves:
        outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
    by_cause = {}
    for t in leaves:
        for c, m in t["owner_by_cause"].items():
            by_cause[c] = by_cause.get(c, 0) + m
    bands = {}
    for b in ("S", "M", "L"):
        bt = [t for t in shipped if t["band"] == b]
        bands[b] = {"n": len(bt), "lead_p50_h": pct([t["lead_h"] for t in bt], 0.5),
                    "lead_p85_h": pct([t["lead_h"] for t in bt], 0.85)}
    return {
        "cohort": len(leaves), "parents": len(tasks) - len(leaves), "outcomes": outcomes,
        "shipped_points": pts, "ftr_points": ftr_pts,
        "E": round(ftr_pts / H, 4) if H else None,
        "h_per_point": round(H / pts, 3) if pts else None,
        "h_by_stage_per_point": {k: round(v / pts, 3) for k, v in stage_h.items()} if pts else None,
        "stage_hours": stage_h, "H": round(H, 2),
        "Ac": rate(ftr_pts, pts) | {"unit": "points"},
        "Au": rate(sum(1 for t in leaves if t["autonomous"]), sum(1 for t in leaves if t["autonomous"] is not None)),
        "escape_rate": rate(sum(t["escaped"] for t in mature), len(mature)) | {"provisional": len(shipped) - len(mature)},
        "S": round(sum(t["points"] for t in worker) / sum(t["worker_h"] for t in worker), 3) if worker else None,
        "S_source": "attempts" if worker else "not recorded",
        "volume_ftr_points_per_week": round(ftr_pts / weeks, 2),
        "lead_by_band": bands,
        "wip": outcomes.get("open", 0), "oldest_open_h": max((t["age_h"] for t in leaves if t["age_h"]), default=None),
        "owner_minutes": sum(t["owner_min"] for t in leaves), "owner_minutes_by_cause": by_cause,
        "cost_per_ftr_point": round(sum(costs) / ftr_pts, 2) if costs and ftr_pts else None,
        "self_made_share": rate(sum(1 for t in leaves if t["origin"] != "owner"), len(leaves)),
        "record_mismatches": sum(t["mismatch"] for t in leaves),
    }


# ---------- routing decision ----------

def routing_decision(current, other, threshold=0.70, min_n=20, cost_ratio=1.5):
    """current/other: {"model", "k", "n", "cost_per_ftr_point"} within one band.
    Returns "keep", "trial" (alternate tasks to gather data) or "switch". Same rule both ways."""
    if current["n"] < min_n:
        return "keep"
    lo, hi = wilson(current["k"], current["n"])
    if hi >= threshold:
        return "keep"
    if other is None or other["n"] < min_n:
        return "trial"
    o_lo, _ = wilson(other["k"], other["n"])
    if o_lo <= hi:
        return "trial"
    cc, oc = current.get("cost_per_ftr_point"), other.get("cost_per_ftr_point")
    if cc and oc and oc > cost_ratio * cc:
        return "keep"
    return "switch"
