#!/usr/bin/env python3
"""Task record, worker updates, assignments and the state machine. Pure text in, text out:
no network, no GitHub calls, so the same helper works for the local and the cloud runner.

The task record is one manager-owned issue comment containing a JSON block; worker updates are
separate append-only comments. Only the manager (progress module) writes the record and labels.

Commands (JSON on stdin, text on stdout; exit 1 with the reason on invalid input):
  render-record      task record JSON      -> comment markdown
  parse-record       comment markdown      -> task record JSON
  render-update      worker update JSON    -> comment markdown
  parse-update       comment markdown      -> worker update JSON
  render-assignment  {"record":..,"worktree":..,"merge":..,"repo":..} -> assignment text
  transition         {"from": state, "to": state} -> "ok" | reason (exit 1)
  check-update       {"old": record, "new": record} -> "ok" | reason (frozen size, counters only grow)
  label              {"state": state}      -> the GitHub label for that state ("" for done)
"""
import json
import re
import sys

RECORD_MARK = "<!-- ops-task-record -->"
UPDATE_MARK = "<!-- ops-update -->"
WORKER_MARK = "<!-- ops-worker -->"

STATES = ["new", "ready", "planning", "blocked", "in-progress", "review", "needs-owner", "done"]
LABELS = {"ready": "agent-ready", "planning": "agent-planning", "blocked": "blocked",
          "in-progress": "agent-in-progress", "review": "agent-review",
          "needs-owner": "needs-owner", "new": "", "done": ""}
TRANSITIONS = {
    "new": {"ready", "needs-owner", "planning"},
    "ready": {"planning", "blocked", "in-progress", "needs-owner"},
    "planning": {"ready", "blocked", "needs-owner"},
    "blocked": {"ready", "needs-owner", "done"},  # done: a parent whose sub-tasks all finished
    "in-progress": {"review", "ready", "blocked", "needs-owner"},
    "review": {"done", "in-progress", "needs-owner"},
    "needs-owner": {"ready", "new", "done"},
    "done": set(),
}
KINDS = {"fix", "ci", "docs", "config", "feature", "design", "debug", "chore"}
SIZES = {1, 2, 3, 5, 8}
UPDATE_STATUS = {"started", "milestone", "blocked", "finished", "stuck"}

RECORD_REQUIRED = {"task": str, "kind": str, "size": int, "state": str, "done_when": list}
RECORD_OPTIONAL = {"plan": str, "depends_on": list, "parent": str, "model": str, "effort": str,
                   "routing_reason": str, "fix_rounds": int, "attempts": list, "owner_touches": int,
                   "pr": str, "notes": str, "size_at_triage": int, "origin": str, "caused_by": str,
                   "no_change": bool, "merged_at": str, "live_at": str, "owner_events": list,
                   "children": list}
ORIGINS = {"owner", "system", "monitor", "regression-fix"}
OWNER_CAUSES = {"policy", "required", "access", "intent", "rescue", "unknown"}
ATTEMPT_KEYS = {"model", "effort", "started_at", "finished_at", "result", "cost_usd", "tokens"}
UPDATE_REQUIRED = {"task": str, "status": str, "completed": str, "next": str}
UPDATE_OPTIONAL = {"blocker": str, "links": list, "tests": str, "model": str}


def fail(msg):
    print(msg)
    sys.exit(1)


def check_fields(obj, required, optional, what):
    for k, t in required.items():
        if k not in obj:
            fail(f"{what}: missing '{k}'")
        if not isinstance(obj[k], t):
            fail(f"{what}: '{k}' must be {t.__name__}")
    for k, v in obj.items():
        if k in required:
            continue
        if k not in optional:
            fail(f"{what}: unknown field '{k}'")
        if v is not None and not isinstance(v, optional[k]):
            fail(f"{what}: '{k}' must be {optional[k].__name__}")


def validate_record(r):
    check_fields(r, RECORD_REQUIRED, RECORD_OPTIONAL, "record")
    if r["kind"] not in KINDS:
        fail(f"record: kind must be one of {sorted(KINDS)}")
    if r["size"] not in SIZES:
        fail(f"record: size must be one of {sorted(SIZES)}")
    if r["state"] not in STATES:
        fail(f"record: state must be one of {STATES}")
    if not 1 <= len(r["done_when"]) <= 8 or not all(isinstance(x, str) and x for x in r["done_when"]):
        fail("record: done_when needs 1-8 non-empty criteria")
    if r.get("model") and not r.get("routing_reason"):
        fail("record: a routed task needs routing_reason (why this model and effort)")
    if r.get("size_at_triage") is not None and r["size_at_triage"] not in SIZES:
        fail(f"record: size_at_triage must be one of {sorted(SIZES)}")
    if r.get("origin") and r["origin"] not in ORIGINS:
        fail(f"record: origin must be one of {sorted(ORIGINS)}")
    for o in r.get("owner_events") or []:
        if not isinstance(o, dict) or o.get("cause") not in OWNER_CAUSES or not o.get("type"):
            fail(f"record: owner_events need type and cause in {sorted(OWNER_CAUSES)}")
    for a in r.get("attempts") or []:
        if not isinstance(a, dict) or not a.get("model") or set(a) - ATTEMPT_KEYS:
            fail(f"record: attempts are objects with model and only {sorted(ATTEMPT_KEYS)}")
    return r


def check_update(old, new):
    """Frozen and monotonic fields: what a record rewrite may not do."""
    if old.get("size_at_triage") is not None and new.get("size_at_triage") != old["size_at_triage"]:
        fail("record: size_at_triage is frozen after triage")
    for k in ("fix_rounds", "owner_touches"):
        if (new.get(k) or 0) < (old.get(k) or 0):
            fail(f"record: {k} cannot decrease")
    for k in ("attempts", "owner_events"):
        if len(new.get(k) or []) < len(old.get(k) or []):
            fail(f"record: {k} cannot shrink")
    return "ok"


def validate_update(u):
    check_fields(u, UPDATE_REQUIRED, UPDATE_OPTIONAL, "update")
    if u["status"] not in UPDATE_STATUS:
        fail(f"update: status must be one of {sorted(UPDATE_STATUS)}")
    if u["status"] in ("blocked", "stuck") and not u.get("blocker"):
        fail("update: a blocked or stuck update needs 'blocker'")
    if u["status"] == "finished" and not (u.get("links") or u.get("tests")):
        fail("update: a finished update needs output links or test evidence")
    return u


def block(obj):
    return "```json\n" + json.dumps(obj, indent=2) + "\n```"


def parse_block(text, mark, what):
    if mark not in text:
        fail(f"{what}: marker {mark} not found")
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        fail(f"{what}: no JSON block")
    return json.loads(m.group(1))


def render_record(r):
    validate_record(r)
    crit = "\n".join(f"- [ ] {c}" for c in r["done_when"])
    lines = [f"**Task record** `{r['task']}` · {r['kind']} · size {r['size']} · state **{r['state']}**"]
    if r.get("model"):
        lines.append(f"Routing: {r['model']} / effort {r.get('effort', 'default')} — {r['routing_reason']}")
    if r.get("depends_on"):
        lines.append("Depends on: " + ", ".join(r["depends_on"]))
    if r.get("plan"):
        lines.append(f"Plan: {r['plan']}")
    lines += ["", "**Done when**", crit, "", "<details><summary>record</summary>", "", block(r), "",
              "</details>", "", RECORD_MARK, WORKER_MARK]
    return "\n".join(lines)


def render_update(u):
    validate_update(u)
    head = f"**Update** `{u['task']}` · **{u['status']}**"
    body = [head, f"- Completed: {u['completed']}", f"- Next: {u['next']}"]
    if u.get("blocker"):
        body.append(f"- Blocker: {u['blocker']}")
    if u.get("tests"):
        body.append(f"- Tests: {u['tests']}")
    for link in u.get("links") or []:
        body.append(f"- Output: {link}")
    body += ["", "<details><summary>update</summary>", "", block(u), "", "</details>", "", UPDATE_MARK, WORKER_MARK]
    return "\n".join(body)


def render_assignment(a):
    r = validate_record(a["record"])
    crit = "\n".join(f"  - {c}" for c in r["done_when"])
    return f"""Assignment {r['task']} ({r['kind']}, size {r['size']})
Repository: {a['repo']}   Worktree: {a['worktree']}   Merge rule: {a.get('merge', 'auto')}
Model/effort: {r.get('model', '?')} / {r.get('effort', 'default')} — {r.get('routing_reason', '')}
Plan: {r.get('plan') or 'none (bounded task)'}
Done when:
{crit}
Rules: follow ~/.claude/CLAUDE.md (or dev-standards/standards) accuracy rules: test first, ground APIs,
run the real thing, repository checks. Stay inside this assignment; scope changes go back to the
manager as a 'blocked' update.
Updates: post with `taskrec.py render-update` as issue comments at start, at meaningful milestones,
on any blocker, and at the end. 'finished' moves the task to review; do not merge.
End your reply with the final update JSON."""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        fail(__doc__)
    cmd = sys.argv[1]
    data = sys.stdin.read()
    if cmd == "render-record":
        print(render_record(json.loads(data)))
    elif cmd == "parse-record":
        print(json.dumps(validate_record(parse_block(data, RECORD_MARK, "record")), indent=2))
    elif cmd == "render-update":
        print(render_update(json.loads(data)))
    elif cmd == "parse-update":
        print(json.dumps(validate_update(parse_block(data, UPDATE_MARK, "update")), indent=2))
    elif cmd == "render-assignment":
        print(render_assignment(json.loads(data)))
    elif cmd == "check-update":
        d = json.loads(data)
        print(check_update(d["old"], d["new"]))
    elif cmd == "transition":
        t = json.loads(data)
        if t["to"] not in TRANSITIONS.get(t["from"], set()):
            fail(f"transition {t['from']} -> {t['to']} not allowed; allowed: {sorted(TRANSITIONS.get(t['from'], []))}")
        print("ok")
    elif cmd == "label":
        print(LABELS[json.loads(data)["state"]])
    else:
        fail(__doc__)


if __name__ == "__main__":
    main()
