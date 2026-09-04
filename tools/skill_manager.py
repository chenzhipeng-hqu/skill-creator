#!/usr/bin/env python3
"""Repository-owned Skill Manager."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Sequence, TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.skill_manager_core import SkillManager
from tools.skill_manager_core.reporting import OperationReport
from tools.skill_manager_core.tui import run_tui


def build_parser() -> argparse.ArgumentParser:
    """Build the initial non-interactive command parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command")
    tui_parser = subcommands.add_parser("tui", help="manage Agent installations interactively")
    tui_parser.add_argument("--dry-run", action="store_true")
    list_parser = subcommands.add_parser("list", help="discover Skill candidates")
    list_parser.add_argument("--json", action="store_true", help="emit a JSON operation report")
    status_parser = subcommands.add_parser("status", help="report Skill lifecycle state")
    status_parser.add_argument("skill", nargs="*", metavar="SKILL")
    status_parser.add_argument(
        "--agent",
        action="append",
        choices=("codex", "claude", "agents", "hermes", "opencode"),
    )
    status_parser.add_argument("--json", action="store_true", help="emit a JSON operation report")
    diff_parser = subcommands.add_parser("diff", help="compare Development and Stable content")
    diff_parser.add_argument("skill", nargs="+", metavar="SKILL", help="canonical Skill Name")
    diff_parser.add_argument("--full", action="store_true", dest="full_diff", help="show all text hunks")
    diff_parser.add_argument("--json", action="store_true", help="emit a JSON operation report")
    validate_parser = subcommands.add_parser("validate", help="validate one Skill candidate")
    validate_group = validate_parser.add_mutually_exclusive_group(required=True)
    validate_group.add_argument("skill", nargs="*", metavar="SKILL", help="canonical Skill Name")
    validate_group.add_argument("--all", action="store_true", dest="validate_all")
    validate_parser.add_argument("--stable", action="store_true", dest="validate_stable")
    validate_parser.add_argument("--json", action="store_true", help="emit a JSON operation report")
    promote_parser = subcommands.add_parser("promote", help="promote Development Skills")
    promote_parser.add_argument("skill", nargs="*", metavar="SKILL")
    promote_parser.add_argument("--all", action="store_true", dest="promote_all")
    promote_parser.add_argument("--replace-stable-drift", action="store_true")
    promote_parser.add_argument("--record-release", action="store_true")
    promote_parser.add_argument(
        "--commit-message-file",
        action="append",
        default=[],
        metavar="SKILL=PATH",
    )
    promote_parser.add_argument("--dry-run", action="store_true")
    promote_parser.add_argument("--json", action="store_true")
    recover_parser = subcommands.add_parser("recover", help="recover an unfinished promotion")
    recover_parser.add_argument("--dry-run", action="store_true")
    recover_parser.add_argument("--json", action="store_true")
    for command, help_text in (("install", "install Stable Skills"), ("uninstall", "uninstall managed Stable Skill links")):
        operation_parser = subcommands.add_parser(command, help=help_text)
        operation_parser.add_argument("skill", nargs="*", metavar="SKILL")
        operation_parser.add_argument("--all", action="store_true", dest="install_all")
        operation_parser.add_argument("--agent", action="append", required=True, choices=("codex", "claude", "agents", "hermes", "opencode"))
        operation_parser.add_argument("--dry-run", action="store_true")
        operation_parser.add_argument("--json", action="store_true")
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
    home: Path | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run the initial list command through the typed core."""

    parser = build_parser()
    arguments = parser.parse_args(argv)
    output = stdout or sys.stdout
    root = repository_root or Path(__file__).resolve().parents[1]
    manager = SkillManager(root, home=home)
    if arguments.command in {None, "tui"}:
        with _termination_as_operation_error():
            return run_tui(
                manager,
                stdin or sys.stdin,
                output,
                dry_run=getattr(arguments, "dry_run", False),
            )
    if arguments.command in {"install", "uninstall"} and (
        bool(arguments.skill) == bool(arguments.install_all)
    ):
        parser.error("select SKILL... or --all, but not both")
    if arguments.command == "promote" and (
        bool(arguments.skill) == bool(arguments.promote_all)
    ):
        parser.error("select SKILL... or --all, but not both")
    if arguments.command == "validate" and (
        bool(arguments.skill) == bool(arguments.validate_all)
    ):
        parser.error("select SKILL... or --all, but not both")
    if arguments.command == "promote" and bool(arguments.commit_message_file) != bool(arguments.record_release):
        parser.error("--commit-message-file requires --record-release and vice versa")
    raw_skill = getattr(arguments, "skill", None)
    scalar_skill = raw_skill if isinstance(raw_skill, str) else None
    selected_skills = tuple(raw_skill) if isinstance(raw_skill, list) else ()
    with _termination_as_operation_error():
        report = manager.apply(
            manager.plan(
                arguments.command,
                manager.scan(),
                skill=scalar_skill,
                full_diff=getattr(arguments, "full_diff", False),
                validate_stable=getattr(arguments, "validate_stable", False),
                validate_all=getattr(arguments, "validate_all", False),
                dry_run=getattr(arguments, "dry_run", False),
                skills=selected_skills,
                agents=tuple(getattr(arguments, "agent", ()) or ()),
                select_all=(
                    getattr(arguments, "install_all", False)
                    or getattr(arguments, "promote_all", False)
                ),
                replace_stable_drift=getattr(arguments, "replace_stable_drift", False),
                record_release=getattr(arguments, "record_release", False),
                commit_message_files=tuple(getattr(arguments, "commit_message_file", ())),
            )
        )

    if arguments.json:
        print(json.dumps(report.to_json_object(), sort_keys=True), file=output)
        return 0 if report.status in {"success", "no-change"} else 1

    write_human_report(report, output)
    return 0 if report.status in {"success", "no-change"} else 1


@contextmanager
def _termination_as_operation_error():
    """Turn interactive termination into a caught transaction failure."""

    previous: dict[int, object] = {}

    def interrupt(signum: int, frame: object) -> None:
        raise InterruptedError(f"received signal {signum}")

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
