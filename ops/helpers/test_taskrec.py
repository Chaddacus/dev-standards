"""Tests for taskrec.py (run: python -m pytest ops/helpers -q, or python ops/helpers/test_taskrec.py)."""
import json
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).with_name("taskrec.py")

RECORD = {"task": "crosstalk-lab#4", "kind": "feature", "size": 3, "state": "ready",
          "done_when": ["PR previews publish", "preview removed on close"],
          "model": "sonnet", "effort": "medium", "routing_reason": "bounded change against a known pattern"}


def run(cmd, payload):
    data = payload if isinstance(payload, str) else json.dumps(payload)
    r = subprocess.run([sys.executable, str(HELPER), cmd], input=data, capture_output=True, text=True,
                       encoding="utf-8")
    return r.returncode, r.stdout.strip()


def test_record_round_trip():
    code, md = run("render-record", RECORD)
    assert code == 0 and "<!-- ops-task-record -->" in md
    code, back = run("parse-record", md)
    assert code == 0 and json.loads(back) == RECORD


def test_record_rejects_bad_size_and_missing_reason():
    assert run("render-record", {**RECORD, "size": 4})[0] == 1
    bad = dict(RECORD)
    del bad["routing_reason"]
    assert "routing_reason" in run("render-record", bad)[1]


def test_update_rules():
    ok = {"task": "x#1", "status": "finished", "completed": "done", "next": "review", "tests": "12 passed"}
    code, md = run("render-update", ok)
    assert code == 0 and json.loads(run("parse-update", md)[1]) == ok
    assert run("render-update", {**ok, "status": "stuck"})[0] == 1  # needs blocker
    no_evidence = {k: v for k, v in ok.items() if k != "tests"}
    assert run("render-update", no_evidence)[0] == 1


def test_state_machine():
    assert run("transition", {"from": "in-progress", "to": "review"}) == (0, "ok")
    assert run("transition", {"from": "in-progress", "to": "done"})[0] == 1  # review is mandatory
    assert run("transition", {"from": "review", "to": "done"}) == (0, "ok")
    assert run("label", {"state": "review"})[1] == "agent-review"


def test_assignment_mentions_update_protocol():
    code, text = run("render-assignment", {"record": RECORD, "repo": "Chaddacus/crosstalk-lab",
                                           "worktree": "/tmp/wt", "merge": "auto"})
    assert code == 0 and "do not merge" in text and "PR previews publish" in text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
