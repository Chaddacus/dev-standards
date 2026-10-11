#!/usr/bin/env python3
"""Prove that a PR's new or changed tests catch the change.

Runs the changed test files twice:
  - on the BASE revision with only the test files taken from HEAD -> must FAIL
  - on HEAD                                                     -> must PASS
A test that passes on base does not exercise the change.

Usage:
  fail_then_pass.py --repo <path> [--base origin/main] [--head HEAD]
                    --cmd "<test command with {files}>" [--pattern REGEX]

Examples of --cmd: "python -m pytest -q {files}", "npx vitest run {files}",
"python -X utf8 {files}" (script-style tests, run once per file when {file} is used instead).
Prints a JSON verdict and exits 0 on PASS, 1 on FAIL, 2 when there is nothing to check.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_PATTERN = r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$"


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def run_tests(cwd, cmd, files):
    env = {**os.environ, "PYTHONUTF8": "1"}
    if "{file}" in cmd:
        results = []
        for f in files:
            r = subprocess.run(cmd.replace("{file}", f), shell=True, cwd=cwd, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=1800)
            results.append((r.returncode, r.stdout + r.stderr))
        code = max(c for c, _ in results)
        return code, "\n".join(o for _, o in results)
    r = subprocess.run(cmd.replace("{files}", " ".join(files)), shell=True, cwd=cwd, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    return r.returncode, r.stdout + r.stderr


def tail(text, n=15):
    return "\n".join(text.strip().splitlines()[-n:])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--base", default="origin/main")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--cmd", required=True)
    p.add_argument("--pattern", default=DEFAULT_PATTERN)
    a = p.parse_args()
    repo = Path(a.repo).resolve()
    merge_base = git(repo, "merge-base", a.base, a.head).strip()
    changed = [f for f in git(repo, "diff", "--name-only", "--diff-filter=AM", merge_base, a.head).splitlines()
               if re.search(a.pattern, f)]
    if not changed:
        print(json.dumps({"verdict": "NO_TESTS", "detail": "no added or modified test files in the diff"}))
        return 2

    head_code, head_out = run_tests(repo, a.cmd, changed)

    tmp = Path(tempfile.mkdtemp(prefix="ftp-"))
    wt = tmp / "base"
    try:
        git(repo, "worktree", "add", "--detach", str(wt), merge_base)
        for f in changed:
            dest = wt / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(subprocess.run(["git", "-C", str(repo), "show", f"{a.head}:{f}"],
                                            capture_output=True, check=True).stdout)
        for name in ("node_modules", ".venv"):
            src = repo / name
            if src.is_dir() and not (wt / name).exists():
                try:
                    os.symlink(src, wt / name, target_is_directory=True)
                except OSError:
                    pass
        base_code, base_out = run_tests(wt, a.cmd, changed)
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)

    ok = head_code == 0 and base_code != 0
    verdict = {
        "verdict": "PASS" if ok else "FAIL",
        "tests": changed,
        "head": {"exit": head_code, "expected": "0", "tail": tail(head_out)},
        "base_with_new_tests": {"exit": base_code, "expected": "non-zero", "tail": tail(base_out)},
    }
    if not ok:
        verdict["reason"] = ("tests fail on HEAD" if head_code != 0
                             else "tests also pass without the change: they do not exercise it")
    print(json.dumps(verdict, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
