"""Safe execution and private logging for Skill Test Entrypoints."""

from __future__ import annotations

import hashlib
import os
import signal
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .identity import IdentityError, canonical_skill_name, package_identity
from .model import Candidate, Diagnostic


MAX_INLINE_BYTES = 64 * 1024
MAX_LOG_BYTES = 100 * 1024 * 1024
MAX_LOG_AGE_SECONDS = 30 * 24 * 60 * 60
TEST_TIMEOUT_SECONDS = 300
TERMINATE_GRACE_SECONDS = 5


@dataclass(frozen=True)
class SkillTestResult:
    details: dict[str, str]
    diagnostics: tuple[Diagnostic, ...]


def run_skill_test(candidate: Candidate, repository_root: Path) -> SkillTestResult:
    """Run one entrypoint directly, retaining complete output under manager state."""

    skill_name = canonical_skill_name(candidate)
    assert skill_name is not None
    entrypoint = repository_root / "tests" / skill_name / "run"
    if not entrypoint.is_file() or not os.access(entrypoint, os.X_OK):
        return SkillTestResult({"test_state": "not-applicable"}, ())
    before = package_identity(candidate.path)
    environment = os.environ | {
        "SKILL_NAME": skill_name,
        "SKILL_UNDER_TEST": str(candidate.path),
        "SKILL_SOURCE_KIND": candidate.source.value,
        "SKILL_REPO_ROOT": str(repository_root),
    }
    try:
        process = subprocess.Popen(
            [str(entrypoint)], cwd=repository_root, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError as exc:
        details = _write_logs(repository_root, skill_name, b"", str(exc).encode("utf-8"))
        return _finished(repository_root, skill_name, details, "failed", (_error("SKILL_TEST_EXECUTION_ERROR", str(exc)),))
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=TEST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
    details = _write_logs(repository_root, skill_name, stdout, stderr)
    diagnostics: list[Diagnostic] = []
    state = "passed"
    if timed_out:
        state = "timeout"
        diagnostics.append(_error("SKILL_TEST_TIMEOUT", skill_name))
    if process.returncode != 0:
        code = "SKILL_TEST_SIGNAL" if process.returncode < 0 else "SKILL_TEST_FAILED"
        state = "failed"
        details["exit_code"] = str(process.returncode)
        diagnostics.append(_error(code, skill_name))
    try:
        after = package_identity(candidate.path)
    except (IdentityError, OSError) as exc:
        state = "modified"
        diagnostics.append(_error("SKILL_TEST_IDENTITY_ERROR", str(exc)))
    else:
        if before != after:
            state = "modified"
            diagnostics.append(_error("SKILL_TEST_MUTATED_CONTENT", skill_name))
    return _finished(repository_root, skill_name, details, state, tuple(diagnostics))


def _write_logs(root: Path, skill: str, stdout: bytes, stderr: bytes) -> dict[str, str]:
    log_root = root / ".skill-manager" / "logs" / str(uuid.uuid4())
    _private_directory(root / ".skill-manager")
    _private_directory(root / ".skill-manager" / "logs")
    log_root.mkdir(mode=0o700, exist_ok=False)
    log_root.chmod(0o700)
    values: dict[str, str] = {}
    for stream, data in (("stdout", stdout), ("stderr", stderr)):
        path = log_root / f"{skill}.{stream}"
        path.write_bytes(data)
        path.chmod(0o600)
        values[f"{stream}_path"] = str(path)
        values[f"{stream}_bytes"] = str(len(data))
        values[f"{stream}_sha256"] = hashlib.sha256(data).hexdigest()
        values[f"{stream}_truncated"] = str(len(data) > MAX_INLINE_BYTES).lower()
        values[f"{stream}_tail"] = data[-MAX_INLINE_BYTES:].decode("utf-8", errors="replace")
    return values


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)


def _finished(root: Path, skill: str, details: dict[str, str], state: str, diagnostics: tuple[Diagnostic, ...]) -> SkillTestResult:
    run_root = Path(details["stdout_path"]).parent
    if state != "passed":
        (run_root / ".failed").write_text(skill, encoding="utf-8")
    _prune_logs(root, run_root)
    return SkillTestResult({**details, "test_state": state}, diagnostics)


def _prune_logs(root: Path, current: Path) -> None:
    """Bound manager-owned logs while preserving each Skill's latest failure."""
    logs = root / ".skill-manager" / "logs"
    runs = [path for path in logs.iterdir() if path.is_dir()]
    latest_failed: dict[str, Path] = {}
    for run in runs:
        marker = run / ".failed"
        if marker.is_file():
            skill = marker.read_text(encoding="utf-8")
            if skill not in latest_failed or run.stat().st_mtime > latest_failed[skill].stat().st_mtime:
                latest_failed[skill] = run
    protected = {current, *latest_failed.values()}
    now = time.time()
    for run in runs:
        if run not in protected and now - run.stat().st_mtime > MAX_LOG_AGE_SECONDS:
            shutil.rmtree(run)
    runs = [path for path in logs.iterdir() if path.is_dir()]
    total = sum(sum(file.stat().st_size for file in run.rglob("*") if file.is_file()) for run in runs)
    for run in sorted(runs, key=lambda path: path.stat().st_mtime):
        if total <= MAX_LOG_BYTES:
            break
        if run in protected:
            continue
        size = sum(file.stat().st_size for file in run.rglob("*") if file.is_file())
        shutil.rmtree(run)
        total -= size


def _error(code: str, message: str) -> Diagnostic:
    return Diagnostic(severity="error", code=code, message=message)
