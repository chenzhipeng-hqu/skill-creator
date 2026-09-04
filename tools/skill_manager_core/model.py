"""Domain types shared by Skill Manager scanning, planning, and reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CandidateSource(str, Enum):
    """The repository namespace that supplied a candidate."""

    DEVELOPMENT = "development"
    STABLE = "stable"


@dataclass(frozen=True)
class Diagnostic:
    """One stable, machine-readable observation about a manager command."""

    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class Candidate:
    """One discovered location, before structural validation."""

    source: CandidateSource
    name: str
    path: Path
    entry_kind: str


@dataclass(frozen=True)
class Scan:
    """The read-only discovered state used as a planning input."""

    candidates: tuple[Candidate, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class Plan:
    """A typed, immutable request prepared from one Scan."""

    command: str
    scan: Scan
    skill: str | None = None
    full_diff: bool = False
    validate_stable: bool = False
    validate_all: bool = False


@dataclass(frozen=True)
class Result:
    """One observable result from applying a Plan."""

    source: str
    skill: str
    path: str
    outcome: str
    details: dict[str, str] = field(default_factory=dict)
