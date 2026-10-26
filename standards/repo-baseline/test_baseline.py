"""Tests for baseline.py: GitHub-light checks (owner decision 2026-10-24).

Run: python -m unittest discover -s ~/.claude/skills/repo-baseline -p "test_*.py"
"""
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import baseline  # noqa: E402

PR_ONLY = """name: checks
on:
  pull_request:
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""


class WorkflowCost(unittest.TestCase):
    def test_pull_request_only_is_clean(self):
        self.assertEqual(baseline.workflow_cost(PR_ONLY), [])

    def test_push_schedule_matrix_and_lfs_are_flagged(self):
        text = """on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: '0 3 * * *'
jobs:
  t:
    strategy:
      matrix:
        os: [a, b]
    steps:
      - uses: actions/checkout@v4
        with:
          lfs: true
      - run: git lfs pull
"""
        got = baseline.workflow_cost(text)
        self.assertIn("is triggered by push", got)
        self.assertIn("is triggered by schedule", got)
        self.assertIn("uses a matrix", got)
        self.assertIn("downloads Git LFS files", got)

    def test_inline_trigger_list(self):
        self.assertEqual(baseline.workflow_cost("on: [push, pull_request]\njobs: {}\n"), ["is triggered by push"])

    def test_push_word_in_jobs_is_not_a_trigger(self):
        text = PR_ONLY + "      - run: git push origin HEAD\n"
        self.assertEqual(baseline.workflow_cost(text), [])

    def test_manual_only_workflow_costs_nothing(self):
        # A workflow_dispatch-only job runs only when someone starts it, so LFS there is not a gap.
        text = ("on:\n  workflow_dispatch:\njobs:\n  t:\n    steps:\n"
                "      - uses: actions/checkout@v7\n        with:\n          lfs: true\n")
        self.assertEqual(baseline.workflow_cost(text), [])

    def test_trigger_words_in_comments_are_ignored(self):
        text = "on:\n  # never on push, see the schedule note\n  pull_request:\njobs: {}\n"
        self.assertEqual(baseline.workflow_cost(text), [])

    def test_ci_files_come_from_the_fetched_default_branch(self):
        # A stale checkout must not report CI problems that main already fixed (2026-10-26).
        d = Path(tempfile.mkdtemp())
        git = lambda *a: subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t"); git("config", "user.name", "t")
        wf = d / ".github" / "workflows" / "ci.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text("on: [push, pull_request]\njobs: {}\n", encoding="utf-8")
        git("add", "-A"); git("commit", "-qm", "old")
        old = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        wf.write_text("on:\n  pull_request:\njobs: {}\n", encoding="utf-8")
        git("commit", "-qam", "new")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        git("checkout", "-q", old)
        self.assertEqual(baseline.local_facts(d)["ci_cost"], [])


class LocalFacts(unittest.TestCase):
    def make(self, files):
        d = Path(tempfile.mkdtemp())
        for rel, body in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(body, encoding="utf-8")
        return d

    def test_lfs_without_fetchexclude_is_a_gap(self):
        root = self.make({".gitattributes": "*.png filter=lfs diff=lfs merge=lfs -text\n"})
        self.assertFalse(baseline.local_facts(root)["lfs_fetchexclude"])
        (root / ".lfsconfig").write_text("[lfs]\n\tfetchexclude = ArtSource/Meshy/**\n", encoding="utf-8")
        self.assertTrue(baseline.local_facts(root)["lfs_fetchexclude"])

    def test_no_lfs_needs_no_lfsconfig(self):
        self.assertTrue(baseline.local_facts(self.make({"README.md": "x"}))["lfs_fetchexclude"])

    def test_reads_files_without_network(self):
        root = self.make({
            "AGENTS.md": "map", "dev.py": 'TASKS = {"fast": ["ruff check ."]}\n',
            ".claude/verification.json": json.dumps({"fast": [{"command": "python dev.py fast"}]}),
            ".github/workflows/checks.yml": PR_ONLY,
        })
        with mock.patch.object(baseline, "gh", side_effect=AssertionError("network")):
            f = baseline.local_facts(root)
        self.assertTrue(f["map"] and f["dev_py"] and f["fast_checks"] and f["static_checks"] and f["ci"])
        self.assertEqual(f["ci_cost"], [])


class HookCache(unittest.TestCase):
    def test_warm_cache_makes_no_github_call(self):
        tmp = Path(tempfile.mkdtemp())
        repo = tmp / "r"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/Chaddacus/x.git"],
                       check=True)
        cache = tmp / "cache.json"
        state = {"repo": "Chaddacus/x", "branch": "main", "empty": False, "label": True, "auto_merge": True,
                 "agent_push": True, "basic_protection": True, "full_protection": True}
        cache.write_text(json.dumps({"Chaddacus/x": {"at": time.time(), "v": baseline.CACHE_VERSION,
                                                     "state": state}}), encoding="utf-8")
        calls = []
        with mock.patch.object(baseline, "CACHE", cache), \
                mock.patch.object(baseline, "owners", return_value=["chaddacus"]), \
                mock.patch.object(baseline, "gh", side_effect=lambda *a, **k: calls.append(a) or (1, "", "")), \
                mock.patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(repo)}))), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            baseline.cmd_hook(None)
        self.assertEqual(calls, [])
        self.assertIn("no CI workflow", out.getvalue())  # file facts still come from the clone


class LocalRoot(unittest.TestCase):
    def test_main_clone_wins_over_worktree(self):
        base = Path(tempfile.mkdtemp())
        main = base / "b-main"
        subprocess.run(["git", "init", "-q", str(main)], check=True)
        subprocess.run(["git", "-C", str(main), "remote", "add", "origin", "https://github.com/O/r.git"], check=True)
        wt = base / "a-wt"  # sorts first
        wt.mkdir()
        (wt / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        with mock.patch.object(baseline, "CODE_DIRS", [base]), mock.patch.object(baseline, "repo_from_cwd", return_value="O/r"):
            self.assertEqual(baseline.local_root("O/r"), main)


if __name__ == "__main__":
    unittest.main()
