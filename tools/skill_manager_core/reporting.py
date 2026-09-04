"""The versioned operation-report envelope."""

from __future__ import annotations

import platform
import socket
from dataclasses import dataclass
from datetime import datetime, timezone

from .model import Diagnostic, Result


def utc_now() -> str:
    """Return an RFC 3339 UTC timestamp accepted by the report contract."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class OperationReport:
    """One human-readable or machine-readable manager command result."""

    command: str
    dry_run: bool
    status: str
    changed: bool
    transaction_id: str | None
    started_at: str
    finished_at: str
    results: tuple[Result, ...]
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_json_object(self) -> dict[str, object]:
        """Serialize the common schema-versioned report envelope."""

        return {
            "schema_version": 1,
            "command": self.command,
            "dry_run": self.dry_run,
            "status": self.status,
            "changed": self.changed,
            "transaction_id": self.transaction_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "host": {
                "hostname": socket.gethostname(),
                "platform": platform.system().lower(),
            },
            "results": [
                {
                    "source": result.source,
                    "skill": result.skill,
                    "path": result.path,
                    "outcome": result.outcome,
                    "details": result.details,
                    **({"target": result.target} if result.target is not None else {}),
                }
                for result in self.results
            ],
            "diagnostics": [
                {
                    "severity": diagnostic.severity,
                    "code": diagnostic.code,
                    "message": diagnostic.message,
                }
                for diagnostic in self.diagnostics
            ],
        }
