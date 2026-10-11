#!/usr/bin/env python3
"""LLM effectiveness tracker.

Measures E = Ac / Delta  (working changes per hour), where
  Delta = write time + wait-on-human time + test/validate time
  Ac    = share of tasks that worked on the first try

Runs are stored in ~/.claude/llm-effectiveness/runs.json
(override with LLM_EFF_DATA). Standard library only.
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

PHASES = ("write", "wait", "test")
DATA = Path(os.environ.get("LLM_EFF_DATA", Path.home() / ".claude" / "llm-effectiveness" / "runs.json"))
SAFE_ACCURACY = 0.85


class FileLock:
    """Cross-process lock so parallel agents can log runs safely."""

    def __init__(self, path):
        self.path = Path(str(path) + ".lock")

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + 30
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 60:
                        self.path.unlink()  # stale lock from a killed process
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    sys.exit("effectiveness: data file is locked; try again.")
                time.sleep(0.1)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def load():
    if not DATA.exists():
        return {"runs": []}
    with DATA.open(encoding="utf-8") as f:
        return json.load(f)


def save(db):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)
    os.replace(tmp, DATA)


def find(db, run_id):
    if run_id is None:
        open_runs = [r for r in db["runs"] if r["status"] == "open"]
        if len(open_runs) == 1:
            return open_runs[0]
        if not open_runs:
            sys.exit("No open run. Start one with: start \"<task>\"")
        sys.exit("Several open runs; pass --id. Open: " + ", ".join(r["id"] for r in open_runs))
    matches = [r for r in db["runs"] if r["id"].startswith(run_id)]
    if len(matches) != 1:
        sys.exit(f"No unique run matches id '{run_id}'.")
    return matches[0]


def close_phase(run, now):
    """Add time since the last phase switch to the current phase bucket."""
    if run.get("phase") and run.get("since"):
        run["minutes"][run["phase"]] += (now - run["since"]) / 60.0
    run["since"] = now


def fmt_min(m):
    return f"{m:6.1f}m"


# ---------- commands ----------

def cmd_start(a):
    db = load()
    now = time.time()
    run = {
        "id": uuid.uuid4().hex[:8],
        "task": a.task,
        "setup": a.setup,
        "status": "open",
        "created": datetime.now().isoformat(timespec="seconds"),
        "phase": "write",
        "since": now,
        "stops": 0,
        "minutes": {p: 0.0 for p in PHASES},
        "passed": None,
        "fixes": 0,
    }
    db["runs"].append(run)
    save(db)
    print(f"Started run {run['id']} [{a.setup}] '{a.task}' (phase: write)")


def cmd_phase(a):
    db = load()
    run = find(db, a.id)
    if run["status"] != "open":
        sys.exit(f"Run {run['id']} is already closed.")
    now = time.time()
    close_phase(run, now)
    if a.phase == "wait" and run["phase"] != "wait":
        run["stops"] += 1
    run["phase"] = a.phase
    save(db)
    print(f"Run {run['id']}: phase -> {a.phase}")


def cmd_fix(a):
    db = load()
    run = find(db, a.id)
    run["fixes"] += 1
    save(db)
    print(f"Run {run['id']}: fix round #{run['fixes']} recorded (task will count as not first-try).")


def cmd_done(a):
    db = load()
    run = find(db, a.id)
    if run["status"] != "open":
        sys.exit(f"Run {run['id']} is already closed.")
    close_phase(run, time.time())
    run["status"] = "done"
    run["phase"] = None
    run["passed"] = a.result == "pass" and run["fixes"] == 0
    save(db)
    m = run["minutes"]
    total = sum(m.values())
    print(f"Closed run {run['id']}: {'PASS' if run['passed'] else 'FAIL'} first try, "
          f"total {total:.1f}m (write {m['write']:.1f} / wait {m['wait']:.1f} / test {m['test']:.1f}), "
          f"stops {run['stops']}")


def cmd_add(a):
    db = load()
    run = {
        "id": uuid.uuid4().hex[:8],
        "task": a.task,
        "setup": a.setup,
        "status": "done",
        "created": datetime.now().isoformat(timespec="seconds"),
        "phase": None,
        "since": None,
        "stops": a.stops,
        "minutes": {"write": a.write, "wait": a.wait, "test": a.test},
        "passed": a.result == "pass",
        "fixes": 0 if a.result == "pass" else 1,
    }
    db["runs"].append(run)
    save(db)
    print(f"Added run {run['id']} [{a.setup}] '{a.task}'")


def cmd_list(a):
    db = load()
    runs = db["runs"][-a.limit:]
    if not runs:
        print("No runs yet.")
        return
    print(f"{'id':8}  {'setup':14} {'status':6} {'result':6} {'write':>7} {'wait':>7} {'test':>7} stops  task")
    for r in runs:
        m = r["minutes"]
        res = "-" if r["passed"] is None else ("pass" if r["passed"] else "fail")
        print(f"{r['id']:8}  {r['setup'][:14]:14} {r['status']:6} {res:6} "
              f"{fmt_min(m['write'])} {fmt_min(m['wait'])} {fmt_min(m['test'])} {r['stops']:5}  {r['task']}")


def cmd_delete(a):
    db = load()
    run = find(db, a.id)
    db["runs"].remove(run)
    save(db)
    print(f"Deleted run {run['id']}.")


def metrics(runs, scale=None):
    scale = scale or {}
    n = len(runs)
    avg = {p: sum(r["minutes"][p] for r in runs) / n * scale.get(p, 1.0) for p in PHASES}
    delta_min = sum(avg.values())
    ac = scale["ac"] if "ac" in scale else sum(1 for r in runs if r["passed"]) / n
    ac = min(ac, 1.0)
    e = ac / (delta_min / 60.0) if delta_min > 0 else 0.0
    stops = sum(r["stops"] for r in runs) / n
    return {"n": n, "avg": avg, "delta": delta_min, "ac": ac, "e": e, "stops": stops}


ADVICE = {
    "wait": "Raise autonomy: broader permissions, clearer CLAUDE.md/spec, fewer check-ins.",
    "test": "Automate validation: tests, CI, scripted checks the LLM runs itself.",
    "write": "Faster model/mode, or split work into smaller tasks.",
}


def print_metrics(name, m):
    d = m["delta"] or 1e-9
    print(f"\n== {name}  ({m['n']} runs)")
    print(f"  E  (working changes/hr) : {m['e']:.2f}")
    print(f"  Ac (first-try accuracy) : {m['ac']:.0%}")
    print(f"  Delta (avg per task)    : {m['delta']:.1f} min")
    for p in PHASES:
        print(f"     {p:5} {m['avg'][p]:6.1f} min  ({m['avg'][p] / d:4.0%})")
    print(f"  Stops per task          : {m['stops']:.1f}")
    worst = max(PHASES, key=lambda p: m["avg"][p])
    print(f"  Bottleneck              : {worst} -> {ADVICE[worst]}")
    if m["ac"] < SAFE_ACCURACY:
        print(f"  Accuracy below {SAFE_ACCURACY:.0%}: improve context/specs/self-testing "
              f"before raising autonomy (errors would ship unreviewed).")


def cmd_report(a):
    db = load()
    runs = [r for r in db["runs"] if r["status"] == "done"]
    if a.setup:
        runs = [r for r in runs if r["setup"] == a.setup]
    if a.last:
        runs = runs[-a.last:]
    if not runs:
        print("No completed runs match.")
        return
    if a.json:
        groups = {}
        for r in runs:
            groups.setdefault(r["setup"], []).append(r)
        out = {s: metrics(rs) for s, rs in groups.items()}
        out["_all"] = metrics(runs)
        print(json.dumps(out, indent=2))
        return
    setups = sorted({r["setup"] for r in runs})
    for s in setups:
        print_metrics(f"setup: {s}", metrics([r for r in runs if r["setup"] == s]))
    if len(setups) > 1:
        ranked = sorted(setups, key=lambda s: -metrics([r for r in runs if r["setup"] == s])["e"])
        print("\nRanking by E: " + " > ".join(ranked))
        print_metrics("all setups", metrics(runs))


def cmd_whatif(a):
    db = load()
    runs = [r for r in db["runs"] if r["status"] == "done"]
    if a.setup:
        runs = [r for r in runs if r["setup"] == a.setup]
    if not runs:
        print("No completed runs match.")
        return
    base = metrics(runs)
    scale = {"write": a.write, "wait": a.wait, "test": a.test}
    if a.ac is not None:
        scale["ac"] = a.ac
    new = metrics(runs, scale)
    print(f"Current E : {base['e']:.2f}/hr  (Delta {base['delta']:.1f} min, Ac {base['ac']:.0%})")
    print(f"What-if E : {new['e']:.2f}/hr  (Delta {new['delta']:.1f} min, Ac {new['ac']:.0%})")
    if base["e"]:
        print(f"Change    : {(new['e'] / base['e'] - 1):+.0%}")
    print("\nSingle-lever comparison (each part halved, or Ac -> 95%):")
    for label, sc in [("halve write", {"write": .5}), ("halve wait", {"wait": .5}),
                      ("halve test", {"test": .5}), ("Ac -> 95%", {"ac": .95})]:
        m = metrics(runs, sc)
        print(f"  {label:12} E = {m['e']:.2f}  ({(m['e'] / base['e'] - 1) if base['e'] else 0:+.0%})")


def main():
    p = argparse.ArgumentParser(description="Track LLM effectiveness: E = Ac / Delta")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="start timing a task (phase: write)")
    s.add_argument("task")
    s.add_argument("--setup", default="default", help="label for the model/config being measured")
    s.set_defaults(fn=cmd_start)

    s = sub.add_parser("phase", help="switch phase: write | wait | test")
    s.add_argument("phase", choices=PHASES)
    s.add_argument("--id")
    s.set_defaults(fn=cmd_phase)

    s = sub.add_parser("fix", help="record a fix-up round (task no longer counts as first-try)")
    s.add_argument("--id")
    s.set_defaults(fn=cmd_fix)

    s = sub.add_parser("done", help="close a run")
    s.add_argument("result", choices=("pass", "fail"))
    s.add_argument("--id")
    s.set_defaults(fn=cmd_done)

    s = sub.add_parser("add", help="log a finished run manually (minutes)")
    s.add_argument("task")
    s.add_argument("--write", type=float, required=True)
    s.add_argument("--wait", type=float, default=0.0)
    s.add_argument("--test", type=float, default=0.0)
    s.add_argument("--stops", type=int, default=0)
    s.add_argument("--result", choices=("pass", "fail"), required=True)
    s.add_argument("--setup", default="default")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("list", help="show recent runs")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("delete", help="remove a run")
    s.add_argument("--id", required=True)
    s.set_defaults(fn=cmd_delete)

    s = sub.add_parser("report", help="compute E, Ac, Delta and bottleneck per setup")
    s.add_argument("--setup")
    s.add_argument("--last", type=int, help="only the last N runs")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("whatif", help="project E if parts of Delta change")
    s.add_argument("--write", type=float, default=1.0, help="multiplier, e.g. 0.5")
    s.add_argument("--wait", type=float, default=1.0)
    s.add_argument("--test", type=float, default=1.0)
    s.add_argument("--ac", type=float, help="target accuracy 0-1")
    s.add_argument("--setup")
    s.set_defaults(fn=cmd_whatif)

    a = p.parse_args()
    with FileLock(DATA):
        a.fn(a)


if __name__ == "__main__":
    main()
