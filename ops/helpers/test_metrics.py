"""Tests for metrics.py (ops/METRICS.md v3). Run: python ops/helpers/test_metrics.py (or pytest).

Scenarios come from the six-reviewer critique: each test names the problem it guards against.
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M  # noqa: E402

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
MGR = OWNER = "Chaddacus"
CTX = {"manager_login": MGR, "owner_login": OWNER, "deploy_repos": ["o/svc"],
       "now": (T0 + timedelta(days=60)).isoformat()}
SINCE, UNTIL = T0.isoformat(), (T0 + timedelta(days=7)).isoformat()


def at(h):
    return (T0 + timedelta(hours=h)).isoformat()


ONE_ATTEMPT = [{"model": "sonnet", "started_at": "2026-10-01T00:30:00+00:00",
                "finished_at": "2026-10-01T01:30:00+00:00", "result": "finished"}]


def record(size=3, state="done", at_h=0.1, **extra):
    rec = {"task": "x", "kind": "fix", "size": size, "size_at_triage": size, "origin": "owner",
           "attempts": ONE_ATTEMPT, "state": state, "done_when": ["works"], **extra}
    rec = {k: v for k, v in rec.items() if v is not None}
    body = f"**Task record** `x`\n\n```json\n{json.dumps(rec)}\n```\n{M.RECORD_MARK}\n{M.WORKER_MARK}"
    return {"author": MGR, "created_at": at(at_h), "body": body}


def task(n, labels=(), closed=None, reason="COMPLETED", merged=None, repo="o/r", comments=None,
         extra_events=(), opened=0, **rec):
    events = []
    for lab, a, b in labels:
        events.append({"event": "labeled", "label": lab, "created_at": at(a)})
        if b is not None:
            events.append({"event": "unlabeled", "label": lab, "created_at": at(b)})
    events += list(extra_events)
    if closed is not None:
        events.append({"event": "closed", "label": None, "created_at": at(closed)})
    return {"repo": repo, "number": n, "created_at": at(opened),
            "closed_at": at(closed) if closed is not None else None,
            "state": "CLOSED" if closed is not None else "OPEN",
            "state_reason": reason if closed is not None else None,
            "comments": [record(**rec)] + (comments or []),
            "events": events,
            "prs": [{"number": 100 + n, "url": f"https://github.com/{repo}/pull/{100 + n}",
                     "merged_at": at(merged)}] if merged is not None else []}


def run(raws, regs=(), ctx=CTX, since=SINCE, until=UNTIL):
    return M.compute(list(raws), list(regs), ctx, since, until)


def one(raw, **kw):
    tasks, _ = run([raw], **kw)
    return tasks[0]


# ---------- time partition ----------

def test_clean_task_stages_are_exact():
    t = one(task(1, labels=[("agent-in-progress", 1, 3), ("agent-review", 3, 4)], merged=4, closed=4.2))
    assert t["outcome"] == "shipped" and t["ftr"] and t["points"] == 3
    assert t["stages"]["queue"] == 1 and t["stages"]["build"] == 2 and t["stages"]["review"] == 1
    assert abs(t["H"] - 4) < 1e-9 and abs(t["lead_h"] - 4) < 1e-9


def test_fail_round_trip_no_double_count_and_not_ftr():
    # Reviewers: v2 counted 57 h of stages for a 31 h task.
    t = one(task(2, labels=[("agent-in-progress", 1, 3), ("agent-review", 3, 4), ("agent-in-progress", 4, 6),
                            ("agent-review", 6, 7), ("needs-owner", 7, 31)], merged=31, closed=31))
    s = t["stages"]
    assert (s["queue"], s["build"], s["review"], s["owner"]) == (1, 4, 2, 24)
    assert abs(sum(s.values()) - 31) < 1e-9
    assert t["fix_rounds"] == 1 and not t["ftr"]


def test_planning_is_not_build():
    t = one(task(3, labels=[("agent-planning", 1, 2), ("agent-in-progress", 3, 5)], merged=5, closed=5))
    assert (t["stages"]["plan"], t["stages"]["build"], t["stages"]["queue"]) == (1, 2, 2)


def test_label_removed_after_close_is_clipped():
    t = one(task(4, labels=[("agent-in-progress", 1, 30)], merged=12, closed=12))
    assert t["stages"]["build"] == 11 and abs(t["H"] - 12) < 1e-9


def test_reopen_gap_excluded_and_not_ftr():
    ev = [{"event": "closed", "label": None, "created_at": at(5)},
          {"event": "reopened", "label": None, "created_at": at(15)}]
    t = one(task(5, labels=[("agent-in-progress", 1, 4)], extra_events=ev, merged=20, closed=20))
    assert t["reopened"] == 1 and not t["ftr"]
    assert abs(t["H"] - 10) < 1e-9  # 20 h lead minus the 10 h closed gap


def test_repeated_label_from_racing_runners():
    raw = task(6, labels=[("agent-in-progress", 1, None)], merged=5, closed=5,
               extra_events=[{"event": "labeled", "label": "agent-in-progress", "created_at": at(2)},
                             {"event": "unlabeled", "label": "agent-in-progress", "created_at": at(4)}])
    t = one(raw)
    assert t["stages"]["build"] == 3


# ---------- outcomes and denominators ----------

def test_not_planned_and_needs_owner_done_are_dropped():
    a = one(task(7, labels=[("agent-in-progress", 1, 2)], closed=3, reason="NOT_PLANNED"))
    b = one(task(8, closed=3))  # done, closed completed, but no merged PR
    assert a["outcome"] == b["outcome"] == "dropped" and a["points"] == b["points"] == 0


def test_twenty_task_cohort_matches_reviewer_example():
    # 14 clean shipped, 2 not planned, 4 stuck in needs-owner: v2 said Au = Ac = 1.00.
    raws = [task(10 + i, labels=[("agent-in-progress", 1, 2)], merged=2, closed=2) for i in range(14)]
    raws += [task(30 + i, closed=3, reason="NOT_PLANNED") for i in range(2)]
    raws += [task(40 + i, labels=[("needs-owner", 1, None)]) for i in range(4)]
    _, s = run(raws)
    assert s["Au"]["k"] == 14 and s["Au"]["n"] == 20 and s["Au"]["value"] == 0.7
    assert s["outcomes"] == {"shipped": 14, "dropped": 2, "open": 4}
    assert s["Ac"]["value"] == 1.0 and s["Ac"]["unit"] == "tasks"  # waste shows in E and outcomes
    clean_only, _ = run(raws[:14])
    assert s["E"] < M.summarise(clean_only, M.ts(SINCE), M.ts(UNTIL))["E"]


def test_parent_points_counted_once():
    raws = [task(50, size=8, merged=None, closed=None, labels=[("blocked", 1, None)])]
    for i, sz in enumerate((3, 3, 2)):
        raws.append(task(51 + i, size=sz, parent="r#50", labels=[("agent-in-progress", 1, 2)], merged=2, closed=2))
    _, s = run(raws)
    assert s["shipped_points"] == 8 and s["cohort"] == 3 and s["parents"] == 1


def test_regression_fix_scores_zero():
    t = one(task(60, origin="regression-fix", labels=[("agent-in-progress", 1, 2)], merged=2, closed=2))
    assert t["outcome"] == "shipped" and t["points"] == 0


def test_cohort_window_by_acceptance():
    inside = task(61, at_h=0.1, merged=2, closed=2)
    before = task(62, at_h=-0.02, opened=-1, merged=2, closed=2)  # accepted 1.2 min before since
    tasks, _ = run([inside, before])
    assert [t["task"] for t in tasks] == ["o/r#61"]


def test_empty_inputs_give_none_not_errors():
    _, s = run([])
    assert s["E"] is None and s["Au"]["value"] is None and s["S"] is None and s["cohort"] == 0


# ---------- records and trust ----------

def test_records_from_others_or_quoted_or_malformed_are_ignored():
    fake = record(size=8)
    fake["author"] = "stranger"
    quoted = record(size=8)
    quoted["body"] = "> " + quoted["body"]
    bad = {"author": MGR, "created_at": at(0.2), "body": "**Task record**\n```json\n{not json\n```\n" + M.RECORD_MARK}
    assert M.parse_record([fake, quoted, bad], MGR) is None
    real = record(size=2)
    assert M.parse_record([fake, real], MGR)["size"] == 2


def test_derived_fix_rounds_beat_recorded_zero():
    t = one(task(70, fix_rounds=0, labels=[("agent-review", 1, 2), ("agent-review", 3, 4)], merged=4, closed=4))
    assert t["fix_rounds"] == 1 and t["mismatch"] and not t["ftr"]


def test_unmarked_owner_comment_counts_against_autonomy():
    c = {"author": OWNER, "created_at": at(1), "body": "Use the other API."}
    t = one(task(71, comments=[c], merged=2, closed=2))
    assert t["autonomous"] is False and t["owner_min"] == 4
    marked = {"author": OWNER, "created_at": at(1), "body": "update\n" + M.WORKER_MARK}
    assert one(task(72, comments=[marked], merged=2, closed=2))["autonomous"] is True


def test_policy_escalation_does_not_count_against_autonomy():
    t = one(task(73, owner_events=[{"type": "merge", "cause": "policy", "at": at(3)}],
                 labels=[("needs-owner", 2, 3)], merged=3, closed=3))
    assert t["autonomous"] is True and t["owner_min"] == 1


# ---------- escapes ----------

def test_escape_links_are_strict():
    regs = [{"repo": "o/r", "body": "Caused by: o/r#12\nSee #1 and other/x#13 for context"},
            {"repo": "o/r", "body": "Caused by: https://github.com/o/r/pull/114"}]
    links = M.escape_links(regs)
    assert ("o/r", 12) in links and ("o/r", 114) in links
    assert ("o/r", 1) not in links and ("o/r", 13) not in links and ("other/x", 13) not in links
    assert M.parse_refs("#1.5 and v#3", "o/r") == set()


def test_escape_maturity():
    old = task(14, merged=2, closed=2)  # PR 114, shipped 60 days before now
    regs = [{"repo": "o/r", "body": "Caused by: https://github.com/o/r/pull/114"}]
    t = one(old, regs=regs)
    assert t["escaped"] and t["mature"]
    young_ctx = dict(CTX, now=at(24 * 5))
    assert not one(old, regs=regs, ctx=young_ctx)["mature"]


# ---------- deploy ----------

def test_deploy_repo_needs_live_and_counts_deploy_stage():
    pending = one(task(80, repo="o/svc", merged=4, closed=4.5, labels=[("agent-in-progress", 1, 4)]))
    assert pending["outcome"] == "pending_live"
    assert pending["h_stages"]["deploy"] == M.PENDING_CAP_H  # 60 days pending: capped
    live = one(task(81, repo="o/svc", merged=4, closed=4.5, live_at=at(6), labels=[("agent-in-progress", 1, 4)]))
    assert live["outcome"] == "shipped" and live["stages"]["deploy"] == 2 and abs(live["lead_h"] - 6) < 1e-9


# ---------- headline identities and properties ----------

def test_E_equals_Ac_over_hours_per_point():
    raws = [task(90, labels=[("agent-in-progress", 1, 3)], merged=3, closed=3),
            task(91, size=5, labels=[("agent-review", 1, 2), ("agent-review", 3, 4)], merged=4, closed=4),
            task(92, labels=[("needs-owner", 1, None)])]
    _, s = run(raws)
    assert abs(s["E"] - s["Ac_points"] / s["h_per_point"]) < 1e-3
    assert abs(sum(s["h_by_stage_per_point"].values()) - s["h_per_point"]) < 1e-2


def test_random_timelines_keep_invariants():
    rng = random.Random(7)
    labels = ["agent-in-progress", "agent-review", "needs-owner", "agent-planning", "blocked"]
    raws = []
    for n in range(200):
        evs, tcur = [], 0.2
        for _ in range(rng.randint(0, 8)):
            lab = rng.choice(labels)
            a = tcur + rng.random() * 5
            b = a + rng.random() * 5 if rng.random() < 0.8 else None
            evs.append((lab, a, b))
            tcur = a
        closed = tcur + rng.random() * 10 if rng.random() < 0.7 else None
        merged = closed if closed is not None and rng.random() < 0.8 else None
        raws.append(task(1000 + n, labels=evs, closed=closed, merged=merged,
                         reason=rng.choice(["COMPLETED", "COMPLETED", "NOT_PLANNED"])))
    tasks, s = run(raws)
    for t in tasks:
        assert all(v >= -1e-9 for v in t["stages"].values())
        assert t["H"] <= t["lead_h"] + 1e-6
        assert t["points"] == 0 or t["outcome"] == "shipped"
    for r in ("Ac", "Au"):
        if s[r]["value"] is not None:
            assert 0 <= s[r]["value"] <= 1 and s[r]["ci95"][0] <= s[r]["value"] <= s[r]["ci95"][1]
    # Adding a dropped task can never raise E or Au.
    more, _ = run(raws + [task(5000, closed=2, reason="NOT_PLANNED")])
    s2 = M.summarise(more, M.ts(SINCE), M.ts(UNTIL))
    assert s2["E"] <= s["E"] + 1e-12 and s2["Au"]["value"] <= s["Au"]["value"] + 1e-12


# ---------- statistics and routing ----------

def test_wilson_known_value_and_small_n_display():
    assert M.wilson(8, 10) == (0.49, 0.943)
    assert M.rate(7, 9)["show"] == "7 of 9" and M.rate(18, 20)["show"] == "90%"


def test_routing_rule_does_not_chase_noise():
    rng = random.Random(1)
    switches = 0
    window = []
    for week in range(20):  # 20 weeks, ~5 tasks a week, true accuracy 90%
        window = (window + [rng.random() < 0.9 for _ in range(5)])[-20:]
        d = M.routing_decision({"model": "sonnet", "k": sum(window), "n": len(window)}, None)
        switches += d == "switch"
    assert switches == 0


def test_routing_rule_switches_on_clear_evidence_only():
    weak = {"model": "sonnet", "k": 9, "n": 25}  # 36%
    assert M.routing_decision(weak, None) == "trial"
    assert M.routing_decision(weak, {"model": "opus", "k": 22, "n": 24}) == "switch"
    pricey = {"model": "opus", "k": 22, "n": 24, "cost_per_ftr_point": 9.0}
    assert M.routing_decision(dict(weak, cost_per_ftr_point=2.0), pricey) == "keep"
    assert M.routing_decision({"model": "sonnet", "k": 8, "n": 10}, None) == "keep"  # n too small


# ---------- round 2 findings ----------

def test_manager_comments_with_marker_and_matched_owner_events_do_not_count():
    plan = {"author": MGR, "created_at": at(1), "body": "Plan: ...\n" + M.WORKER_MARK}
    reply = {"author": OWNER, "created_at": at(13), "body": "LGTM, merge it"}
    t = one(task(100, comments=[plan, reply], merged=14, closed=14,
                 owner_events=[{"type": "merge", "cause": "policy", "at": at(14)}]))
    assert t["autonomous"] is True and t["owner_min"] == 1 and t["unknown_owner_events"] == 0


def test_owner_comment_after_end_and_owner_touches():
    late = {"author": OWNER, "created_at": at(200), "body": "thanks"}
    assert one(task(101, comments=[late], merged=2, closed=2))["autonomous"] is True
    t = one(task(102, merged=2, closed=2, owner_touches=3))
    assert t["autonomous"] is False and t["unknown_owner_events"] == 3


def test_latest_merge_ends_task_and_merge_splits_stages_in_deploy_repo():
    raw = task(103, labels=[("agent-in-progress", 1, 9)], merged=10, closed=10)
    raw["prs"].insert(0, {"number": 1, "url": "u", "merged_at": at(4)})
    t = one(raw)
    assert abs(t["lead_h"] - 10) < 1e-9 and t["stages"]["build"] == 8
    d = one(task(104, repo="o/svc", labels=[("agent-review", 1, 10)], merged=4, closed=10, live_at=at(12)))
    assert d["stages"]["review"] == 3 and d["stages"]["deploy"] == 8


def test_ac_interval_uses_tasks_not_points():
    raws = [task(110 + i, merged=2, closed=2) for i in range(8)]
    raws += [task(120 + i, merged=2, closed=2, fix_rounds=1) for i in range(2)]
    _, s = run(raws)
    assert s["Ac"]["k"] == 8 and s["Ac"]["n"] == 10 and s["Ac"]["ci95"] == M.wilson(8, 10)
    _, big = run([task(130, size=8, merged=2, closed=2), task(131, size=1, merged=2, closed=2, fix_rounds=1)])
    assert big["Ac"]["show"] == "1 of 2" and big["Ac_points"] == round(8 / 9, 3)


def test_missing_data_fails_closed():
    t = one(task(140, origin=None, merged=2, closed=2))
    assert t["points"] == 0 and t["mismatch"] and "origin" in t["missing_fields"]
    t = one(task(141, size_at_triage=None, merged=2, closed=2))
    assert t["points"] == 0 and "size_at_triage" in t["missing_fields"]
    assert one(task(142, attempts=[], merged=2, closed=2))["ftr"] is False
    assert one(task(143, attempts=ONE_ATTEMPT * 2, merged=2, closed=2))["ftr"] is False


def test_caused_by_forms_field_and_window():
    regs = [{"repo": "o/r", "created_at": at(10), "body": "**Caused by:** o/r#150"},
            {"repo": "o/r", "created_at": at(10), "body": "- Caused by https://github.com/o/r/pull/251"},
            {"repo": "o/r", "created_at": at(10), "body": "> caused by: #152"},
            {"repo": "o/r", "created_at": at(24 * 45), "body": "Caused by: o/r#153"}]
    links = M.escape_links(regs)
    assert {("o/r", 150), ("o/r", 251), ("o/r", 152), ("o/r", 153)} <= set(links)
    assert one(task(150, merged=2, closed=2), regs=regs)["escaped"]
    assert one(task(151, merged=2, closed=2), regs=regs)["escaped"]  # via its PR 251
    assert not one(task(153, merged=2, closed=2), regs=regs)["escaped"]  # filed 45 days later
    fix = task(160, origin="regression-fix", caused_by="o/r#161", merged=3, closed=3)
    tasks, _ = run([fix, task(161, merged=2, closed=2)])
    assert {t["task"]: t["escaped"] for t in tasks}["o/r#161"]


def test_parents_url_children_hours_and_blocked_not_in_H():
    raws = [task(170, size=8, children=["#171"], labels=[("agent-planning", 1, 3), ("blocked", 3, 10)],
                 closed=10)]
    raws.append(task(171, size=3, parent="https://github.com/o/r/issues/170",
                     labels=[("blocked", 1, 5), ("agent-in-progress", 5, 9)], merged=9, closed=9))
    tasks, s = run(raws)
    by = {t["task"]: t for t in tasks}
    p, c = by["o/r#170"], by["o/r#171"]
    assert p["parent"] and p["outcome"] == "parent_done" and p["points"] == 0
    assert p["h_stages"]["blocked"] == 0 and p["H"] == 3  # 1 h queue + 2 h planning
    assert c["H"] == 5  # 1 h queue + 4 h build; 4 h blocked excluded
    assert s["shipped_points"] == 3 and s["parents"] == 1


def test_no_change_and_intent_drops():
    nc = one(task(180, no_change=True, closed=3, labels=[("agent-in-progress", 1, 3)]))
    assert nc["outcome"] == "no_change" and nc["H"] == 0
    drop = one(task(181, closed=30, reason="NOT_PLANNED", labels=[("needs-owner", 1, 30)],
                    owner_events=[{"type": "decision", "cause": "intent", "at": at(30)}]))
    assert drop["autonomous"] is None
    struggle = one(task(182, fix_rounds=1, merged=9, closed=9,
                        owner_events=[{"type": "decision", "cause": "intent", "at": at(5)}]))
    assert struggle["autonomous"] is False


def test_au_bounds_for_open_tasks():
    rescue = [{"type": "answer", "cause": "rescue", "at": at(1)}]
    raws = [task(190 + i, merged=2, closed=2) for i in range(9)]
    raws.append(task(199, merged=2, closed=2, owner_events=rescue))
    raws += [task(200 + i, labels=[("needs-owner", 1, None)], owner_events=rescue) for i in range(3)]
    raws += [task(210 + i, labels=[("agent-in-progress", 1, None)]) for i in range(7)]
    _, s = run(raws)
    assert s["Au"]["k"] == 9 and s["Au"]["n"] == 13 and s["Au"]["undetermined"] == 7
    assert s["Au"]["bounds"] == (0.45, 0.8) and s["provisional"]


def test_bad_inputs_do_not_crash():
    t = one(task(220, merged=2, closed=2, owner_events=[{"type": "answer", "cause": "rescue", "est_min": None}]))
    assert t["owner_min"] == 4
    _, s = M.compute([task(221, merged=2, closed=2)], [], CTX, "2026-10-01", "2026-10-08")  # naive dates
    assert s["cohort"] == 1


def test_acceptance_uses_earliest_record():
    raw = task(230, merged=2, closed=2)
    raw["comments"].append(record(at_h=24 * 9))  # a second record comment after the window
    tasks, _ = run([raw])
    assert [t["task"] for t in tasks] == ["o/r#230"]


def test_manager_login_per_repo():
    # Local repos: records come from the agent account; a record by the owner's account is ignored.
    # Cloud repos still act as the owner, so there the owner's records count.
    ctx = dict(CTX, manager_login="agent-bot", manager_login_by_repo={"o/cloud": OWNER})
    local = task(250, merged=2, closed=2)  # record authored by OWNER ("Chaddacus")
    cloud = task(251, merged=2, closed=2, repo="o/cloud")
    tasks, _ = M.compute([local, cloud], [], ctx, SINCE, UNTIL)
    assert [t["task"] for t in tasks] == ["o/cloud#251"]
    bot = task(252, merged=2, closed=2)
    bot["comments"][0]["author"] = "agent-bot"
    tasks, _ = M.compute([bot], [], ctx, SINCE, UNTIL)
    assert [t["task"] for t in tasks] == ["o/r#252"] and tasks[0]["autonomous"] is True


def test_routing_table_counts_tasks():
    _, s = run([task(240 + i, merged=2, closed=2) for i in range(3)])
    assert s["routing_table"]["M"]["sonnet"] == {"model": "sonnet", "k": 3, "n": 3}


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok  ", name)
            except AssertionError as e:
                failed += 1
                print("FAIL", name, e)
    print(f"{failed} failed")
    sys.exit(1 if failed else 0)
