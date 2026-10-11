#!/usr/bin/env python3
"""GitHub adapter for the local runner (uses the gh CLI). The cloud runner does the same steps
with its GitHub tools; the rules live in taskrec.py and the progress module.

  ghtask.py get-record  <owner/repo> <n>            -> record JSON (or {} when none)
  ghtask.py set-record  <owner/repo> <n> < record   -> writes the record comment and the state
                                                       label; refuses an illegal transition
  ghtask.py post-update <owner/repo> <n> < update   -> validates and appends a worker update
  ghtask.py deps        <owner/repo> <n>            -> {"open": [...]} unfinished dependencies
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKREC = HERE / "taskrec.py"
STATE_LABELS = {"agent-ready", "agent-in-progress", "agent-review", "needs-owner", "blocked"}


def gh(*args, input=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", input=input)
    if r.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])}: {r.stderr.strip()}")
    return r.stdout


def helper(cmd, payload):
    r = subprocess.run([sys.executable, str(TASKREC), cmd], input=payload, capture_output=True,
                       text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
    if r.returncode != 0:
        sys.exit(r.stdout.strip() or r.stderr.strip())
    return r.stdout.strip()


def comments(repo, n):
    return json.loads(gh("api", f"repos/{repo}/issues/{n}/comments?per_page=100"))


def record_comment(repo, n):
    for c in comments(repo, n):
        if "<!-- ops-task-record -->" in c["body"]:
            return c
    return None


def get_record(repo, n):
    c = record_comment(repo, n)
    return json.loads(helper("parse-record", c["body"])) if c else {}


def set_record(repo, n, record):
    old = get_record(repo, n)
    if old and old.get("state") != record["state"]:
        helper("transition", json.dumps({"from": old["state"], "to": record["state"]}))
    body = helper("render-record", json.dumps(record))
    c = record_comment(repo, n)
    if c:
        gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{c['id']}", "-f", f"body={body}")
    else:
        gh("api", "-X", "POST", f"repos/{repo}/issues/{n}/comments", "-f", f"body={body}")
    want = helper("label", json.dumps({"state": record["state"]}))
    have = {l["name"] for l in json.loads(gh("api", f"repos/{repo}/issues/{n}"))["labels"]}
    for label in (have & STATE_LABELS) - {want}:
        gh("api", "-X", "DELETE", f"repos/{repo}/issues/{n}/labels/{label}")
    if want and want not in have:
        gh("api", "-X", "POST", f"repos/{repo}/issues/{n}/labels", "-f", f"labels[]={want}")
    if record["state"] == "done":
        gh("issue", "close", str(n), "-R", repo)
    print(json.dumps({"ok": True, "state": record["state"], "label": want}))


def post_update(repo, n, update):
    body = helper("render-update", json.dumps(update))
    gh("api", "-X", "POST", f"repos/{repo}/issues/{n}/comments", "-f", f"body={body}")
    print(json.dumps({"ok": True}))


def deps(repo, n):
    rec = get_record(repo, n)
    open_deps = []
    for d in rec.get("depends_on") or []:
        m = re.fullmatch(r"(?:([\w.-]+/[\w.-]+))?#(\d+)", d.strip())
        if not m:
            continue
        r2 = m.group(1) or repo
        state = json.loads(gh("api", f"repos/{r2}/issues/{m.group(2)}"))["state"]
        if state != "closed":
            open_deps.append(f"{r2}#{m.group(2)}")
    print(json.dumps({"open": open_deps}))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    cmd, repo, n = sys.argv[1], sys.argv[2], sys.argv[3]
    if cmd == "get-record":
        print(json.dumps(get_record(repo, n), indent=2))
    elif cmd == "set-record":
        set_record(repo, n, json.loads(sys.stdin.read()))
    elif cmd == "post-update":
        post_update(repo, n, json.loads(sys.stdin.read()))
    elif cmd == "deps":
        deps(repo, n)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
