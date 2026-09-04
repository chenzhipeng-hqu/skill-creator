#!/usr/bin/env python3
"""Repository-owned Skill Manager."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.skill_manager_core import SkillManager
from tools.skill_manager_core.reporting import OperationReport


def build_parser() -> argparse.ArgumentParser:
    """Build the initial non-interactive command parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    list_parser = subcommands.add_parser("list", help="discover Skill candidates")
    list_parser.add_argument("--json", action="store_true", help="emit a JSON operation report")
    return parser


def write_human_report(report: OperationReport, output: TextIO) -> None:
    """Write the human-readable form of the common report envelope."""

    print(f"Command: {report.command}", file=output)
    print(f"Status: {report.status}", file=output)
    print(f"Changed: {'yes' if report.changed else 'no'}", file=output)
    print("Results:", file=output)
    if report.results:
        for result in report.results:
            print(f"  {result.source:<11} {result.skill:<32} {result.path}", file=output)
    else:
        print("  (none)", file=output)
    if not report.diagnostics:
        print("Diagnostics: none", file=output)
        return
    print("Diagnostics:", file=output)
    for diagnostic in report.diagnostics:
        print(f"  {diagnostic.severity} {diagnostic.code}: {diagnostic.message}", file=output)


def main(
    argv: Sequence[str] | None = None,
    *,
    repository_root: Path | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run the initial list command through the typed core."""

    arguments = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    root = repository_root or Path(__file__).resolve().parents[1]
    manager = SkillManager(root)
    report = manager.apply(manager.plan(arguments.command, manager.scan()))

    if arguments.json:
        print(json.dumps(report.to_json_object(), sort_keys=True), file=output)
        return 1 if report.status == "failed" else 0

    write_human_report(report, output)
    return 1 if report.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
