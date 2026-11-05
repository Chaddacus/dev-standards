"""Tests for the dev-process generator and evidence check (run: python -m unittest test_dev_process)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import check_evidence
import generate_process

HERE = Path(__file__).resolve().parent

OVERLAY = {
    "project": "Demo",
    "agents_file": "AGENTS.md",
    "process_doc": "docs/process.md",
    "merged_spec": "docs/process.json",
    "check_command": "python tools/process/check_evidence.py",
    "start_pr": 10,
    "current_phase": "slice",
    "phases": [
        {"id": "proto", "title": "Prototype", "entry": "an idea", "exit": "fun found"},
        {"id": "slice", "title": "Vertical slice", "entry": "fun found", "exit": "one level shippable", "locks": "No new pillars."},
    ],
    "classes": {"content": {"gate_extra": "Screen walkthrough.", "sections_extra": ["Walkthrough"]}},
}


class Repo:
    """A temporary repository root with an overlay and an agent file holding the markers."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tools/process").mkdir(parents=True)
        (self.root / "tools/process/process.overlay.json").write_text(json.dumps(OVERLAY), encoding="utf-8")
        (self.root / "AGENTS.md").write_text(
            "# Rules\n\n<!-- dev-process:begin -->\nold\n<!-- dev-process:end -->\n\n## Tail\n", encoding="utf-8")

    def generate(self, *extra: str) -> int:
        overlay = self.root / "tools/process/process.overlay.json"
        return generate_process.main(["--root", str(self.root), "--overlay", str(overlay), *extra])

    def spec(self) -> dict:
        return json.loads((self.root / "docs/process.json").read_text(encoding="utf-8"))


class GeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.addCleanup(self.repo.tmp.cleanup)

    def test_check_fails_before_generation_and_passes_after(self) -> None:
        self.assertEqual(self.repo.generate("--check"), 1)
        self.assertEqual(self.repo.generate(), 0)
        self.assertEqual(self.repo.generate("--check"), 0)

    def test_agents_block_replaced_and_rest_kept(self) -> None:
        self.repo.generate()
        text = (self.repo.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## Tail", text)
        self.assertNotIn("\nold\n", text)
        self.assertIn("Phase: **Vertical slice**. No new pillars.", text)

    def test_overlay_extends_class_gate(self) -> None:
        self.repo.generate()
        content = self.repo.spec()["classes"]["content"]
        self.assertIn("Screen walkthrough.", content["gate"])
        self.assertIn("Walkthrough", content["sections"])

    def test_hand_edit_is_drift(self) -> None:
        self.repo.generate()
        doc = self.repo.root / "docs/process.md"
        doc.write_text(doc.read_text(encoding="utf-8") + "edit\n", encoding="utf-8")
        self.assertEqual(self.repo.generate("--check"), 1)

    def test_unknown_current_phase_is_refused(self) -> None:
        bad = dict(OVERLAY, current_phase="nope")
        (self.repo.root / "tools/process/process.overlay.json").write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.repo.generate()


class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.addCleanup(self.repo.tmp.cleanup)
        self.repo.generate()
        (self.repo.root / "tests").mkdir()
        (self.repo.root / "tests/test_x.py").write_text("", encoding="utf-8")
        self.spec = self.repo.spec()

    def errors(self, body: str, labels: list[str] | None = None) -> list[str]:
        return check_evidence.check(body, labels or [], self.spec, self.repo.root)

    def test_unfilled_template_fails(self) -> None:
        template = (self.repo.root / ".github/pull_request_template.md").read_text(encoding="utf-8")
        self.assertTrue(self.errors(template))

    def test_fix_with_existing_test_passes(self) -> None:
        body = "Class: fix\nRung: E0\n\n### Evidence\n\n- `tests/test_x.py` fails before, passes after\n"
        self.assertEqual(self.errors(body), [])

    def test_evidence_must_exist_or_link(self) -> None:
        body = "Class: fix\nRung: E0\n\n### Evidence\n\n- `tests/missing.py` it works\n"
        self.assertTrue(any("no existing file" in e for e in self.errors(body)))
        linked = "Class: fix\nRung: E0\n\n### Evidence\n\n- https://github.com/o/r/issues/1#c shows it\n"
        self.assertEqual(self.errors(linked), [])

    def test_rung_below_class_minimum_fails(self) -> None:
        body = "Class: tuning\nRung: E0\n\n### Evidence\n\n- #12 metrics\n\n### Reached / not shown\n\nE0 only\n"
        self.assertTrue(any("at least rung E1" in e for e in self.errors(body)))

    def test_experiment_needs_card_and_decision(self) -> None:
        body = ("Class: experiment\nRung: E2\n\n### Card\n\nsee notes\n\n### Evidence\n\n- #5 bots\n\n"
                "### Reached / not shown\n\nE2; not fun\n\n### Decision\n\nship it\n")
        errs = self.errors(body)
        self.assertTrue(any("Card must link" in e for e in errs))
        self.assertTrue(any("Decision must say" in e for e in errs))
        good = body.replace("see notes", "#41").replace("ship it", "keep")
        self.assertEqual(self.errors(good), [])

    def test_class_from_label(self) -> None:
        self.assertEqual(self.errors("Rung: none\n", ["class:infra"]), [])

    def test_unknown_class_fails(self) -> None:
        self.assertTrue(self.errors("Class: misc\nRung: none\n"))

    def test_old_pr_is_skipped(self) -> None:
        event = self.repo.root / "event.json"
        event.write_text(json.dumps({"pull_request": {"number": 3, "body": ""}}), encoding="utf-8")
        spec = self.repo.root / "docs/process.json"
        self.assertEqual(check_evidence.main(["--spec", str(spec), "--event", str(event)]), 0)
        event.write_text(json.dumps({"pull_request": {"number": 11, "body": ""}}), encoding="utf-8")
        self.assertEqual(check_evidence.main(["--spec", str(spec), "--event", str(event)]), 1)


if __name__ == "__main__":
    unittest.main()
