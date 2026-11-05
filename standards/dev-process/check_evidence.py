"""Check that a pull request body meets its work class's evidence gate.

Reads the merged process spec written by generate_process.py and a pull request body (from the
GitHub Actions event file, or --body-file for local runs). Fails when the class is missing or
unknown, the rung is below the class's lowest rung, a required section is empty, an evidence line
names no existing file and no link, or an experiment has no card or decision. It checks that
evidence is present and points at something real; it does not judge whether the evidence is good.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RUNGS = ["none", "E0", "E1", "E2", "E3", "E4"]
DECISIONS = ("keep", "revise", "remove", "inconclusive", "investigate")
COMMENT = re.compile(r"<!--.*?-->", re.S)
PLACEHOLDER = re.compile(r"^-?\s*`?path/to/[^`]*`?\s*(—.*)?$")


def field(body: str, name: str) -> str:
    """The value of a 'Name: value' line at the start of a line, or ''."""
    m = re.search(rf"^\s*{name}\s*:\s*(.+?)\s*$", body, re.M | re.I)
    value = m.group(1).strip() if m else ""
    # The template's "<a | b | c>" choice list means the field was never filled in.
    return "" if "|" in value or value.startswith("<") else value


def sections(body: str) -> dict[str, str]:
    """Map each '### Heading' to its text, comments and placeholder lines removed."""
    out: dict[str, str] = {}
    parts = re.split(r"^###\s+(.+?)\s*$", COMMENT.sub("", body), flags=re.M)
    for name, text in zip(parts[1::2], parts[2::2]):
        lines = [ln for ln in text.strip().splitlines() if ln.strip() and not PLACEHOLDER.match(ln.strip())]
        out[name.strip().lower()] = "\n".join(lines)
    return out


def evidence_ok(line: str, root: Path) -> bool:
    """An evidence line is real if it links somewhere or names a file that exists in the checkout."""
    if re.search(r"https?://\S+", line) or re.search(r"(^|\s)#\d+\b", line):
        return True
    for candidate in re.findall(r"`([^`]+)`", line):
        path = candidate.split("::")[0].split(":")[0].strip()
        if path and (root / path).exists():
            return True
    return False


def check(body: str, labels: list[str], spec: dict, root: Path) -> list[str]:
    """Return every gate failure for this body; an empty list passes."""
    errors: list[str] = []
    classes = spec["classes"]
    key = field(body, "Class").split()[0].lower() if field(body, "Class") else ""
    if not key:
        key = next((lb.split(":", 1)[1] for lb in labels if lb.startswith("class:")), "")
    if key not in classes:
        return [f"Class must be one of {', '.join(classes)} (got {key or 'nothing'}). See {spec['overlay']['process_doc']}."]
    gate = classes[key]
    rung = field(body, "Rung").split()[0] if field(body, "Rung") else ""
    rung = rung.upper() if rung.lower() != "none" else "none"
    if rung not in RUNGS:
        errors.append(f"Rung must be one of {', '.join(RUNGS)} (got {rung or 'nothing'}).")
    elif gate["min_rung"] and RUNGS.index(rung) < RUNGS.index(gate["min_rung"]):
        errors.append(f"Class {key} needs at least rung {gate['min_rung']} (got {rung}).")
    found = sections(body)
    for name in gate["sections"]:
        if not found.get(name.lower()):
            errors.append(f"Section '### {name}' is required for class {key} and is empty.")
    if "Evidence" in gate["sections"] and found.get("evidence"):
        items = [ln for ln in found["evidence"].splitlines() if ln.lstrip().startswith(("-", "*"))]
        bad = [ln.strip() for ln in items if not evidence_ok(ln, root)]
        if not items:
            errors.append("Evidence needs at least one '- ' line naming a file or link.")
        for ln in bad:
            errors.append(f"Evidence line names no existing file and no link: {ln}")
    if "Card" in gate["sections"] and found.get("card") and not re.search(r"#\d+|https?://", found["card"]):
        errors.append("Card must link the experiment issue (#123 or a URL).")
    if "Decision" in gate["sections"] and found.get("decision"):
        if not any(d in found["decision"].lower() for d in DECISIONS):
            errors.append(f"Decision must say one of: {', '.join(DECISIONS)}.")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True, help="merged spec from generate_process.py")
    parser.add_argument("--event", type=Path, help="GitHub Actions event JSON ($GITHUB_EVENT_PATH)")
    parser.add_argument("--body-file", type=Path, help="a pull request body, for local runs")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    labels: list[str] = []
    if args.event:
        pr = json.loads(args.event.read_text(encoding="utf-8")).get("pull_request") or {}
        if not pr:
            print("PROCESS_EVIDENCE_SKIP: not a pull request event")
            return 0
        start = spec["overlay"].get("start_pr", 0)
        if pr.get("number", 0) < start:
            print(f"PROCESS_EVIDENCE_SKIP: PR #{pr.get('number')} predates the process (start #{start})")
            return 0
        body = pr.get("body") or ""
        labels = [lb.get("name", "") for lb in pr.get("labels", [])]
    elif args.body_file:
        body = args.body_file.read_text(encoding="utf-8")
    else:
        parser.error("give --event or --body-file")
    errors = check(body, labels, spec, args.root.resolve())
    for e in errors:
        print(f"PROCESS_EVIDENCE_FAIL: {e}")
    if not errors:
        print("PROCESS_EVIDENCE_OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
