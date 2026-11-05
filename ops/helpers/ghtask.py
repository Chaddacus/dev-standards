#!/usr/bin/env python3
"""GitHub adapter for the local runner (uses the gh CLI). The cloud runner does the same steps
with its GitHub tools; the rules live in taskrec.py and the progress module.

  ghtask.py get-record  <owner/repo> <n>            -> record JSON (or {} when none)
  ghtask.py set-record  <owner/repo> <n> < record   -> validates transition and frozen fields,
                                                       writes the record comment and state label
  ghtask.py append      <owner/repo> <n> <field> < item   -> re-read, append to attempts or
                                                       owner_events, write (no lost updates)
  ghtask.py bump        <owner/repo> <n> <field>    -> re-read, +1 fix_rounds or owner_touches
  ghtask.py post-update <owner/repo> <n> < update   -> validates and appends a worker update
  ghtask.py comment     <owner/repo> <n> < text     -> manager comment with the worker marker
  ghtask.py live        <owner/repo> <n> <sha>      -> deploy step: record live_at
  ghtask.py deps        <owner/repo> <n>            -> {"open": [...]} unfinished dependencies

Only records written by the manager's login count; any other "record" is ignored. Exception: a
record by a login in `legacy_manager_logins` (~/.claude/ops/config.json, optional) counts when it was
created before that login's cutoff (metrics.is_manager_record). The newest valid record wins, and
set-record never edits a comment of another login: it posts a new record comment as the current login.
"""
import json
import os
from datetime import datetime, timezone
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKREC = HERE / "taskrec.py"
sys.path.insert(0, str(HERE))
import metrics  # noqa: E402

STATE_LABELS = {"agent-ready", "agent-in-progress", "agent-planning", "agent-review", "needs-owner", "blocked"}
CONFIG = Path.home() / ".claude" / "ops" / "config.json"
_ME = None


def gh(*args, input=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", input=input)
    if r.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])}: {r.stderr.strip()}")
    return r.stdout


def me():
    global _ME
    if _ME is None:
        _ME = json.loads(gh("api", "user"))["login"]
    return _ME


def helper(cmd, payload):
    r = subprocess.run([sys.executable, str(TASKREC), cmd], input=payload, capture_output=True,
                       text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
    if r.returncode != 0:
        sys.exit(r.stdout.strip() or r.stderr.strip())
    return r.stdout.strip()


def comments(repo, n):
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/issues/{n}/comments?per_page=100") or "[]")
    return [c for page in pages for c in page]


def legacy_logins():
    """`legacy_manager_logins` from the ops config; empty when the file or the key is missing."""
    try:
        legacy = json.loads(CONFIG.read_text(encoding="utf-8")).get("legacy_manager_logins")
    except (OSError, ValueError, AttributeError):
        return []
    return legacy if isinstance(legacy, list) else []


def record_comment(repo, n):
    found, legacy = None, legacy_logins()
    for c in comments(repo, n):
        body = c.get("body") or ""
        if (metrics.is_manager_record(c, me(), legacy) and metrics.RECORD_MARK in body
                and body.lstrip().startswith("**Task record**")):
            found = c
    return found


def get_record(repo, n):
    c = record_comment(repo, n)
    return json.loads(helper("parse-record", c["body"])) if c else {}


def write_record(repo, n, record, old):
    if not old and (record.get("size_at_triage") is None or not record.get("origin")):
        sys.exit("set-record: the first record needs size_at_triage and origin (ops/METRICS.md)")
    if old:
        helper("check-update", json.dumps({"old": old, "new": record}))
    helper("transition", json.dumps({"from": old.get("state", "new"), "to": record["state"]})) \
        if old.get("state", "new") != record["state"] else None
    body = helper("render-record", json.dumps(record))
    c = record_comment(repo, n)
    if c and (c.get("user") or {}).get("login") == me():
        gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{c['id']}", "-f", f"body={body}")
    else:
        gh("api", "-X", "POST", f"repos/{repo}/issues/{n}/comments", "-f", f"body={body}")
    want = helper("label", json.dumps({"state": record["state"]}))
    have = {l["name"] for l in json.loads(gh("api", f"repos/{repo}/issues/{n}"))["labels"]}
    if want and want not in have:  # add first, then remove: never a moment without a state label
        gh("api", "-X", "POST", f"repos/{repo}/issues/{n}/labels", "-f", f"labels[]={want}")
    for label in (have & STATE_LABELS) - {want}:
        gh("api", "-X", "DELETE", f"repos/{repo}/issues/{n}/labels/{label}")
    if record["state"] == "done":
        shipped = record.get("pr") or record.get("no_change") or record.get("children")
        gh("issue", "close", str(n), "-R", repo, "--reason", "completed" if shipped else "not planned")
    print(json.dumps({"ok": True, "state": record["state"], "label": want}))


def set_record(repo, n, record):
    write_record(repo, n, record, get_record(repo, n))


def append(repo, n, field, item):
    if field not in ("attempts", "owner_events"):
        sys.exit("append: field must be attempts or owner_events")
    old = get_record(repo, n)
    if not old:
        sys.exit("append: no record yet")
    if field == "owner_events" and not item.get("at"):
        item = dict(item, at=datetime.now(timezone.utc).isoformat())
    new = dict(old, **{field: list(old.get(field) or []) + [item]})
    write_record(repo, n, new, old)


def bump(repo, n, field):
    if field not in ("fix_rounds", "owner_touches"):
        sys.exit("bump: field must be fix_rounds or owner_touches")
    old = get_record(repo, n)
    if not old:
        sys.exit("bump: no record yet")
    write_record(repo, n, dict(old, **{field: (old.get(field) or 0) + 1}), old)


def live(repo, n, sha):
    """Deploy step: the change is live (health check passed)."""
    old = get_record(repo, n)
    if not old:
        sys.exit("live: no record")
    new = dict(old, live_at=datetime.now(timezone.utc).isoformat(), notes=f"live at {sha}")
    write_record(repo, n, new, old)


def comment(repo, n, text):
    """Every manager comment goes through here so it carries the worker marker; unmarked comments
    from the shared login count as owner actions (ops/METRICS.md)."""
    body = text.rstrip() + "\n\n" + metrics.WORKER_MARK
    gh("api", "-X", "POST", f"repos/{repo}/issues/{n}/comments", "-f", f"body={body}")
    print(json.dumps({"ok": True}))


def post_update(repo, n, update):
    body = helper("render-update", json.dumps(update))
    gh("api", "-X", "POST", f"repos/{repo}/issues/{n}/comments", "-f", f"body={body}")
    print(json.dumps({"ok": True}))


def deps(repo, n):
    rec = get_record(repo, n)
    open_deps = []
    for d in rec.get("depends_on") or []:
        ref = metrics.norm_id(d, repo)
        if ref and json.loads(gh("api", f"repos/{ref[0]}/issues/{ref[1]}"))["state"] != "closed":
            open_deps.append(f"{ref[0]}#{ref[1]}")
    print(json.dumps({"open": open_deps}))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    cmd, repo, n = sys.argv[1], sys.argv[2], sys.argv[3]
    if cmd == "get-record":
        print(json.dumps(get_record(repo, n), indent=2))
    elif cmd == "set-record":
        set_record(repo, n, json.loads(sys.stdin.read()))
    elif cmd == "append" and len(sys.argv) == 5:
        append(repo, n, sys.argv[4], json.loads(sys.stdin.read()))
    elif cmd == "bump" and len(sys.argv) == 5:
        bump(repo, n, sys.argv[4])
    elif cmd == "live" and len(sys.argv) == 5:
        live(repo, n, sys.argv[4])
    elif cmd == "comment":
        comment(repo, n, sys.stdin.read())
    elif cmd == "post-update":
        post_update(repo, n, json.loads(sys.stdin.read()))
    elif cmd == "deps":
        deps(repo, n)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
