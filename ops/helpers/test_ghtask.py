"""Tests for ghtask.py record handling, with a fake `gh` (no network).
Run: python ops/helpers/test_ghtask.py (or pytest).

taskrec.py runs for real (it is pure text), so state transitions and validation are the real rules.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ghtask  # noqa: E402

REPO, N = "o/r", 1
BOT, OWNER = "agent-bot", "Chaddacus"
CUTOFF = "2026-10-12T00:56:36Z"
BEFORE, AFTER = "2026-10-12T00:00:00Z", "2026-10-12T02:00:00Z"
RECORD = {"task": "r#1", "kind": "fix", "size": 3, "size_at_triage": 3, "origin": "owner",
          "done_when": ["works"]}


def rec_comment(cid, login, created, state, **extra):
    """An issue comment in the GitHub REST shape, holding a real rendered task record."""
    body = ghtask.helper("render-record", json.dumps({**RECORD, "state": state, **extra}))
    return {"id": cid, "user": {"login": login}, "created_at": created, "updated_at": AFTER, "body": body}


class FakeGH:
    """Answers the REST calls ghtask makes and records the writes."""

    def __init__(self, comments):
        self.comments, self.posted, self.patched, self.calls = comments, [], [], []

    def __call__(self, *args, input=None):
        self.calls.append(args)
        if args[:3] == ("api", "--paginate", "--slurp"):
            return json.dumps([self.comments])
        if args[:2] == ("api", "-X"):
            method, path = args[2], args[3]
            body = next((a[5:] for a in args if a.startswith("body=")), "")
            if method == "POST" and path.endswith("/comments"):
                self.posted.append((path, body))
            elif method == "PATCH":
                self.patched.append((path, body))
            return ""
        if args[0] == "api":  # GET issue: labels
            return json.dumps({"labels": []})
        return ""  # issue close and anything else


@contextlib.contextmanager
def world(comments, legacy=None, config="file"):
    """Fake gh as BOT, with a config file holding `legacy` (config='file'), none ('missing') or junk."""
    fake = FakeGH(comments)
    saved = (ghtask.gh, ghtask._ME, ghtask.CONFIG)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "config.json"
        if config == "file":
            path.write_text(json.dumps({"legacy_manager_logins": legacy or []}), encoding="utf-8")
        elif config == "junk":
            path.write_text("{not json", encoding="utf-8")
        ghtask.gh, ghtask._ME, ghtask.CONFIG = fake, BOT, path
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield fake
        finally:
            ghtask.gh, ghtask._ME, ghtask.CONFIG = saved


LEGACY = [{"login": OWNER, "until": CUTOFF}]


def test_legacy_record_is_read_and_rewritten_as_a_new_comment():
    with world([rec_comment(11, OWNER, BEFORE, "review")], LEGACY) as fake:
        old = ghtask.get_record(REPO, N)
        assert old["state"] == "review"  # read, not "new": review -> done is a legal move
        ghtask.set_record(REPO, N, dict(old, state="done", pr="#5"))
        assert fake.patched == []  # never edits the owner's comment
        assert len(fake.posted) == 1 and fake.posted[0][0] == f"repos/{REPO}/issues/{N}/comments"
        assert '"state": "done"' in fake.posted[0][1]
        assert any(c[:2] == ("issue", "close") for c in fake.calls)


def test_record_by_the_current_login_is_edited_in_place():
    with world([rec_comment(12, BOT, AFTER, "review")], LEGACY) as fake:
        old = ghtask.get_record(REPO, N)
        ghtask.set_record(REPO, N, dict(old, state="in-progress"))
        assert fake.posted == [] and [p for p, _ in fake.patched] == [f"repos/{REPO}/issues/comments/12"]


def test_newest_valid_record_wins_and_is_the_one_edited():
    comments = [rec_comment(11, OWNER, BEFORE, "review"), rec_comment(12, BOT, AFTER, "in-progress")]
    with world(comments, LEGACY) as fake:
        old = ghtask.get_record(REPO, N)
        assert old["state"] == "in-progress"
        ghtask.set_record(REPO, N, dict(old, state="review"))
        assert fake.posted == [] and [p for p, _ in fake.patched] == [f"repos/{REPO}/issues/comments/12"]


def test_owner_record_after_the_cutoff_is_ignored():
    with world([rec_comment(13, OWNER, AFTER, "review")], LEGACY):
        assert ghtask.get_record(REPO, N) == {}


def test_append_and_bump_on_a_legacy_record_post_a_new_comment():
    for run in (lambda: ghtask.bump(REPO, N, "fix_rounds"),
                lambda: ghtask.append(REPO, N, "attempts", {"model": "sonnet", "result": "finished"})):
        with world([rec_comment(11, OWNER, BEFORE, "in-progress")], LEGACY) as fake:
            run()
            assert fake.patched == []  # the API refuses edits of the owner's comment, so never try
            assert len(fake.posted) == 1 and '"state": "in-progress"' in fake.posted[0][1]


def test_works_without_a_config_file_or_with_a_broken_one():
    legacy_comment = [rec_comment(11, OWNER, BEFORE, "review")]
    for mode in ("missing", "junk"):
        with world(legacy_comment, config=mode):
            assert ghtask.get_record(REPO, N) == {}  # no legacy list: only the current login counts
    with world([rec_comment(12, BOT, AFTER, "review")], config="missing"):
        assert ghtask.get_record(REPO, N)["state"] == "review"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
