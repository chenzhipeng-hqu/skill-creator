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
    status_parser = subcommands.add_parser("status", help="report Skill lifecycle state")
    status_parser.add_argument("--json", action="store_true", help="emit a JSON operation report")
    diff_parser = subcommands.add_parser("diff", help="compare Development and Stable content")
    diff_parser.add_argument("skill", metavar="SKILL", help="canonical Skill Name")
    diff_parser.add_argument("--full", action="store_true", dest="full_diff", help="show all text hunks")
    diff_parser.add_argument("--json", action="store_true", help="emit a JSON operation report")
    validate_parser = subcommands.add_parser("validate", help="validate one Skill candidate")
    validate_group = validate_parser.add_mutually_exclusive_group(required=True)
    validate_group.add_argument("skill", nargs="?", metavar="SKILL", help="canonical Skill Name")
    validate_group.add_argument("--all", action="store_true", dest="validate_all")
    validate_parser.add_argument("--stable", action="store_true", dest="validate_stable")
    validate_parser.add_argument("--json", action="store_true", help="emit a JSON operation report")
    return parser


def write_human_report(report: OperationReport, output: TextIO) -> None:
    """Write the human-readable form of the common report envelope."""

    print(f"Command: {report.command}", file=output)
    print(f"Status: {report.status}", file=output)
    print(f"Changed: {'yes' if report.changed else 'no'}", file=output)
    print("Results:", file=output)
    if report.results:
        if report.command == "diff":
            _write_human_diff(report, output)
        else:
            for result in report.results:
                print(
                    f"  {result.source:<11} {result.skill:<32} "
                    f"{result.outcome:<18} {result.path}",
                    file=output,
                )
    else:
        print("  (none)", file=output)
    if not report.diagnostics:
        print("Diagnostics: none", file=output)
        return
    print("Diagnostics:", file=output)
    for diagnostic in report.diagnostics:
        print(f"  {diagnostic.severity} {diagnostic.code}: {diagnostic.message}", file=output)


def _write_human_diff(report: OperationReport, output: TextIO) -> None:
    for result in report.results:
        print(f"  {result.path}: {result.outcome}", file=output)
        if result.details.get("content_kind") == "binary":
            print(
                "    binary "
                f"stable={result.details.get('stable_size')}:{result.details.get('stable_sha256')} "
                f"development={result.details.get('development_size')}:{result.details.get('development_sha256')}",
                file=output,
            )
        if result.details.get("executable_changed") == "true":
            print("    executable bit changed", file=output)
        if result.outcome == "file-type-changed":
            print(
                "    file type: "
                f"stable={result.details.get('stable_type')} "
                f"development={result.details.get('development_type')}",
                file=output,
            )
        unified_diff = result.details.get("unified_diff")
        if unified_diff:
            print(unified_diff, end="" if unified_diff.endswith("\n") else "\n", file=output)
        if result.details.get("diff_truncated") == "true":
            print("    text hunk truncated; run diff --full for all text", file=output)


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
    report = manager.apply(
        manager.plan(
            arguments.command,
            manager.scan(),
            skill=getattr(arguments, "skill", None),
            full_diff=getattr(arguments, "full_diff", False),
            validate_stable=getattr(arguments, "validate_stable", False),
            validate_all=getattr(arguments, "validate_all", False),
        )
    )

    if arguments.json:
        print(json.dumps(report.to_json_object(), sort_keys=True), file=output)
        return 1 if report.status == "failed" else 0

    write_human_report(report, output)
    return 1 if report.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
