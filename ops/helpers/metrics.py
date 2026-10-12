"""Ops metrics v3.1: pure functions over fetched GitHub data (see ops/METRICS.md).

No network and no clock: callers pass `now`. formula.py fetches; tests use fixtures.

Raw task shape:
  {"repo": "owner/name", "number": 7, "created_at": iso, "closed_at": iso | None,
   "state": "OPEN" | "CLOSED", "state_reason": "COMPLETED" | "NOT_PLANNED" | "DUPLICATE" | None,
   "comments": [{"author": login, "created_at": iso, "body": str}],
   "events": [{"event": "labeled" | "unlabeled" | "closed" | "reopened", "label": str | None,
               "created_at": iso}],
   "prs": [{"number": 12, "url": str, "merged_at": iso | None}]}
Regression shape: {"repo", "number", "created_at", "body"}
Context: {"manager_login": str, "manager_login_by_repo": {repo: login}, "owner_login": str,
          "deploy_repos": [..], "now": iso}
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone

RECORD_MARK = "<!-- ops-task-record -->"
WORKER_MARK = "<!-- ops-worker -->"
STAGE_ORDER = ["owner", "plan", "build", "review", "blocked", "deploy", "queue"]
STAGE_LABEL = {"needs-owner": "owner", "agent-planning": "plan", "agent-in-progress": "build",
               "agent-review": "review", "blocked": "blocked"}
NOT_IN_H = {"blocked"}  # waiting on another task is lead time, not work time
OWNER_MINUTES = {"answer": 4, "merge": 1, "review": 8, "decision": 6, "access": 10, "unknown": 4}
NOT_AGAINST_AU = {"policy", "required", "access", "intent"}
FAILED_RESULTS = {"stuck", "fail", "failed", "error"}
MATURE_DAYS = 30
PENDING_CAP_H = 72  # deploy time beyond this is a health problem, not task time
OWNER_MATCH_H = 6   # a recorded owner event explains owner comments within this many hours
SMALL_N = 10


def ts(s):
    if not s:
        return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def hours(a, b):
    return (b - a).total_seconds() / 3600


# ---------- parsing ----------

def parse_record(comments, manager_login):
    """Latest manager-authored task record (content), with `_created_at` = the earliest valid
    record comment (acceptance time). Records by anyone else, quoted or malformed, are ignored."""
    found, first = None, None
    for c in sorted(comments, key=lambda c: c["created_at"]):
        body = c.get("body") or ""
        if c.get("author") != manager_login or RECORD_MARK not in body:
            continue
        if not body.lstrip().startswith("**Task record**"):
            continue
        m = re.search(r"```json\s*(\{.*?\})\s*```", body, re.S)
        if not m:
            continue
        try:
            rec = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("size"), int) and rec.get("state"):
            first = first or c["created_at"]
            found = rec
    return dict(found, _created_at=first) if found else None


REF_RE = re.compile(r"(?<![\w/.])(?:([\w.-]+/[\w.-]+))?#(\d+)\b(?!\.\d)"
                    r"|github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")
CAUSED_LINE = re.compile(r"^[\s>*_-]*caused\s+by\b[\s*_]*:?", re.I)


def parse_refs(text, default_repo):
    out = set()
    for m in REF_RE.finditer(text or ""):
        if m.group(2):
            out.add(((m.group(1) or default_repo).lower(), int(m.group(2))))
        else:
            out.add((m.group(3).lower(), int(m.group(4))))
    return out


def norm_id(ref, repo):
    """'owner/repo#4', 'repo#4', '#4' or an issue URL -> ('owner/repo', 4)."""
    ref = (ref or "").strip()
    m = re.search(r"github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)", ref)
    if m:
        return (m.group(1).lower(), int(m.group(2)))
    m = re.fullmatch(r"(?:([\w.-]+)/)?([\w.-]+)?#(\d+)", ref)
    if not m:
        return None
    owner = m.group(1) or repo.split("/")[0]
    name = m.group(2) or repo.split("/")[1]
    return (f"{owner}/{name}".lower(), int(m.group(3)))


def escape_links(regressions, records=()):
    """{(repo, number): earliest regression time} for tasks or PRs named as the cause."""
    linked = {}

    def add(keys, when):
        for k in keys:
            if k not in linked or (when and when < linked[k]):
                linked[k] = when

    for r in regressions:
        lines = [ln for ln in (r.get("body") or "").splitlines() if CAUSED_LINE.match(ln)]
        add(parse_refs(" ".join(CAUSED_LINE.sub("", ln) for ln in lines), r["repo"].lower()), ts(r.get("created_at")))
    for repo, rec, created in records:
        if rec.get("caused_by"):
            add(parse_refs(rec["caused_by"], repo), ts(created))
    return linked


# ---------- time ----------

def change_points(events, start, end, extra=()):
    """[(time, labels, closed)] clipped to [start, end]; extra times split segments."""
    active, closed, pts = set(), False, [(start, frozenset(), False)]
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
        pts.append((min(max(t, start), end), frozenset(active), closed))
    for x in extra:
        if x and start < x < end:
            prev = [p for p in pts if p[0] <= x][-1]
            pts.append((x, prev[1], prev[2]))
    return sorted(pts, key=lambda p: p[0])


def stage_of(labels, after_merge, deploy_repo):
    if "needs-owner" in labels:
        return "owner"
    if after_merge and deploy_repo:
        return "deploy"
    for lab, stage in STAGE_LABEL.items():
        if lab in labels:
            return stage
    return "queue"


def partition(raw, rec, ctx):
    """Exact split of the task's elapsed hours by stage, plus timing facts."""
    start = ts(raw["created_at"])
    now = ts(ctx["now"])
    deploy_repo = raw["repo"] in ctx.get("deploy_repos", [])
    pr_merges = [ts(p["merged_at"]) for p in raw.get("prs", []) if p.get("merged_at")]
    merged_at = max(pr_merges + ([ts(rec.get("merged_at"))] if rec.get("merged_at") else []), default=None)
    live_at = ts(rec.get("live_at")) if deploy_repo else None
    if deploy_repo and merged_at:
        end = live_at or now
    elif merged_at:
        end = merged_at
    else:
        end = ts(raw["closed_at"]) or now
    end = max(end, start)
    pts = change_points(raw.get("events", []), start, end, extra=(merged_at, live_at))
    stages = {s: 0.0 for s in STAGE_ORDER}
    for (t0, labels, closed), (t1, _, _) in zip(pts, pts[1:] + [(end, None, None)]):
        after_merge = bool(merged_at and t0 >= merged_at)
        if t1 <= t0 or (closed and not (deploy_repo and after_merge)):
            continue  # closed-and-reopened gaps are not task time; deploy time after close is
        stages[stage_of(labels, after_merge, deploy_repo)] += hours(t0, t1)
    return stages, end, merged_at, live_at, deploy_repo


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

def outcome_of(raw, rec, merged_at, live_at, deploy_repo, has_children):
    reason = (raw.get("state_reason") or "").upper()
    closed_ok = raw["state"] == "CLOSED" and reason == "COMPLETED"
    if has_children:
        return "parent_done" if (rec.get("state") == "done" and closed_ok) else ("open" if raw["state"] == "OPEN" else "dropped")
    if merged_at and deploy_repo and not live_at:
        return "pending_live"
    if raw["state"] == "OPEN":
        return "open"
    if reason in ("NOT_PLANNED", "DUPLICATE"):
        return "dropped"
    if rec.get("state") == "done" and closed_ok and merged_at:
        return "shipped"
    if rec.get("state") == "done" and closed_ok and rec.get("no_change"):
        return "no_change"
    return "dropped"


def owner_events_of(raw, rec, ctx, end, stages):
    events = [dict(o) for o in rec.get("owner_events") or [] if isinstance(o, dict)]
    rec_created = ts(rec["_created_at"])
    recorded_at = [ts(o.get("at")) for o in events if o.get("at")]
    for c in raw.get("comments", []):
        t = ts(c["created_at"])
        if (c.get("author") == ctx["owner_login"] and WORKER_MARK not in (c.get("body") or "")
                and rec_created < t <= end):
            if not any(abs(hours(a, t)) <= OWNER_MATCH_H for a in recorded_at):
                events.append({"type": "answer", "cause": "unknown", "at": c["created_at"], "derived": True})
    missing = (rec.get("owner_touches") or 0) - len(events)
    events += [{"type": "unknown", "cause": "unknown", "derived": True}] * max(0, missing)
    if stages["owner"] > 0 and not events:
        events.append({"type": "decision", "cause": "unknown", "derived": True})
    return events


def build_task(raw, rec, ctx, parents, escaped):
    repo = raw["repo"].lower()
    key = (repo, raw["number"])
    stages, end, merged_at, live_at, deploy_repo = partition(raw, rec, ctx)
    is_parent = key in parents or bool(rec.get("children"))
    outcome = outcome_of(raw, rec, merged_at, live_at, deploy_repo, is_parent)
    now = ts(ctx["now"])

    size = rec.get("size_at_triage")
    origin = rec.get("origin") or "unknown"
    missing = [f for f, v in (("size_at_triage", size), ("origin", rec.get("origin"))) if v is None]
    points = 0 if (is_parent or origin in ("regression-fix", "unknown") or size is None) else size

    derived_fix = max(0, review_entries(raw.get("events", [])) - 1)
    fix_rounds = max(rec.get("fix_rounds") or 0, derived_fix)
    attempts = [a for a in rec.get("attempts") or [] if isinstance(a, dict)]
    failed_attempt = any((a.get("result") or "").lower() in FAILED_RESULTS for a in attempts)
    reopened = sum(1 for e in raw.get("events", []) if e["event"] == "reopened")

    owner_events = owner_events_of(raw, rec, ctx, end, stages)
    for o in owner_events:  # a struggle followed by an "intent" question is still a rescue
        if (fix_rounds or failed_attempt) and o.get("cause") in ("intent", "access", "required"):
            o["cause"] = "rescue"
    owner_min = sum(o.get("est_min") or OWNER_MINUTES.get(o.get("type"), 4) for o in owner_events)
    rescue = any(o.get("cause") not in NOT_AGAINST_AU for o in owner_events)
    intent_drop = outcome == "dropped" and any(o.get("cause") == "intent" for o in owner_events) and not rescue
    if outcome in ("open", "pending_live") and not rescue:
        autonomous = None  # undetermined until the task ends
    elif intent_drop:
        autonomous = None  # the owner changed their mind: not the agents' success or failure
    else:
        autonomous = not rescue and outcome != "dropped"

    # Work hours (H): no blocked time, no no-change answers, pending deploys capped.
    h_stages = {s: (0.0 if s in NOT_IN_H else v) for s, v in stages.items()}
    if outcome == "pending_live":
        h_stages["deploy"] = min(h_stages["deploy"], PENDING_CAP_H)
    if outcome == "no_change":
        h_stages = {s: 0.0 for s in h_stages}

    pr_keys = {(repo, p["number"]) for p in raw.get("prs", [])}
    ship_time = live_at or merged_at
    esc_times = [escaped[k] for k in ({key} | pr_keys) if k in escaped]
    escaped_flag = bool(ship_time and any(t is None or hours(ship_time, t) <= 24 * MATURE_DAYS for t in esc_times))
    worker_h = sum(hours(ts(a["started_at"]), ts(a["finished_at"])) for a in attempts
                   if a.get("started_at") and a.get("finished_at"))
    shipped = outcome == "shipped"
    return {
        "task": f"{raw['repo']}#{raw['number']}", "outcome": outcome, "parent": is_parent,
        "points": points if shipped else 0, "size": size, "kind": rec.get("kind"), "origin": origin,
        "band": "?" if size is None else ("S" if size <= 2 else ("M" if size <= 3 else "L")),
        "model": attempts[0].get("model") if attempts else rec.get("model"),
        "stages": stages, "h_stages": h_stages, "H": sum(h_stages.values()),
        "lead_h": hours(ts(raw["created_at"]), end),
        "worker_h": worker_h if worker_h > 0 else (stages["build"] if shipped and stages["build"] > 0 else None),
        "worker_h_source": "attempts" if worker_h > 0 else "labels",
        "fix_rounds": fix_rounds, "attempts": len(attempts), "reopened": reopened,
        "ftr": shipped and len(attempts) == 1 and fix_rounds == 0 and reopened == 0 and not failed_attempt,
        "escaped": escaped_flag,
        "mature": bool(ship_time and (now - ship_time) >= timedelta(days=MATURE_DAYS)),
        "autonomous": autonomous, "owner_min": owner_min, "owner_by_cause": _by_cause(owner_events),
        "unknown_owner_events": sum(1 for o in owner_events if o.get("cause") == "unknown"),
        "cost_usd": sum(a.get("cost_usd") or 0 for a in attempts) or None,
        "mismatch": derived_fix > (rec.get("fix_rounds") or 0) or bool(missing),
        "missing_fields": missing,
        "age_h": hours(ts(raw["created_at"]), now) if outcome in ("open", "pending_live") else None,
        "ended": outcome not in ("open", "pending_live"),
    }


def _by_cause(events):
    out = {}
    for o in events:
        c = o.get("cause", "unknown")
        out[c] = out.get(c, 0) + (o.get("est_min") or OWNER_MINUTES.get(o.get("type"), 4))
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
    """A task-count rate (each task is one trial)."""
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
        login = ctx.get("manager_login_by_repo", {}).get(raw["repo"], ctx["manager_login"])
        try:
            rec = parse_record(raw.get("comments", []), login)
        except Exception:  # noqa: BLE001 - a bad record must not stop the report
            rec = None
        if rec:
            recs[(raw["repo"].lower(), raw["number"])] = (raw, rec)
    parents = set()
    for (repo, _), (_, rec) in recs.items():
        p = norm_id(rec.get("parent"), repo) if rec.get("parent") else None
        if p:
            parents.add(p)
    escaped = escape_links(regressions, [(r, rec, raw["created_at"]) for (r, _), (raw, rec) in recs.items()])
    tasks = []
    for (repo, n), (raw, rec) in recs.items():
        if not (s <= ts(rec["_created_at"]) < u):
            continue
        try:
            tasks.append(build_task(raw, rec, ctx, parents, escaped))
        except Exception as e:  # noqa: BLE001
            tasks.append({"task": f"{raw['repo']}#{n}", "outcome": "bad_record", "reason": str(e)[:200],
                          "parent": False, "points": 0, "H": 0, "ended": True, "autonomous": None})
    return tasks, summarise(tasks, s, u)


def routing_table(tasks):
    """{band: {model: {"k": successes, "n": tasks}}}: success = first-time right and, for mature
    tasks, not escaped. Feed one band's rows to routing_decision."""
    table = {}
    for t in tasks:
        if t.get("outcome") != "shipped" or not t.get("model"):
            continue
        row = table.setdefault(t["band"], {}).setdefault(t["model"], {"model": t["model"], "k": 0, "n": 0})
        row["n"] += 1
        row["k"] += int(t["ftr"] and not (t["mature"] and t["escaped"]))
    return table


def summarise(tasks, since, until):
    good = [t for t in tasks if t["outcome"] != "bad_record"]
    leaves = [t for t in good if not t["parent"]]
    shipped = [t for t in leaves if t["outcome"] == "shipped"]
    pts = sum(t["points"] for t in shipped)
    ftr_pts = sum(t["points"] for t in shipped if t["ftr"])
    H = sum(t["H"] for t in good)  # parents' own hours (before the split) count; blocked never
    stage_h = {st: round(sum(t["h_stages"][st] for t in good), 2) for st in STAGE_ORDER}
    weeks = max(hours(since, until) / 168, 1e-9)
    mature = [t for t in shipped if t["mature"]]
    worker = [t for t in shipped if t["worker_h"]]
    costs = [t["cost_usd"] for t in good if t.get("cost_usd")]
    outcomes = {}
    for t in tasks:
        outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
    by_cause = {}
    for t in good:
        for c, m in t["owner_by_cause"].items():
            by_cause[c] = by_cause.get(c, 0) + m
    au_k = sum(1 for t in good if t["autonomous"])
    au_n = sum(1 for t in good if t["autonomous"] is not None)
    undetermined = sum(1 for t in good if t["autonomous"] is None and not t["ended"])
    bands = {}
    for b in ("S", "M", "L"):
        bt = [t for t in shipped if t["band"] == b]
        bw = [t for t in bt if t["worker_h"]]
        bands[b] = {"n": len(bt), "lead_p50_h": pct([t["lead_h"] for t in bt], 0.5),
                    "lead_p85_h": pct([t["lead_h"] for t in bt], 0.85),
                    "S": round(sum(t["points"] for t in bw) / sum(t["worker_h"] for t in bw), 3) if bw else None}
    ended = sum(1 for t in good if t["ended"])
    queue_h = stage_h["queue"]
    return {
        "cohort": len(leaves), "parents": len(good) - len(leaves), "bad_records": len(tasks) - len(good),
        "outcomes": outcomes, "ended": f"{ended} of {len(good)}", "provisional": ended < len(good),
        "shipped_points": pts, "ftr_points": ftr_pts,
        "E": round(ftr_pts / H, 4) if H else None,
        "E_service": round(ftr_pts / (H - queue_h), 4) if H - queue_h > 0 else None,
        "h_per_point": round(H / pts, 3) if pts else None,
        "h_by_stage_per_point": {k: round(v / pts, 3) for k, v in stage_h.items()} if pts else None,
        "stage_hours": stage_h, "H": round(H, 2),
        "Ac": rate(sum(1 for t in shipped if t["ftr"]), len(shipped)) | {"unit": "tasks"},
        "Ac_points": round(ftr_pts / pts, 3) if pts else None,
        "Au": rate(au_k, au_n) | {"bounds": (round(au_k / (au_n + undetermined), 3),
                                            round((au_k + undetermined) / (au_n + undetermined), 3))
                                  if au_n + undetermined else None,
                                  "undetermined": undetermined},
        "escape_rate": rate(sum(t["escaped"] for t in mature), len(mature)) | {"provisional": len(shipped) - len(mature)},
        "S": round(sum(t["points"] for t in worker) / sum(t["worker_h"] for t in worker), 3) if worker else None,
        "S_source": ("attempts" if all(t["worker_h_source"] == "attempts" for t in worker) else "mixed (labels)")
        if worker else "not recorded",
        "volume_ftr_points_per_week": round(ftr_pts / weeks, 2),
        "by_band": bands,
        "wip": outcomes.get("open", 0), "pending_live": outcomes.get("pending_live", 0),
        "oldest_open_h": max((t["age_h"] for t in good if t.get("age_h")), default=None),
        "owner_minutes": sum(t["owner_min"] for t in good), "owner_minutes_by_cause": by_cause,
        "unknown_owner_events": sum(t["unknown_owner_events"] for t in good),
        "cost_per_ftr_point": round(sum(costs) / ftr_pts, 2) if costs and ftr_pts else None,
        "self_made_share": rate(sum(1 for t in leaves if t["origin"] not in ("owner", "unknown")), len(leaves)),
        "record_mismatches": sum(t["mismatch"] for t in good),
        "routing_table": routing_table(good),
    }


# ---------- routing decision ----------

def routing_decision(current, other, threshold=0.70, min_n=20, cost_ratio=1.5):
    """current/other: {"model", "k", "n", "cost_per_ftr_point"} within one band, in TASKS
    (from routing_table). Returns "keep", "trial" (alternate tasks to gather data) or "switch".
    Same rule in both directions."""
    if current["n"] < min_n:
        return "keep"
    _, hi = wilson(current["k"], current["n"])
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
