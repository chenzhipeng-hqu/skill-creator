"""Candidate discovery without validation or filesystem mutation."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .model import Candidate, CandidateSource, Diagnostic, Scan


def scan_repository(repository_root: Path) -> Scan:
    """Discover Development and Stable candidate locations in one repository."""

    root_diagnostic = _repository_root_diagnostic(repository_root)
    if root_diagnostic is not None:
        return Scan(candidates=(), diagnostics=(root_diagnostic,))

    development_candidates, development_diagnostics = _development_candidates(repository_root)
    stable_candidates, stable_diagnostics = _stable_candidates(repository_root / "skills")
    candidates = [
        *development_candidates,
        *stable_candidates,
    ]
    return Scan(
        candidates=tuple(candidates),
        diagnostics=tuple((*development_diagnostics, *stable_diagnostics)),
    )


def _repository_root_diagnostic(repository_root: Path) -> Diagnostic | None:
    try:
        mode = repository_root.lstat().st_mode
    except FileNotFoundError:
        return Diagnostic(
            severity="error",
            code="REPOSITORY_NOT_FOUND",
            message=f"repository root does not exist: {repository_root}",
        )
    except OSError as exc:
        return Diagnostic(
            severity="error",
            code="DISCOVERY_IO_ERROR",
            message=f"cannot inspect repository root {repository_root}: {exc}",
        )
    if not stat.S_ISDIR(mode):
        return Diagnostic(
            severity="error",
            code="REPOSITORY_ROOT_NOT_DIRECTORY",
            message=f"repository root is not a real directory: {repository_root}",
        )
    return None


def _development_candidates(repository_root: Path) -> tuple[list[Candidate], list[Diagnostic]]:
    candidates: list[Candidate] = []
    diagnostics: list[Diagnostic] = []
    try:
        entries = sorted(repository_root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return [], [_discovery_io_diagnostic(repository_root, exc)]
    for entry in entries:
        if entry.name.startswith(".") or entry.name in {"skills", "tools", "tests", "docs"}:
            continue
        try:
            if entry.name.startswith("sk-") or _looks_like_unprefixed_development_directory(entry):
                candidates.append(_candidate(CandidateSource.DEVELOPMENT, entry))
        except OSError as exc:
            diagnostics.append(_discovery_io_diagnostic(entry, exc))
    return candidates, diagnostics


def _looks_like_unprefixed_development_directory(entry: Path) -> bool:
    """Keep an attempted root Development Skill visible for later validation."""

    if entry.is_symlink() or not entry.is_dir():
        return False
    if os.path.lexists(entry / "SKILL.md"):
        return True
    agents_directory = entry / "agents"
    if not os.path.lexists(agents_directory):
        return False
    if agents_directory.is_symlink():
        return True
    return agents_directory.is_dir() and os.path.lexists(agents_directory / "openai.yaml")


def _stable_candidates(skills_root: Path) -> tuple[list[Candidate], list[Diagnostic]]:
    if not os.path.lexists(skills_root):
        return [], []
    if skills_root.is_symlink():
        return [], [
            Diagnostic(
                severity="error",
                code="SYMLINKED_STABLE_NAMESPACE",
                message=f"stable namespace may not be a symbolic link: {skills_root}",
            )
        ]
    if not skills_root.is_dir():
        return [], [
            Diagnostic(
                severity="error",
                code="STABLE_NAMESPACE_NOT_DIRECTORY",
                message=f"stable namespace is not a directory: {skills_root}",
            )
        ]
    try:
        entries = sorted(skills_root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return [], [_discovery_io_diagnostic(skills_root, exc)]
    candidates: list[Candidate] = []
    diagnostics: list[Diagnostic] = []
    for entry in entries:
        if entry.name == "__pycache__":
            continue
        try:
            candidates.append(_candidate(CandidateSource.STABLE, entry))
        except OSError as exc:
            diagnostics.append(_discovery_io_diagnostic(entry, exc))
    return candidates, diagnostics


def _discovery_io_diagnostic(path: Path, exc: OSError) -> Diagnostic:
    return Diagnostic(
        severity="error",
        code="DISCOVERY_IO_ERROR",
        message=f"cannot inspect {path}: {exc}",
    )


def _candidate(source: CandidateSource, entry: Path) -> Candidate:
    mode = entry.lstat().st_mode
    if stat.S_ISLNK(mode):
        entry_kind = "symlink"
    elif stat.S_ISDIR(mode):
        entry_kind = "directory"
    elif stat.S_ISREG(mode):
        entry_kind = "file"
    else:
        entry_kind = "other"
    return Candidate(source=source, name=entry.name, path=entry, entry_kind=entry_kind)
