"""The public scan → plan → apply behavior seam."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .discovery import scan_repository
from .filesystem import mutation_filesystem_error
from .identity import IdentityError, canonical_skill_name, package_identity, package_manifest
from .journal import JournalError, append_event, read_events, read_plan, write_plan
from .locking import (
    ManagerBusyError,
    exclusive_repository_lock,
    exclusive_target_locks,
    shared_existing_locks,
)
from .model import Candidate, CandidateSource, Diagnostic, Plan, Result, Scan
from .reporting import OperationReport, utc_now
from .provenance import stable_provenance
from .release import (
    git_output,
    git_release_preflight,
    is_scoped_release_commit,
    read_commit_message,
    record_skill_release,
    release_tag_name,
    rollback_git_release,
)
from .test_runner import run_skill_test
from .transaction import (
    clean_rolled_back_transaction as _clean_rolled_back_transaction,
    create_transaction_directory as _create_transaction_directory,
    fsync_directory as _fsync_directory,
    fsync_tree as _fsync_tree,
    link_state as _link_state,
    package_identity_or_unavailable as _package_identity_or_unavailable,
    restore_link_state as _restore_link_state,
    rollback_installation as _rollback_installation,
    tree_identity as _tree_identity,
)
from .targets import (
    apply_installation,
    base_targets,
    hermes_target,
    is_exact_managed_link,
    opencode_target,
)
from .validation import validate_candidate


TEXT_DIFF_MAX_BYTES = 1024 * 1024
TEXT_DIFF_MAX_LINES = 10_000


class SkillManager:
    """Plan and apply repository Skill Manager commands."""

    def __init__(
        self,
        repository_root: Path,
        *,
        home: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._repository_root = repository_root
        self._home = home or Path.home()
        self._environ = dict(
            ({} if home is not None else os.environ) if environ is None else environ
        )

    def scan(self) -> Scan:
        """Read current candidate locations without validating or mutating them."""

        return scan_repository(self._repository_root)

    def detected_targets(self):
        """Return the Adapter-resolved detected targets for the TUI."""

        return {
            key: target
            for key, target in _resolved_targets(self._home, self._environ).items()
            if target.detected()
        }

    def plan(
        self,
        command: str,
        scan: Scan,
        skill: str | None = None,
        full_diff: bool = False,
        validate_stable: bool = False,
        validate_all: bool = False,
        dry_run: bool = False,
        skills: tuple[str, ...] = (),
        agents: tuple[str, ...] = (),
        select_all: bool = False,
        replace_stable_drift: bool = False,
        record_release: bool = False,
        commit_message_files: tuple[str, ...] = (),
    ) -> Plan:
        """Prepare one typed command from a Scan."""

        if command not in {"list", "status", "diff", "validate", "promote", "recover", "install", "uninstall"}:
            raise ValueError(f"unsupported command: {command}")
        return Plan(
            command=command,
            scan=scan,
            skill=skill,
            full_diff=full_diff,
            validate_stable=validate_stable,
            validate_all=validate_all,
            dry_run=dry_run,
            skills=skills,
            agents=agents,
            select_all=select_all,
            replace_stable_drift=replace_stable_drift,
            record_release=record_release,
            commit_message_files=commit_message_files,
        )

    def apply(self, plan: Plan) -> OperationReport:
        """Apply one Plan while respecting read-only and dry-run locks."""

        inspection = plan.dry_run or plan.command in {"list", "status", "diff", "validate"}
        if not inspection:
            return self._apply_unlocked(plan)
        targets = _resolved_targets(self._home, self._environ)
        lock_paths = [self._repository_root / ".skill-manager" / "repository.lock"]
        if plan.command in {"status", "install", "uninstall"}:
            selected = plan.agents or tuple(
                key for key, target in targets.items() if target.detected()
            )
            lock_paths.extend(
                targets[key].config_root / ".skill-manager.lock"
                for key in selected
                if targets[key].detected()
            )
        try:
            with shared_existing_locks(tuple(lock_paths)):
                fresh_plan = Plan(
                    **{
                        **plan.__dict__,
                        "scan": self.scan(),
                    }
                )
                return self._apply_unlocked(fresh_plan)
        except ManagerBusyError as exc:
            instant = utc_now()
            return OperationReport(
                command=plan.command,
                dry_run=plan.dry_run,
                status="busy",
                changed=False,
                transaction_id=None,
                started_at=instant,
                finished_at=instant,
                results=(),
                diagnostics=(Diagnostic("error", "MANAGER_BUSY", str(exc)),),
            )

    def _apply_unlocked(self, plan: Plan) -> OperationReport:
        """Apply a Plan after any inspection locks have been acquired."""

        started_at = utc_now()
        transaction_id: str | None = None
        changed = False
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
                plan.skills,
                plan.agents,
                self._home,
                self._environ,
            )
            diagnostics = (*plan.scan.diagnostics, *status_diagnostics)
        elif plan.command == "diff":
            diff_results: list[Result] = []
            diff_diagnostic_list: list[Diagnostic] = []
            for selected_skill in plan.skills or ((plan.skill,) if plan.skill else (None,)):
                one_results, one_diagnostics = _diff_results(
                    plan.scan.candidates,
                    selected_skill,
                    plan.full_diff,
                )
                diff_results.extend(one_results)
                diff_diagnostic_list.extend(one_diagnostics)
            results = tuple(diff_results)
            diff_diagnostics = tuple(diff_diagnostic_list)
            diagnostics = (*plan.scan.diagnostics, *diff_diagnostics)
        elif plan.command == "validate":
            results, validation_diagnostics = _validate_results(
                plan.scan.candidates,
                plan.skill,
                plan.skills,
                plan.validate_stable,
                plan.validate_all,
                self._repository_root,
            )
            diagnostics = (*plan.scan.diagnostics, *validation_diagnostics)
        elif plan.command == "promote":
            try:
                if plan.dry_run:
                    results, promotion_diagnostics = _promote_result(
                        plan.scan.candidates,
                        plan.skills or ((plan.skill,) if plan.skill else ()),
                        plan.select_all or plan.validate_all,
                        self._repository_root,
                        True,
                        plan.replace_stable_drift,
                        plan.record_release,
                        plan.commit_message_files,
                    )
                else:
                    _, initial_diagnostics = _promote_result(
                        plan.scan.candidates,
                        plan.skills or ((plan.skill,) if plan.skill else ()),
                        plan.select_all or plan.validate_all,
                        self._repository_root,
                        True,
                        plan.replace_stable_drift,
                        plan.record_release,
                        plan.commit_message_files,
                    )
                    if initial_diagnostics:
                        results = ()
                        promotion_diagnostics = initial_diagnostics
                    else:
                        with exclusive_repository_lock(self._repository_root):
                            with exclusive_target_locks(
                                _recovery_target_roots(self._repository_root)
                            ):
                                _, recovery_diagnostics = _recover_result(self._repository_root, False)
                                if recovery_diagnostics:
                                    results = ()
                                    promotion_diagnostics = recovery_diagnostics
                                else:
                                    fresh_scan = self.scan()
                                    results, promotion_diagnostics = _promote_result(
                                        fresh_scan.candidates,
                                        plan.skills or ((plan.skill,) if plan.skill else ()),
                                        plan.select_all or plan.validate_all,
                                        self._repository_root,
                                        False,
                                        plan.replace_stable_drift,
                                        plan.record_release,
                                        plan.commit_message_files,
                                    )
            except ManagerBusyError as exc:
                results = ()
                promotion_diagnostics = (Diagnostic("error", "MANAGER_BUSY", str(exc)),)
            diagnostics = (*plan.scan.diagnostics, *promotion_diagnostics)
            if results and any(result.outcome == "promoted" for result in results):
                promoted_result = next(
                    result for result in results if result.outcome == "promoted"
                )
                recorded_transaction = promoted_result.details.get("transaction_id")
                transaction_id = str(recorded_transaction) if recorded_transaction else None
                changed = True
        elif plan.command in {"install", "uninstall"}:
            try:
                if plan.dry_run:
                    results, installation_diagnostics, transaction_id = _installation_results(
                        plan.scan.candidates,
                        plan.skills,
                        plan.agents,
                        plan.command == "uninstall",
                        True,
                        self._home,
                        self._repository_root,
                        self._environ,
                        plan.select_all,
                    )
                else:
                    targets = _resolved_targets(self._home, self._environ)
                    initial_results, initial_diagnostics, _ = _installation_results(
                        plan.scan.candidates,
                        plan.skills,
                        plan.agents,
                        plan.command == "uninstall",
                        True,
                        self._home,
                        self._repository_root,
                        self._environ,
                        plan.select_all,
                    )
                    if any(item.severity == "error" for item in initial_diagnostics):
                        results = initial_results
                        installation_diagnostics = initial_diagnostics
                    else:
                        with exclusive_repository_lock(self._repository_root):
                            recovery_roots = _recovery_target_roots(self._repository_root)
                            selected_roots = tuple(
                                targets[key].config_root
                                for key in plan.agents
                                if targets[key].detected()
                            )
                            with exclusive_target_locks(tuple((*recovery_roots, *selected_roots))):
                                recovery_results, recovery_diagnostics = _recover_result(
                                    self._repository_root, False
                                )
                                if recovery_diagnostics:
                                    results = recovery_results
                                    installation_diagnostics = recovery_diagnostics
                                else:
                                    fresh_scan = self.scan()
                                    results, installation_diagnostics, transaction_id = _installation_results(
                                        fresh_scan.candidates,
                                        plan.skills,
                                        plan.agents,
                                        plan.command == "uninstall",
                                        False,
                                        self._home,
                                        self._repository_root,
                                        self._environ,
                                        plan.select_all,
                                    )
            except ManagerBusyError as exc:
                results = ()
                installation_diagnostics = (Diagnostic("error", "MANAGER_BUSY", str(exc)),)
            diagnostics = (*plan.scan.diagnostics, *installation_diagnostics)
            changed = any(result.outcome in {"installed", "uninstalled"} for result in results)
        else:
            try:
                if plan.dry_run:
                    results, recovery_diagnostics = _recover_result(self._repository_root, True)
                else:
                    recovery_roots = _recovery_target_roots(self._repository_root)
                    filesystem_error = mutation_filesystem_error(
                        (self._repository_root, *recovery_roots)
                    )
                    if filesystem_error is not None:
                        results = ()
                        recovery_diagnostics = (
                            Diagnostic(
                                "error",
                                filesystem_error,
                                "filesystem does not support durable Atomic Batch mutation",
                            ),
                        )
                    else:
                        with exclusive_repository_lock(self._repository_root):
                            with exclusive_target_locks(recovery_roots):
                                results, recovery_diagnostics = _recover_result(self._repository_root, False)
            except ManagerBusyError as exc:
                results = ()
                recovery_diagnostics = (Diagnostic("error", "MANAGER_BUSY", str(exc)),)
            diagnostics = (*plan.scan.diagnostics, *recovery_diagnostics)
        if plan.command != "recover" and (
            plan.dry_run or plan.command in {"list", "status", "diff", "validate"}
        ):
            active_diagnostic = _active_transaction_diagnostic(self._repository_root)
            if active_diagnostic is not None:
                diagnostics = (*diagnostics, active_diagnostic)
        error_codes = {
            diagnostic.code
            for diagnostic in diagnostics
            if diagnostic.severity == "error"
        }
        if "MANAGER_BUSY" in error_codes:
            report_status = "busy"
        elif error_codes & {"RECOVERY_REQUIRED", "JOURNAL_CONFLICT"}:
            report_status = "recovery-required"
        elif error_codes:
            report_status = "failed"
        elif plan.command in {"promote", "install", "uninstall"} and not plan.dry_run and not changed:
            report_status = "no-change"
        else:
            report_status = "success"
        return OperationReport(
            command=plan.command,
            dry_run=plan.dry_run,
            status=report_status,
            changed=changed,
            transaction_id=transaction_id,
            started_at=started_at,
            finished_at=utc_now(),
            results=results,
            diagnostics=diagnostics,
        )


def _installation_results(
    candidates: tuple[Candidate, ...],
    skills: tuple[str, ...],
    agents: tuple[str, ...],
    uninstall: bool,
    dry_run: bool,
    home: Path,
    repository_root: Path,
    environ: Mapping[str, str],
    select_all: bool,
) -> tuple[tuple[Result, ...], tuple[Diagnostic, ...], str | None]:
    stable = {
        canonical_skill_name(candidate): candidate
        for candidate in candidates
        if candidate.source is CandidateSource.STABLE
        and canonical_skill_name(candidate)
    }
    selected_skills = (
        tuple(sorted(stable))
        if select_all
        else tuple(sorted(dict.fromkeys(skills)))
    )
    selected_agents = tuple(sorted(dict.fromkeys(agents)))
    targets = _resolved_targets(home, environ)
    opencode_config_dir = environ.get("OPENCODE_CONFIG_DIR")
    diagnostics: list[Diagnostic] = []
    hard_conflicts = (
        {"codex", "agents"},
        {"opencode", "agents"},
        {"opencode", "claude"},
    )
    selected_agent_set = set(selected_agents)
    if any(pair <= selected_agent_set for pair in hard_conflicts):
        diagnostics.append(
            Diagnostic(
                "error",
                "TARGET_COMBINATION_CONFLICT",
                "selected Agent Targets expose duplicate Skill roots",
            )
        )
    for agent in selected_agents:
        target = targets[agent]
        if not target.detected():
            diagnostics.append(Diagnostic("error", "TARGET_NOT_DETECTED", f"target not detected: {agent}"))
    for skill in selected_skills:
        candidate = stable.get(skill)
        if candidate is None:
            if not uninstall:
                diagnostics.append(Diagnostic("error", "STABLE_SKILL_NOT_FOUND", f"stable Skill not found: {skill}"))
            continue
        if not uninstall:
            diagnostics.extend(validate_candidate(candidate, repository_root))
            provenance = stable_provenance(repository_root, skill, candidate.path)
            if provenance == "drifted":
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "INSTALLING_DRIFTED_STABLE",
                        f"installing drifted Stable Skill: {skill}",
                    )
                )
            elif provenance == "unknown":
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "INSTALLING_UNPROVEN_STABLE",
                        f"installing Stable Skill without proven provenance: {skill}",
                    )
                )
    if "opencode" in selected_agent_set and not uninstall:
        for skill in selected_skills:
            compatibility_roots = [
                home / ".agents" / "skills",
                home / ".claude" / "skills",
            ]
            if opencode_config_dir:
                compatibility_roots.append(Path(opencode_config_dir) / "skills")
            for compatibility_root in compatibility_roots:
                if compatibility_root == targets["opencode"].skills_root:
                    continue
                duplicate = compatibility_root / skill
                if os.path.lexists(duplicate):
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "DUPLICATE_EXPOSURE",
                            f"OpenCode also discovers {duplicate}",
                        )
                    )
    planned: list[Result] = []
    for skill in selected_skills:
        for agent in selected_agents:
            candidate = stable.get(skill)
            stable_path = candidate.path if candidate is not None else repository_root / "skills" / skill
            if not targets[agent].detected():
                outcome = "target-not-detected"
            elif candidate is None and not uninstall:
                outcome = "stable-not-found"
            else:
                outcome = apply_installation(
                    targets[agent], stable_path, uninstall=uninstall, dry_run=True
                )
            planned.append(
                Result(
                    "stable",
                    skill,
                    str(stable_path),
                    outcome,
                    {
                        "config_root": str(targets[agent].config_root),
                        "skill_root": str(targets[agent].skills_root),
                        "package_identity": _package_identity_or_unavailable(stable_path),
                    },
                    target=agent,
                )
            )
            if outcome in {"conflict", "target-root-conflict"}:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "INSTALLATION_CONFLICT",
                        f"destination conflict for {skill} on {agent}: {outcome}",
                    )
                )
    filesystem_error = mutation_filesystem_error(
        (
            repository_root,
            *(targets[agent].config_root for agent in selected_agents),
        )
    )
    if filesystem_error is not None:
        diagnostics.append(
            Diagnostic("error", filesystem_error, "filesystem does not support durable Atomic Batch mutation")
        )
    state_diagnostic = _manager_state_diagnostic(repository_root)
    if state_diagnostic is not None:
        diagnostics.append(state_diagnostic)
    if any(item.severity == "error" for item in diagnostics) or dry_run:
        return tuple(planned), tuple(diagnostics), None

    changes = [item for item in planned if item.outcome in {"would-install", "would-uninstall"}]
    if not changes:
        return tuple(planned), tuple(diagnostics), None
    transaction_id = str(uuid.uuid4())
    transaction = _create_transaction_directory(repository_root, transaction_id)
    operations: list[dict[str, object]] = []
    for item in planned:
        target = targets[item.target or ""]
        destination = target.skills_root / item.skill
        before = _link_state(destination)
        after = (
            {"kind": "absent"}
            if uninstall
            else {"kind": "symlink", "target": str(Path(item.path))}
        )
        operations.append(
            {
                "skill": item.skill,
                "target": item.target,
                "stable": item.path,
                "destination": str(destination),
                "skills_root": str(target.skills_root),
                "skills_root_existed": os.path.lexists(target.skills_root),
                "before": before,
                "after": after,
                "stable_identity": (
                    _package_identity_or_unavailable(Path(item.path))
                ),
            }
        )
    plan_hash = write_plan(
        transaction / "plan.json",
        {
            "journal_schema_version": 1,
            "transaction_id": transaction_id,
            "operation_type": "uninstall" if uninstall else "install",
            "operations": operations,
        },
    )
    events = transaction / "events.jsonl"
    append_event(events, plan_hash, "PREPARED")
    results: list[Result] = []
    try:
        append_event(events, plan_hash, "APPLYING")
        for index, (item, operation) in enumerate(zip(planned, operations, strict=True)):
            if operation["before"] == operation["after"]:
                results.append(item)
                continue
            append_event(events, plan_hash, "PENDING", {"operation": index})
            destination = Path(str(operation["destination"]))
            current = _link_state(destination)
            if current != operation["before"]:
                raise OSError(f"destination changed after preflight: {destination}")
            skills_root = Path(str(operation["skills_root"]))
            if not uninstall:
                skills_root.mkdir(mode=0o755, exist_ok=True)
                destination.symlink_to(Path(str(operation["stable"])))
                outcome = "installed"
            else:
                destination.unlink()
                outcome = "uninstalled"
            _fsync_directory(skills_root)
            append_event(events, plan_hash, "APPLIED", {"operation": index})
            results.append(
                Result(item.source, item.skill, item.path, outcome, item.details, target=item.target)
            )
        append_event(events, plan_hash, "COMMITTED")
    except OSError as exc:
        rollback_diagnostics = _rollback_installation(transaction, operations, plan_hash)
        diagnostics.append(Diagnostic("error", "INSTALLATION_APPLY_FAILED", str(exc)))
        diagnostics.extend(rollback_diagnostics)
        if not rollback_diagnostics:
            _clean_rolled_back_transaction(transaction, plan_hash)
        return tuple(planned), tuple(diagnostics), transaction_id
    try:
        append_event(events, plan_hash, "CLEANING")
        append_event(events, plan_hash, "CLEANED")
        shutil.rmtree(transaction)
        _fsync_directory(transaction.parent)
    except OSError as exc:
        diagnostics.append(
            Diagnostic("warning", "TRANSACTION_CLEANUP_PENDING", str(exc))
        )
    return tuple(results), tuple(diagnostics), transaction_id


def _resolved_targets(home: Path, environ: Mapping[str, str]):
    targets = base_targets(home)
    hermes_home = environ.get("HERMES_HOME")
    xdg_config_home = environ.get("XDG_CONFIG_HOME")
    opencode_config_dir = environ.get("OPENCODE_CONFIG_DIR")
    targets["hermes"] = hermes_target(home, Path(hermes_home) if hermes_home else None)
    targets["opencode"] = opencode_target(
        home,
        Path(xdg_config_home) if xdg_config_home else None,
        Path(opencode_config_dir) if opencode_config_dir else None,
    )
    return targets


def _status_results(
    candidates: tuple[Candidate, ...],
    repository_root: Path,
    selected_skills: tuple[str, ...] = (),
    selected_agents: tuple[str, ...] = (),
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
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
    targets = _resolved_targets(home or Path.home(), environ or {})
    agent_keys = (
        tuple(dict.fromkeys(selected_agents))
        if selected_agents
        else tuple(key for key, target in targets.items() if target.detected())
    )
    for agent in selected_agents:
        if not targets[agent].detected():
            diagnostics.append(
                Diagnostic("error", "TARGET_NOT_DETECTED", f"target not detected: {agent}")
            )
    for skill_name in sorted(paired):
        if selected_skills and skill_name not in selected_skills:
            continue
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
        installations: dict[str, object] = {}
        stable_path = repository_root / "skills" / skill_name
        for agent in agent_keys:
            target = targets[agent]
            if not target.detected():
                continue
            destination = target.skills_root / skill_name
            if not os.path.lexists(destination):
                installation_state = "not-installed"
            elif is_exact_managed_link(destination, stable_path):
                installation_state = "installed"
            else:
                installation_state = "conflict"
            installations[agent] = {
                "state": installation_state,
                "config_root": str(target.config_root),
                "skill_root": str(target.skills_root),
            }
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
                    "stable_provenance": (
                        stable_provenance(repository_root, skill_name, stable.path)
                        if stable is not None and stable.entry_kind == "directory"
                        else "absent"
                    ),
                    "installation_state": installations,
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


def _promote_result(
    candidates: tuple[Candidate, ...],
    skill_names: tuple[str, ...],
    select_all: bool,
    root: Path,
    dry_run: bool,
    replace_stable_drift: bool,
    record_release: bool,
    commit_message_files: tuple[str, ...],
) -> tuple[tuple[Result, ...], tuple[Diagnostic, ...]]:
    development = {
        name: candidate
        for candidate in candidates
        if candidate.source is CandidateSource.DEVELOPMENT
        and (name := canonical_skill_name(candidate)) is not None
    }
    selected_names = tuple(sorted(development)) if select_all else tuple(sorted(dict.fromkeys(skill_names)))
    missing = [name for name in selected_names if name not in development]
    if missing:
        return (), tuple(
            Diagnostic("error", "PROMOTION_SOURCE_MISSING", f"development candidate not found: {name}")
            for name in missing
        )
    selected = [development[name] for name in selected_names]
    diagnostics = [
        diagnostic
        for candidate in selected
        for diagnostic in validate_candidate(candidate, root)
    ]
    if diagnostics:
        return tuple(
            Result("development", candidate.name, str(candidate.path), "invalid")
            for candidate in selected
        ), tuple(diagnostics)
    changes: list[Candidate] = []
    no_change: list[Result] = []
    for candidate in selected:
        skill = canonical_skill_name(candidate)
        assert skill is not None
        stable = root / "skills" / skill
        if stable.is_dir():
            try:
                identical = package_identity(candidate.path) == package_identity(stable)
            except (IdentityError, OSError):
                identical = False
            if identical:
                no_change.append(
                    Result(
                        "development",
                        candidate.name,
                        str(candidate.path),
                        "no-change",
                        {
                            "test_state": "not-run",
                            "package_identity": package_identity(candidate.path),
                        },
                    )
                )
                continue
            provenance = stable_provenance(root, skill, stable)
            if provenance != "clean" and not replace_stable_drift:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "STABLE_DRIFT_REQUIRES_REPLACEMENT_FLAG",
                        f"refusing to replace {provenance} Stable Skill: {skill}",
                    )
                )
        changes.append(candidate)
    messages, message_metadata, message_diagnostics = _promotion_messages(commit_message_files, changes)
    diagnostics.extend(message_diagnostics)
    if record_release:
        changed_names = {str(canonical_skill_name(candidate)) for candidate in changes}
        if set(messages) != changed_names:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "COMMIT_MESSAGE_SET_MISMATCH",
                    "provide exactly one message file per changed Skill",
                )
            )
        failure = git_release_preflight(root)
        if failure is not None:
            diagnostics.append(Diagnostic("error", failure, "Git release preflight failed"))
    elif commit_message_files:
        diagnostics.append(Diagnostic("error", "UNEXPECTED_COMMIT_MESSAGE", "commit messages require release recording"))
    if diagnostics:
        return tuple(no_change), tuple(diagnostics)
    state_diagnostic = _manager_state_diagnostic(root)
    if state_diagnostic is not None:
        return tuple(no_change), (state_diagnostic,)
    filesystem_error = mutation_filesystem_error((root,))
    if filesystem_error is not None:
        return tuple(no_change), (
            Diagnostic("error", filesystem_error, "filesystem does not support durable Atomic Batch mutation"),
        )
    if dry_run:
        provisional_tags: dict[str, str] = {}
        if record_release:
            existing_tags = set(git_output(root, "tag", "--list").splitlines())
            release_instant = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
            for candidate in changes:
                skill = str(canonical_skill_name(candidate))
                tag = release_tag_name(skill, release_instant, existing_tags)
                provisional_tags[skill] = tag
                existing_tags.add(tag)
        return tuple(sorted((*no_change, *(
            Result(
                "development",
                candidate.name,
                str(candidate.path),
                "would-promote",
                {
                    "test_state": "not-run",
                    "package_identity": package_identity(candidate.path),
                    "release_state": "provisional" if record_release else "not-requested",
                    **(
                        {"release_tag": provisional_tags[str(canonical_skill_name(candidate))]}
                        if record_release
                        else {}
                    ),
                },
            )
            for candidate in changes
        )), key=lambda item: item.skill)), ()
    if not changes:
        return tuple(no_change), ()
    promoted, promotion_diagnostics = _promote_batch(
        changes,
        root,
        False,
        release_messages=messages if record_release else None,
        release_message_metadata=message_metadata if record_release else None,
    )
    return tuple(sorted((*no_change, *promoted), key=lambda item: item.skill)), promotion_diagnostics


def _promotion_messages(
    specifications: tuple[str, ...],
    changed: list[Candidate],
) -> tuple[dict[str, str], dict[str, dict[str, str]], tuple[Diagnostic, ...]]:
    paths: dict[str, Path] = {}
    diagnostics: list[Diagnostic] = []
    for specification in specifications:
        if "=" not in specification:
            diagnostics.append(Diagnostic("error", "INVALID_COMMIT_MESSAGE_MAPPING", specification))
            continue
        skill, raw_path = specification.split("=", 1)
        if not skill or not raw_path or skill in paths:
            diagnostics.append(Diagnostic("error", "INVALID_COMMIT_MESSAGE_MAPPING", specification))
            continue
        paths[skill] = Path(raw_path)
    changed_names = {canonical_skill_name(candidate) for candidate in changed}
    if specifications and set(paths) != changed_names:
        diagnostics.append(
            Diagnostic("error", "COMMIT_MESSAGE_SET_MISMATCH", "provide exactly one message file per changed Skill")
        )
    messages: dict[str, str] = {}
    metadata: dict[str, dict[str, str]] = {}
    for skill, path in paths.items():
        try:
            messages[skill] = read_commit_message(path)
            metadata[skill] = {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        except ValueError as exc:
            diagnostics.append(Diagnostic("error", "INVALID_COMMIT_MESSAGE_FILE", str(exc)))
        except OSError as exc:
            diagnostics.append(Diagnostic("error", "INVALID_COMMIT_MESSAGE_FILE", str(exc)))
    return messages, metadata, tuple(diagnostics)


def _promote_batch(
    candidates: list[Candidate],
    root: Path,
    dry_run: bool,
    *,
    release_messages: dict[str, str] | None = None,
    release_message_metadata: dict[str, dict[str, str]] | None = None,
) -> tuple[tuple[Result, ...], tuple[Diagnostic, ...]]:
    if dry_run:
        return tuple(Result("development", candidate.name, str(candidate.path), "would-promote", {"test_state": "not-run"}) for candidate in candidates), ()
    for candidate in candidates:
        test = run_skill_test(candidate, root)
        if test.diagnostics:
            return (Result("development", candidate.name, str(candidate.path), "invalid", test.details),), test.diagnostics
    transaction_id = str(uuid.uuid4())
    release_instant = datetime.now(timezone.utc)
    original_head: str | None = None
    original_branch: str | None = None
    release_tags: dict[str, str] = {}
    release_commits: dict[str, str] = {}
    if release_messages is not None:
        original_head = git_output(root, "rev-parse", "HEAD")
        original_branch = git_output(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        existing_tags = set(git_output(root, "tag", "--list").splitlines())
        for candidate in candidates:
            skill = str(canonical_skill_name(candidate))
            tag = release_tag_name(skill, release_instant.astimezone(ZoneInfo("Asia/Shanghai")), existing_tags)
            release_tags[skill] = tag
            existing_tags.add(tag)
    manager_root = root / ".skill-manager"
    manager_root.mkdir(mode=0o700, exist_ok=True)
    transaction = _create_transaction_directory(root, transaction_id)
    entries = [
        {
            "skill": canonical_skill_name(candidate),
            "before_exists": (root / "skills" / str(canonical_skill_name(candidate))).exists(),
            "before_identity": _tree_identity(
                root / "skills" / str(canonical_skill_name(candidate))
            ),
            "after_identity": _tree_identity(candidate.path),
            "package_identity": package_identity(candidate.path),
        }
        for candidate in candidates
    ]
    plan_hash = write_plan(
        transaction / "plan.json",
        {
            "journal_schema_version": 1,
            "transaction_id": transaction_id,
            "operation_type": "promote",
            "skills": entries,
            "release_instant": release_instant.isoformat().replace("+00:00", "Z"),
            "original_head": original_head,
            "original_branch": original_branch,
            "release_tags": release_tags,
            "commit_messages": release_message_metadata or {},
        },
    )
    events = transaction / "events.jsonl"
    append_event(events, plan_hash, "PREPARED")
    staging = transaction / "staging"
    backup = transaction / "backup"
    for candidate in candidates:
        skill = canonical_skill_name(candidate)
        assert skill is not None
        shutil.copytree(candidate.path, staging / skill, symlinks=True)
    _fsync_tree(staging)
    applied: list[str] = []
    try:
        append_event(events, plan_hash, "APPLYING")
        for candidate in candidates:
            skill = canonical_skill_name(candidate)
            assert skill is not None
            stable = root / "skills" / skill
            append_event(events, plan_hash, "PENDING", {"kind": "promotion", "skill": skill})
            if stable.exists():
                os.replace(stable, backup / skill)
                _fsync_directory(backup)
                _fsync_directory(stable.parent)
            os.replace(staging / skill, stable)
            _fsync_directory(stable.parent)
            applied.append(skill)
            append_event(events, plan_hash, "APPLIED", {"kind": "promotion", "skill": skill})
        if release_messages is not None:
            for candidate in candidates:
                skill = str(canonical_skill_name(candidate))
                stable = root / "skills" / skill
                identity = package_identity(stable)
                annotation = (
                    f"Release {skill}\n\n"
                    f"Skill: {skill}\n"
                    f"Package-Identity: {identity}\n"
                    f"Atomic-Batch: {transaction_id}\n"
                    f"Recorded-At: {release_instant.isoformat(timespec='seconds').replace('+00:00', 'Z')}"
                )
                expected_parent = git_output(root, "rev-parse", "HEAD")
                append_event(
                    events,
                    plan_hash,
                    "PENDING",
                    {
                        "kind": "release",
                        "skill": skill,
                        "expected_parent": expected_parent,
                        "tag": release_tags[skill],
                    },
                )
                release_commits[skill] = record_skill_release(
                    root,
                    stable,
                    release_messages[skill],
                    release_tags[skill],
                    annotation,
                )
                append_event(
                    events,
                    plan_hash,
                    "APPLIED",
                    {
                        "kind": "release",
                        "skill": skill,
                        "commit": release_commits[skill],
                        "tag": release_tags[skill],
                    },
                )
        append_event(events, plan_hash, "COMMITTED")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        try:
            append_event(events, plan_hash, "ROLLING_BACK")
        except (OSError, JournalError) as journal_exc:
            return (), (Diagnostic("error", "RECOVERY_REQUIRED", f"cannot journal rollback: {journal_exc}"),)
        if original_head is not None and original_branch is not None:
            try:
                known_release_commits = list(release_commits.values())
                current_head = git_output(root, "rev-parse", f"refs/heads/{original_branch}")
                if current_head not in {original_head, *known_release_commits}:
                    parent = git_output(root, "rev-parse", f"{current_head}^")
                    if parent in {original_head, *known_release_commits}:
                        known_release_commits.append(current_head)
                rollback_git_release(
                    root,
                    original_branch,
                    original_head,
                    tuple(release_tags.values()),
                    transaction_id,
                    tuple(known_release_commits),
                )
            except (OSError, subprocess.CalledProcessError) as rollback_exc:
                return (), (
                    Diagnostic("error", "RECOVERY_REQUIRED", f"Git rollback refused: {rollback_exc}"),
                )
        for skill in reversed(applied):
            stable = root / "skills" / skill
            if stable.exists():
                shutil.rmtree(stable)
            previous = backup / skill
            if previous.exists():
                os.replace(previous, stable)
            append_event(events, plan_hash, "UNDONE", {"kind": "promotion", "skill": skill})
        append_event(events, plan_hash, "ROLLED_BACK")
        _clean_rolled_back_transaction(transaction, plan_hash)
        return (), (Diagnostic("error", "PROMOTION_FAILED", str(exc)),)
    try:
        _write_promotion_receipts(
            root,
            entries,
            transaction_id,
            release_instant.isoformat(timespec="seconds").replace("+00:00", "Z"),
        )
    except OSError as exc:
        return tuple(
            Result(
                "development",
                candidate.name,
                str(candidate.path),
                "promoted",
                {"transaction_id": transaction_id, "test_state": "passed"},
            )
            for candidate in candidates
        ), (Diagnostic("error", "RECOVERY_REQUIRED", f"receipt update failed: {exc}"),)
    cleanup_diagnostics: tuple[Diagnostic, ...] = ()
    try:
        append_event(events, plan_hash, "CLEANING")
        append_event(events, plan_hash, "CLEANED")
        shutil.rmtree(transaction)
        _fsync_directory(transaction.parent)
    except OSError as exc:
        cleanup_diagnostics = (
            Diagnostic("warning", "TRANSACTION_CLEANUP_PENDING", str(exc)),
        )
    return tuple(
        Result(
            "development",
            candidate.name,
            str(candidate.path),
            "promoted",
            {
                "transaction_id": transaction_id,
                "test_state": "passed",
                "package_identity": package_identity(
                    root / "skills" / str(canonical_skill_name(candidate))
                ),
                **(
                    {
                        "release_tag": release_tags[str(canonical_skill_name(candidate))],
                        "release_commit": release_commits[str(canonical_skill_name(candidate))],
                    }
                    if release_messages is not None
                    else {}
                ),
            },
        )
        for candidate in candidates
    ), cleanup_diagnostics


def _write_promotion_receipts(
    root: Path,
    entries: list[dict[str, object]],
    transaction_id: str,
    recorded_at: str,
) -> None:
    manager_root = root / ".skill-manager"
    state_path = manager_root / "state.json"
    receipts: dict[str, object] = {}
    if state_path.is_file():
        try:
            existing_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OSError(f"invalid manager state: {exc}") from exc
        if existing_state.get("schema_version") != 1 or not isinstance(existing_state.get("receipts"), dict):
            raise OSError("invalid or unsupported manager state schema")
        receipts.update(existing_state["receipts"])
    for entry in entries:
        skill = str(entry["skill"])
        receipts[skill] = {
            "identity": package_identity(root / "skills" / skill),
            "transaction_id": transaction_id,
            "recorded_at": recorded_at,
        }
    temporary = manager_root / "state.json.new"
    with temporary.open("w", encoding="utf-8") as output:
        json.dump({"schema_version": 1, "receipts": receipts}, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, state_path)
    _fsync_directory(manager_root)


def _manager_state_diagnostic(root: Path) -> Diagnostic | None:
    state_path = root / ".skill-manager" / "state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Diagnostic("error", "INVALID_MANAGER_STATE", str(exc))
    if not isinstance(state, dict) or state.get("schema_version") != 1 or not isinstance(state.get("receipts"), dict):
        return Diagnostic("error", "UNSUPPORTED_MANAGER_STATE", "invalid or unsupported manager state schema")
    return None


def _recover_result(root: Path, dry_run: bool) -> tuple[tuple[Result, ...], tuple[Diagnostic, ...]]:
    transactions = root / ".skill-manager" / "transactions"
    if not transactions.exists():
        return (), ()
    active = [path for path in transactions.iterdir() if path.is_dir()]
    if len(active) > 1:
        return (), (Diagnostic("error", "JOURNAL_CONFLICT", "multiple active transactions"),)
    if not active:
        return (), ()
    transaction = active[0]
    events = transaction / "events.jsonl"
    try:
        plan, plan_hash = read_plan(transaction / "plan.json")
        events = read_events(events, plan_hash)
    except JournalError as exc:
        return (), (Diagnostic("error", "RECOVERY_REQUIRED", str(exc)),)
    if plan.get("journal_schema_version") != 1:
        return (), (
            Diagnostic("error", "RECOVERY_REQUIRED", "unsupported journal schema version"),
        )
    if any(event["type"] == "COMMITTED" for event in events):
        committed_diagnostic = _committed_state_diagnostic(root, plan)
        if committed_diagnostic is not None:
            return (), (committed_diagnostic,)
        if dry_run:
            return (Result("recovery", transaction.name, str(transaction), "would-clean"),), ()
        if plan.get("operation_type") == "promote" and isinstance(plan.get("skills"), list):
            entries = plan["skills"]
            if not all(isinstance(entry, dict) and isinstance(entry.get("skill"), str) for entry in entries):
                return (), (Diagnostic("error", "RECOVERY_REQUIRED", "promotion journal lacks valid skills"),)
            try:
                recorded_at = plan.get("release_instant")
                if not isinstance(recorded_at, str):
                    return (), (Diagnostic("error", "RECOVERY_REQUIRED", "promotion timestamp missing"),)
                _write_promotion_receipts(root, entries, transaction.name, recorded_at)
            except (OSError, IdentityError) as exc:
                return (), (Diagnostic("error", "RECOVERY_REQUIRED", f"receipt recovery failed: {exc}"),)
        try:
            event_types = [str(event["type"]) for event in events]
            if "CLEANING" not in event_types:
                append_event(transaction / "events.jsonl", plan_hash, "CLEANING")
            if "CLEANED" not in event_types:
                append_event(transaction / "events.jsonl", plan_hash, "CLEANED")
            shutil.rmtree(transaction)
            _fsync_directory(transaction.parent)
        except OSError as exc:
            return (), (Diagnostic("error", "RECOVERY_REQUIRED", f"cleanup failed: {exc}"),)
        return (Result("recovery", transaction.name, str(transaction), "cleaned"),), ()
    if dry_run:
        return (Result("recovery", transaction.name, str(transaction), "would-roll-back"),), ()
    if plan.get("operation_type") in {"install", "uninstall"}:
        operations = plan.get("operations")
        if not isinstance(operations, list) or not all(isinstance(item, dict) for item in operations):
            return (), (Diagnostic("error", "RECOVERY_REQUIRED", "installation journal lacks operations"),)
        rollback_diagnostics = _rollback_installation(transaction, operations, plan_hash)
        if rollback_diagnostics:
            return (), rollback_diagnostics
        _clean_rolled_back_transaction(transaction, plan_hash)
        return (Result("recovery", transaction.name, str(transaction), "rolled-back"),), ()
    if plan.get("operation_type") == "promote" and isinstance(plan.get("skills"), list):
        entries = plan["skills"]
        if not all(isinstance(entry, dict) and isinstance(entry.get("skill"), str) for entry in entries):
            return (), (Diagnostic("error", "RECOVERY_REQUIRED", "promotion journal lacks valid skills"),)
        append_event(transaction / "events.jsonl", plan_hash, "ROLLING_BACK")
        backup_root = transaction / "backup"
        for entry in reversed(entries):
            skill_name = str(entry["skill"])
            stable = root / "skills" / skill_name
            backup = backup_root / skill_name
            before_identity = entry.get("before_identity")
            after_identity = entry.get("after_identity")
            if not isinstance(before_identity, str) or not isinstance(after_identity, str):
                return (), (Diagnostic("error", "RECOVERY_REQUIRED", f"promotion identities missing: {skill_name}"),)
            try:
                actual_identity = _tree_identity(stable)
            except OSError as exc:
                return (), (Diagnostic("error", "RECOVERY_REQUIRED", f"cannot inspect {stable}: {exc}"),)
            if backup.exists():
                if actual_identity not in {"absent", before_identity, after_identity}:
                    return (), (
                        Diagnostic(
                            "error",
                            "RECOVERY_REQUIRED",
                            f"promotion state matches neither planned before nor after: {stable}",
                        ),
                    )
                if actual_identity == before_identity:
                    shutil.rmtree(backup)
                    continue
                if os.path.lexists(stable):
                    shutil.rmtree(stable)
                os.replace(backup, stable)
            elif actual_identity == before_identity:
                continue
            elif before_identity == "absent" and actual_identity in {"absent", after_identity}:
                if os.path.lexists(stable):
                    shutil.rmtree(stable)
            else:
                return (), (
                    Diagnostic(
                        "error",
                        "RECOVERY_REQUIRED",
                        f"promotion state matches neither planned before nor after: {stable}",
                    ),
                )
            append_event(
                transaction / "events.jsonl",
                plan_hash,
                "UNDONE",
                {"kind": "promotion", "skill": skill_name},
            )
        original_head = plan.get("original_head")
        original_branch = plan.get("original_branch")
        release_tags = plan.get("release_tags")
        if isinstance(original_head, str) and isinstance(original_branch, str):
            tags = (
                tuple(str(tag) for tag in release_tags.values())
                if isinstance(release_tags, dict)
                else ()
            )
            release_commit_list = [
                str(details["commit"])
                for event in events
                if event.get("type") == "APPLIED"
                and isinstance((details := event.get("details")), dict)
                and details.get("kind") == "release"
                and isinstance(details.get("commit"), str)
            ]
            applied_release_skills = {
                str(details["skill"])
                for event in events
                if event.get("type") == "APPLIED"
                and isinstance((details := event.get("details")), dict)
                and details.get("kind") == "release"
                and isinstance(details.get("skill"), str)
            }
            current_head = git_output(root, "rev-parse", f"refs/heads/{original_branch}")
            for event in events:
                details = event.get("details")
                if event.get("type") != "PENDING" or not isinstance(details, dict) or details.get("kind") != "release":
                    continue
                skill = details.get("skill")
                expected_parent = details.get("expected_parent")
                if not isinstance(skill, str) or not isinstance(expected_parent, str) or skill in applied_release_skills:
                    continue
                if current_head != expected_parent:
                    if not is_scoped_release_commit(root, current_head, expected_parent, skill):
                        return (), (Diagnostic("error", "RECOVERY_REQUIRED", "unrecognized Git state after release interruption"),)
                    release_commit_list.append(current_head)
            release_commits = tuple(release_commit_list)
            try:
                rollback_git_release(
                    root,
                    original_branch,
                    original_head,
                    tags,
                    transaction.name,
                    release_commits,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                return (), (Diagnostic("error", "RECOVERY_REQUIRED", str(exc)),)
        append_event(transaction / "events.jsonl", plan_hash, "ROLLED_BACK")
        _clean_rolled_back_transaction(transaction, plan_hash)
        return (Result("recovery", transaction.name, str(transaction), "rolled-back"),), ()
    skill = plan.get("skill")
    if not isinstance(skill, str):
        return (), (Diagnostic("error", "RECOVERY_REQUIRED", "journal plan lacks Skill Name"),)
    stable = root / "skills" / skill
    backup = transaction / "backup"
    if backup.exists():
        if stable.exists():
            shutil.rmtree(stable)
        os.replace(backup, stable)
    shutil.rmtree(transaction)
    return (Result("recovery", transaction.name, str(transaction), "rolled-back"),), ()


def _committed_state_diagnostic(root: Path, plan: dict[str, object]) -> Diagnostic | None:
    operation_type = plan.get("operation_type")
    if operation_type in {"install", "uninstall"}:
        operations = plan.get("operations")
        if not isinstance(operations, list):
            return Diagnostic("error", "RECOVERY_REQUIRED", "installation journal lacks operations")
        for operation in operations:
            if not isinstance(operation, dict):
                return Diagnostic("error", "RECOVERY_REQUIRED", "installation operation is invalid")
            destination = operation.get("destination")
            if not isinstance(destination, str) or _link_state(Path(destination)) != operation.get("after"):
                return Diagnostic("error", "RECOVERY_REQUIRED", f"committed installation state drifted: {destination}")
    elif operation_type == "promote":
        entries = plan.get("skills")
        if not isinstance(entries, list):
            return Diagnostic("error", "RECOVERY_REQUIRED", "promotion journal lacks skills")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("skill"), str):
                return Diagnostic("error", "RECOVERY_REQUIRED", "promotion entry is invalid")
            stable = root / "skills" / str(entry["skill"])
            if _tree_identity(stable) != entry.get("after_identity"):
                return Diagnostic("error", "RECOVERY_REQUIRED", f"committed Promotion state drifted: {stable}")
    return None


def _active_transaction_diagnostic(root: Path) -> Diagnostic | None:
    transactions = root / ".skill-manager" / "transactions"
    if not transactions.is_dir():
        return None
    active = [path for path in transactions.iterdir() if path.is_dir()]
    if len(active) > 1:
        return Diagnostic("error", "JOURNAL_CONFLICT", "multiple active transactions")
    if active:
        return Diagnostic(
            "error",
            "RECOVERY_REQUIRED",
            f"unfinished transaction requires recovery: {active[0].name}",
        )
    return None


def _recovery_target_roots(root: Path) -> tuple[Path, ...]:
    transactions = root / ".skill-manager" / "transactions"
    if not transactions.is_dir():
        return ()
    active = [path for path in transactions.iterdir() if path.is_dir()]
    if len(active) != 1:
        return ()
    try:
        plan, _ = read_plan(active[0] / "plan.json")
    except JournalError:
        return ()
    operations = plan.get("operations")
    if not isinstance(operations, list):
        return ()
    roots = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        skills_root = operation.get("skills_root")
        if isinstance(skills_root, str):
            roots.append(Path(skills_root).parent)
    return tuple(roots)


def _validate_results(
    candidates: tuple[Candidate, ...],
    skill_name: str | None,
    skill_names: tuple[str, ...],
    validate_stable: bool,
    validate_all: bool,
    repository_root: Path,
) -> tuple[tuple[Result, ...], tuple[Diagnostic, ...]]:
    source = CandidateSource.STABLE if validate_stable else CandidateSource.DEVELOPMENT
    requested = set(skill_names or ((skill_name,) if skill_name else ()))
    selected = [
        candidate
        for candidate in candidates
        if candidate.source is source
        and (
            validate_all
            or any(_matches_validation_selection(candidate, requested_name) for requested_name in requested)
        )
    ]
    selected.sort(key=lambda candidate: candidate.name)
    if not selected:
        return (), (
            Diagnostic(
                severity="error",
                code="VALIDATION_SOURCE_MISSING",
                message=f"{source.value} candidate not found: {', '.join(sorted(requested)) or 'all'}",
            ),
        )
    results: list[Result] = []
    diagnostics: list[Diagnostic] = []
    for candidate in selected:
        candidate_diagnostics = validate_candidate(candidate, repository_root)
        diagnostics.extend(candidate_diagnostics)
        test_details: dict[str, str] = {"test_state": "not-run"}
        test_diagnostics: tuple[Diagnostic, ...] = ()
        if not candidate_diagnostics:
            test_result = run_skill_test(candidate, repository_root)
            test_details = test_result.details
            test_diagnostics = test_result.diagnostics
            diagnostics.extend(test_diagnostics)
        results.append(
            Result(
                source=candidate.source.value,
                skill=candidate.name,
                path=str(candidate.path),
                outcome="valid" if not candidate_diagnostics and not test_diagnostics else "invalid",
                details={
                    "validation_state": "valid" if not candidate_diagnostics else "invalid",
                    **test_details,
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
