"""The public scan → plan → apply behavior seam."""

from __future__ import annotations

from pathlib import Path

from .discovery import scan_repository
from .model import Plan, Result, Scan
from .reporting import OperationReport, utc_now


class SkillManager:
    """Plan and apply repository Skill Manager commands."""

    def __init__(self, repository_root: Path) -> None:
        self._repository_root = repository_root

    def scan(self) -> Scan:
        """Read current candidate locations without validating or mutating them."""

        return scan_repository(self._repository_root)

    def plan(self, command: str, scan: Scan) -> Plan:
        """Prepare one typed command from a Scan."""

        if command != "list":
            raise ValueError(f"unsupported command: {command}")
        return Plan(command=command, scan=scan)

    def apply(self, plan: Plan) -> OperationReport:
        """Apply a non-mutating list Plan and return its common report."""

        started_at = utc_now()
        results = tuple(
            Result(
                source=candidate.source.value,
                skill=candidate.name,
                path=str(candidate.path),
                outcome="discovered",
            )
            for candidate in plan.scan.candidates
        )
        return OperationReport(
            command=plan.command,
            dry_run=False,
            status="failed"
            if any(diagnostic.severity == "error" for diagnostic in plan.scan.diagnostics)
            else "success",
            changed=False,
            transaction_id=None,
            started_at=started_at,
            finished_at=utc_now(),
            results=results,
            diagnostics=plan.scan.diagnostics,
        )
