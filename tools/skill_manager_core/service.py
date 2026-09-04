"""The public scan → plan → apply behavior seam."""

from __future__ import annotations

import difflib
from pathlib import Path

from .discovery import scan_repository
from .identity import IdentityError, canonical_skill_name, package_identity, package_manifest
from .model import Candidate, CandidateSource, Diagnostic, Plan, Result, Scan
from .reporting import OperationReport, utc_now
from .validation import validate_candidate


TEXT_DIFF_MAX_BYTES = 1024 * 1024
TEXT_DIFF_MAX_LINES = 10_000


class SkillManager:
    """Plan and apply repository Skill Manager commands."""

    def __init__(self, repository_root: Path) -> None:
        self._repository_root = repository_root

    def scan(self) -> Scan:
        """Read current candidate locations without validating or mutating them."""

        return scan_repository(self._repository_root)

    def plan(
        self,
        command: str,
        scan: Scan,
        skill: str | None = None,
        full_diff: bool = False,
        validate_stable: bool = False,
        validate_all: bool = False,
    ) -> Plan:
        """Prepare one typed command from a Scan."""

        if command not in {"list", "status", "diff", "validate"}:
            raise ValueError(f"unsupported command: {command}")
        return Plan(
            command=command,
            scan=scan,
            skill=skill,
            full_diff=full_diff,
            validate_stable=validate_stable,
            validate_all=validate_all,
        )

    def apply(self, plan: Plan) -> OperationReport:
        """Apply a non-mutating list Plan and return its common report."""

        started_at = utc_now()
        if plan.command == "list":
            results = tuple(
                Result(
                    source=candidate.source.value,
                    skill=candidate.name,
                    path=str(candidate.path),
                    outcome="discovered",
                )
                for candidate in plan.scan.candidates
            )
            diagnostics = plan.scan.diagnostics
        elif plan.command == "status":
            results, status_diagnostics = _status_results(
                plan.scan.candidates,
                self._repository_root,
            )
            diagnostics = (*plan.scan.diagnostics, *status_diagnostics)
        elif plan.command == "diff":
            results, diff_diagnostics = _diff_results(
                plan.scan.candidates,
                plan.skill,
                plan.full_diff,
            )
            diagnostics = (*plan.scan.diagnostics, *diff_diagnostics)
        else:
            results, validation_diagnostics = _validate_results(
                plan.scan.candidates,
                plan.skill,
                plan.validate_stable,
                plan.validate_all,
                self._repository_root,
            )
            diagnostics = (*plan.scan.diagnostics, *validation_diagnostics)
        return OperationReport(
            command=plan.command,
            dry_run=False,
            status="failed"
            if any(diagnostic.severity == "error" for diagnostic in diagnostics)
            else "success",
            changed=False,
            transaction_id=None,
            started_at=started_at,
            finished_at=utc_now(),
            results=results,
            diagnostics=diagnostics,
        )


def _status_results(
    candidates: tuple[Candidate, ...],
    repository_root: Path,
) -> tuple[tuple[Result, ...], tuple[Diagnostic, ...]]:
    results: list[Result] = []
    diagnostics: list[Diagnostic] = []
    paired: dict[str, dict[CandidateSource, Candidate]] = {}
    for candidate in candidates:
        skill_name = canonical_skill_name(candidate)
        if skill_name is None:
            code = (
                "MISSING_DEVELOPMENT_PREFIX"
                if candidate.source is CandidateSource.DEVELOPMENT
                else "INVALID_SKILL_NAME"
            )
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    code=code,
                    message=f"invalid {candidate.source.value} candidate name: {candidate.name}",
                )
            )
            results.append(
                Result(
                    source=candidate.source.value,
                    skill=candidate.name,
                    path=str(candidate.path),
                    outcome="invalid-candidate",
                    details={"validation_state": "invalid"},
                )
            )
            continue
        paired.setdefault(skill_name, {})[candidate.source] = candidate
    for skill_name in sorted(paired):
        copies = paired[skill_name]
        development = copies.get(CandidateSource.DEVELOPMENT)
        stable = copies.get(CandidateSource.STABLE)
        identities: dict[str, str] = {}
        structural_diagnostics: dict[str, tuple[Diagnostic, ...]] = {}
        for source, candidate in (("development", development), ("stable", stable)):
            if candidate is None:
                continue
            candidate_diagnostics = validate_candidate(candidate, repository_root)
            structural_diagnostics[source] = candidate_diagnostics
            diagnostics.extend(candidate_diagnostics)
            try:
                identities[source] = package_identity(candidate.path)
            except (IdentityError, OSError) as exc:
                diagnostics.append(
                    Diagnostic(
                        severity="error",
                        code="PACKAGE_IDENTITY_ERROR",
                        message=f"cannot identify {source} candidate {candidate.name}: {exc}",
                    )
                )
        if development is None:
            lifecycle = "stable"
        elif stable is None:
            lifecycle = "unpublished"
        elif {"development", "stable"} - identities.keys():
            lifecycle = "unknown"
        elif identities.get("development") == identities.get("stable"):
            lifecycle = "promoted-clean"
        else:
            lifecycle = "modified"
        path = development.path if development is not None else stable.path
        results.append(
            Result(
                source="lifecycle",
                skill=skill_name,
                path=str(path),
                outcome=lifecycle,
                details={
                    "validation_state": (
                        "invalid"
                        if any(structural_diagnostics.values())
                        else "valid"
                    ),
                    "test_state": "not-run",
                    **{
                        f"{source}_validation_state": (
                            "invalid" if source_diagnostics else "valid"
                        )
                        for source, source_diagnostics in structural_diagnostics.items()
                    },
                    **{
                        f"{source}_identity": identity
                        for source, identity in identities.items()
                    },
                },
            )
        )
    return tuple(results), tuple(diagnostics)


def _validate_results(
    candidates: tuple[Candidate, ...],
    skill_name: str | None,
    validate_stable: bool,
    validate_all: bool,
    repository_root: Path,
) -> tuple[tuple[Result, ...], tuple[Diagnostic, ...]]:
    source = CandidateSource.STABLE if validate_stable else CandidateSource.DEVELOPMENT
    selected = [
        candidate
        for candidate in candidates
        if candidate.source is source
        and (validate_all or _matches_validation_selection(candidate, skill_name))
    ]
    selected.sort(key=lambda candidate: candidate.name)
    if not selected:
        return (), (
            Diagnostic(
                severity="error",
                code="VALIDATION_SOURCE_MISSING",
                message=f"{source.value} candidate not found: {skill_name}",
            ),
        )
    results: list[Result] = []
    diagnostics: list[Diagnostic] = []
    for candidate in selected:
        candidate_diagnostics = validate_candidate(candidate, repository_root)
        diagnostics.extend(candidate_diagnostics)
        results.append(
            Result(
                source=candidate.source.value,
                skill=candidate.name,
                path=str(candidate.path),
                outcome="valid" if not candidate_diagnostics else "invalid",
                details={
                    "validation_state": "valid" if not candidate_diagnostics else "invalid"
                },
            )
        )
    return tuple(results), tuple(diagnostics)


def _matches_validation_selection(candidate: Candidate, skill_name: str | None) -> bool:
    """Select explicit invalid candidates before canonical-name validation."""

    if skill_name is None:
        return False
    return skill_name in {
        candidate.name,
        canonical_skill_name(candidate),
        f"sk-{candidate.name}"
        if candidate.source is CandidateSource.DEVELOPMENT
        else candidate.name,
    }


def _diff_results(
    candidates: tuple[Candidate, ...],
    skill_name: str | None,
    full_diff: bool,
) -> tuple[tuple[Result, ...], tuple[Diagnostic, ...]]:
    if skill_name is None:
        return (), (
            Diagnostic(
                severity="error",
                code="MISSING_SKILL_SELECTION",
                message="diff requires one canonical Skill Name",
            ),
        )
    copies = _paired_candidates(candidates).get(skill_name)
    if copies is None or CandidateSource.DEVELOPMENT not in copies or CandidateSource.STABLE not in copies:
        return (), (
            Diagnostic(
                severity="error",
                code="DIFF_SOURCE_MISSING",
                message=f"diff requires Development and Stable candidates for {skill_name}",
            ),
        )
    try:
        development_manifest = package_manifest(
            copies[CandidateSource.DEVELOPMENT].path,
            allow_nonregular=True,
        )
        stable_manifest = package_manifest(
            copies[CandidateSource.STABLE].path,
            allow_nonregular=True,
        )
    except (IdentityError, OSError) as exc:
        return (), (
            Diagnostic(
                severity="error",
                code="PACKAGE_IDENTITY_ERROR",
                message=f"cannot diff {skill_name}: {exc}",
            ),
        )
    results: list[Result] = []
    text_bytes_used = 0
    text_lines_used = 0
    for path in sorted(set(development_manifest) | set(stable_manifest)):
        development = development_manifest.get(path)
        stable = stable_manifest.get(path)
        if stable is None:
            outcome = "added"
        elif development is None:
            outcome = "removed"
        elif development["file_type"] != stable["file_type"]:
            outcome = "file-type-changed"
        elif development["sha256"] != stable["sha256"]:
            outcome = "modified"
        elif development["executable"] != stable["executable"]:
            outcome = "executable-changed"
        else:
            continue
        details = {
            "development_sha256": "" if development is None else str(development["sha256"]),
            "stable_sha256": "" if stable is None else str(stable["sha256"]),
            "development_size": "" if development is None else str(development["size"]),
            "stable_size": "" if stable is None else str(stable["size"]),
            "development_type": "" if development is None else str(development["file_type"]),
            "stable_type": "" if stable is None else str(stable["file_type"]),
        }
        if (
            development is not None
            and stable is not None
            and development["executable"] != stable["executable"]
        ):
            details["executable_changed"] = "true"
        if outcome in {"added", "removed", "modified"} and (
            development is None or development["file_type"] == "regular"
        ) and (
            stable is None or stable["file_type"] == "regular"
        ):
            content_details, consumed_bytes, consumed_lines = _content_diff_details(
                None
                if development is None
                else copies[CandidateSource.DEVELOPMENT].path / path,
                None if stable is None else copies[CandidateSource.STABLE].path / path,
                None if full_diff else TEXT_DIFF_MAX_BYTES - text_bytes_used,
                None if full_diff else TEXT_DIFF_MAX_LINES - text_lines_used,
            )
            details.update(content_details)
            text_bytes_used += consumed_bytes
            text_lines_used += consumed_lines
        results.append(
            Result(
                source="diff",
                skill=skill_name,
                path=path,
                outcome=outcome,
                details=details,
            )
        )
    return tuple(results), ()


def _content_diff_details(
    development_path: Path | None,
    stable_path: Path | None,
    remaining_bytes: int | None,
    remaining_lines: int | None,
) -> tuple[dict[str, str], int, int]:
    """Describe content changes without ever embedding binary bytes."""

    development = b"" if development_path is None else development_path.read_bytes()
    stable = b"" if stable_path is None else stable_path.read_bytes()
    if b"\0" in development or b"\0" in stable:
        return {"content_kind": "binary", "diff_truncated": "false"}, 0, 0
    try:
        development_text = development.decode("utf-8")
        stable_text = stable.decode("utf-8")
    except UnicodeDecodeError:
        return {"content_kind": "binary", "diff_truncated": "false"}, 0, 0
    unified_diff_lines = list(
        difflib.unified_diff(
            stable_text.splitlines(keepends=True),
            development_text.splitlines(keepends=True),
            fromfile="stable",
            tofile="development",
        )
    )
    emitted: list[str] = []
    emitted_bytes = 0
    truncated = False
    for line in unified_diff_lines:
        encoded_length = len(line.encode("utf-8"))
        if (
            remaining_lines is not None
            and len(emitted) >= max(remaining_lines, 0)
        ) or (
            remaining_bytes is not None
            and emitted_bytes + encoded_length > max(remaining_bytes, 0)
        ):
            truncated = True
            break
        emitted.append(line)
        emitted_bytes += encoded_length
    return (
        {
            "content_kind": "text",
            "unified_diff": "".join(emitted),
            "diff_truncated": "true" if truncated else "false",
        },
        emitted_bytes,
        len(emitted),
    )


def _paired_candidates(
    candidates: tuple[Candidate, ...],
) -> dict[str, dict[CandidateSource, Candidate]]:
    paired: dict[str, dict[CandidateSource, Candidate]] = {}
    for candidate in candidates:
        skill_name = canonical_skill_name(candidate)
        if skill_name is not None:
            paired.setdefault(skill_name, {})[candidate.source] = candidate
    return paired
