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


def test_v3_fields_and_frozen_counters():
    rec = {**RECORD, "size_at_triage": 3, "origin": "owner",
           "attempts": [{"model": "sonnet", "started_at": "2026-10-11T10:00:00Z", "finished_at": "2026-10-11T10:40:00Z"}],
           "owner_events": [{"type": "merge", "cause": "policy"}]}
    assert run("render-record", rec)[0] == 0
    assert run("render-record", {**rec, "origin": "aliens"})[0] == 1
    assert run("render-record", {**rec, "owner_events": [{"type": "merge"}]})[0] == 1
    assert run("check-update", {"old": rec, "new": {**rec, "size_at_triage": 5}})[0] == 1
    assert run("check-update", {"old": {**rec, "fix_rounds": 1}, "new": {**rec, "fix_rounds": 0}})[0] == 1
    assert run("check-update", {"old": rec, "new": {**rec, "attempts": []}})[0] == 1
    assert run("check-update", {"old": rec, "new": {**rec, "fix_rounds": 1}}) == (0, "ok")
    assert run("label", {"state": "planning"})[1] == "agent-planning"
    assert run("transition", {"from": "blocked", "to": "done"}) == (0, "ok")


def test_depends_on_and_children_need_non_empty_strings():
    # A numeric depends_on used to crash render-record with a TypeError traceback (issue #2).
    for field in ("depends_on", "children"):
        for bad in ([167], ["#12", 7], [""], [None], [["#1"]], [True], ["  "]):
            code, out = run("render-record", {**RECORD, field: bad})
            assert code == 1, (field, bad)
            assert f"record: {field} items must be non-empty strings" in out, (field, bad, out)
        ok = ["#12", "owner/repo#12"]
        code, md = run("render-record", {**RECORD, field: ok})
        assert code == 0 and json.loads(run("parse-record", md)[1])[field] == ok
    # The parse side reports the same message instead of a traceback.
    _, md = run("render-record", {**RECORD, "depends_on": ["#1"]})
    code, out = run("parse-record", md.replace('"#1"', "1"))
    assert code == 1 and "depends_on items must be non-empty strings" in out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
