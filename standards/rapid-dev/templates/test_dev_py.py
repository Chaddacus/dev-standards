"""Smoke test for dev.py (rapid-dev standard): every message-only task runs on this OS with the
expected exit code, and --list works. Catches shell-specific breakage in CI.
Run: python tests/test_dev_py.py (or pytest)."""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("devpy", ROOT / "dev.py")
devpy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(devpy)


def run(*args):
    return subprocess.run([sys.executable, str(ROOT / "dev.py"), *args], check=False, capture_output=True,
                          text=True,
                          encoding="utf-8", errors="replace", cwd=ROOT)


class DevPy(unittest.TestCase):
    def test_list(self):
        r = run("--list")
        self.assertEqual(r.returncode, 0)
        for task in devpy.TASKS:
            self.assertIn(task, r.stdout)

    def test_message_tasks(self):
        for name, cmds in devpy.TASKS.items():
            if cmds and all(c.startswith(("say:", "fail:")) for c in cmds):
                want = 1 if any(c.startswith("fail:") for c in cmds) else 0
                r = run(name)
                self.assertEqual(r.returncode, want, f"{name}: {r.stdout}{r.stderr}")

    def test_banners_go_to_stderr(self):
        # stdout carries only the commands' own output, so `$(python dev.py <task>)` captures cleanly.
        for name, cmds in devpy.TASKS.items():
            if cmds and all(c.startswith(("say:", "fail:")) for c in cmds):
                r = run(name)
                self.assertEqual(r.stdout, "", f"{name}: banner leaked to stdout")
                self.assertIn(f"[{name}]", r.stderr)

    def test_no_shell_echo_messages(self):
        for name, cmds in devpy.TASKS.items():
            for c in cmds:
                self.assertFalse(c.lstrip().startswith("echo "), f"{name}: use say:/fail:, not echo")

    def test_unknown_task_fails(self):
        self.assertNotEqual(run("no-such-task").returncode, 0)


if __name__ == "__main__":
    unittest.main()
