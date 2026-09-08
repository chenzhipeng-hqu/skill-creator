"""Local-only Git release naming and preflight helpers."""

from __future__ import annotations

import os
import re
import signal
import subprocess
from datetime import datetime
from pathlib import Path

from .identity import package_identity


TERMINATE_GRACE_SECONDS = 5


def _run_git(
    arguments: list[str],
    repository_root: Path,
    *,
    check: bool = False,
    text: bool = False,
) -> subprocess.CompletedProcess:
    """Run Git and its hooks in a process group with bounded signal escalation."""

    process = subprocess.Popen(
        ["git", *arguments],
        cwd=repository_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate()
    except (InterruptedError, KeyboardInterrupt):
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=TERMINATE_GRACE_SECONDS)
        except (subprocess.TimeoutExpired, InterruptedError, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise InterruptedError("Git operation interrupted")
    completed = subprocess.CompletedProcess(
        ["git", *arguments], process.returncode, stdout, stderr
    )
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def release_tag_name(skill: str, instant: datetime, existing_tags: set[str]) -> str:
    """Return the next immutable ``skill-YYMMDD-vN`` name without reusing holes."""

    prefix = f"{skill}-{instant.strftime('%y%m%d')}-v"
    versions = [int(match.group(1)) for tag in existing_tags if (match := re.fullmatch(re.escape(prefix) + r"(\d+)", tag))]
    return f"{prefix}{max(versions, default=0) + 1}"


def tag_is_available(tag: str, existing_tags: set[str]) -> bool:
    """Existing release tags are immutable and cannot be replaced."""

    return tag not in existing_tags


def release_path_has_changes(repository_root: Path, stable_path: Path) -> bool:
    """Return whether a scoped release commit would contain a path change."""

    relative_path = stable_path.relative_to(repository_root)
    status = _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all", "--", str(relative_path)],
        repository_root,
        check=True,
        text=True,
    )
    return bool(status.stdout.strip())


def release_for_receipt(
    repository_root: Path,
    skill: str,
    identity: str,
    promotion_transaction: str,
) -> tuple[str, str] | None:
    """Find an immutable release tag already bound to one Promotion Receipt."""

    tags = _run_git(
        ["tag", "--list", f"{skill}-*", "--sort=-version:refname"],
        repository_root,
        check=True,
        text=True,
    ).stdout.splitlines()
    for tag in tags:
        annotation = _run_git(
            ["for-each-ref", f"refs/tags/{tag}", "--format=%(contents)"],
            repository_root,
            check=True,
            text=True,
        ).stdout
        if f"Package-Identity: {identity}" not in annotation:
            continue
        if (
            f"Atomic-Batch: {promotion_transaction}" not in annotation
            and f"Promotion-Transaction: {promotion_transaction}" not in annotation
        ):
            continue
        commit = _run_git(
            ["rev-list", "-n", "1", tag],
            repository_root,
            check=True,
            text=True,
        ).stdout.strip()
        relative_path = f"skills/{skill}"
        tagged_diff = _run_git(
            ["diff", "--quiet", tag, "--", relative_path],
            repository_root,
        )
        untracked = _run_git(
            ["ls-files", "--others", "--exclude-standard", "--", relative_path],
            repository_root,
            check=True,
            text=True,
        ).stdout
        if tagged_diff.returncode != 0 or untracked.strip():
            continue
        return tag, commit
    return None


def read_commit_message(path: Path) -> str:
    """Validate a caller-approved release commit message file."""

    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"invalid commit message file: {exc}") from exc
    if not value or "\x00" in value:
        raise ValueError("commit message must be non-empty UTF-8 without NUL")
    return value


def git_release_preflight(repository_root: Path) -> str | None:
    """Return a stable failure reason; never mutates refs or the worktree."""

    head = _run_git(
        ["rev-parse", "--verify", "HEAD"],
        repository_root,
        text=True,
    )
    if head.returncode != 0:
        return "GIT_HEAD_MISSING"
    branch = _run_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"], repository_root, text=True
    )
    if branch.returncode != 0:
        return "GIT_HEAD_NOT_BRANCH"
    index = _run_git(["diff", "--cached", "--quiet"], repository_root)
    if index.returncode != 0:
        return "GIT_INDEX_DIRTY"
    for key in ("user.name", "user.email"):
        identity = _run_git(
            ["config", "--get", key],
            repository_root,
            text=True,
        )
        if identity.returncode != 0 or not identity.stdout.strip():
            return "GIT_IDENTITY_MISSING"
    git_directory = repository_root / ".git"
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply"):
        if (git_directory / marker).exists():
            return "GIT_OPERATION_IN_PROGRESS"
    post_commit = git_directory / "hooks" / "post-commit"
    if post_commit.is_file() and post_commit.stat().st_mode & 0o111:
        return "GIT_POST_COMMIT_HOOK_UNSUPPORTED"
    return None


def record_skill_release(repository_root: Path, stable_path: Path, message: str, tag: str, annotation: str) -> str:
    """Create one scoped local commit and immutable annotated tag; never fetches/pushes."""

    failure = git_release_preflight(repository_root)
    if failure is not None:
        raise ValueError(failure)
    relative_path = stable_path.relative_to(repository_root)
    expected_identity = package_identity(stable_path)
    _run_git(
        ["add", "--", str(relative_path)],
        repository_root,
        check=True,
    )
    _run_git(
        ["commit", "-m", message, "--", str(relative_path)],
        repository_root,
        check=True,
    )
    clean_index = _run_git(
        ["diff", "--cached", "--quiet"], repository_root
    )
    committed_matches_worktree = _run_git(
        ["diff", "--quiet", "HEAD", "--", str(relative_path)],
        repository_root,
    )
    changed_paths = _run_git(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        repository_root,
        check=True,
        text=True,
    ).stdout.splitlines()
    prefix = f"{relative_path.as_posix()}/"
    if (
        clean_index.returncode != 0
        or committed_matches_worktree.returncode != 0
        or package_identity(stable_path) != expected_identity
        or not changed_paths
        or any(path != relative_path.as_posix() and not path.startswith(prefix) for path in changed_paths)
    ):
        raise ValueError("GIT_HOOK_MUTATED_RELEASE_SCOPE")
    _run_git(
        ["tag", "-a", tag, "-m", annotation],
        repository_root,
        check=True,
    )
    return _run_git(["rev-parse", "HEAD"], repository_root, check=True, text=True).stdout.strip()


def git_output(repository_root: Path, *arguments: str) -> str:
    return _run_git(list(arguments), repository_root, check=True, text=True).stdout.strip()


def rollback_git_release(
    repository_root: Path,
    branch: str,
    head: str,
    tags: tuple[str, ...],
    transaction_id: str,
    release_commits: tuple[str, ...],
) -> None:
    """CAS-restore a release branch and delete only this transaction's tags."""

    for tag in tags:
        annotation = _run_git(
            ["for-each-ref", f"refs/tags/{tag}", "--format=%(contents)"],
            repository_root,
            text=True,
        ).stdout
        if f"Atomic-Batch: {transaction_id}" in annotation:
            _run_git(["tag", "-d", tag], repository_root)
    current = git_output(repository_root, "rev-parse", f"refs/heads/{branch}")
    if current not in {head, *release_commits}:
        raise OSError(f"branch changed outside transaction: {branch}")
    if current == head:
        staged_release = _run_git(
            ["diff", "--cached", "--quiet", "--", "skills"],
            repository_root,
        )
        if staged_release.returncode == 0:
            return
        _run_git(
            ["reset", "--mixed", head, "--", "skills"],
            repository_root,
            check=True,
        )
        return
    _run_git(
        ["update-ref", f"refs/heads/{branch}", head, current],
        repository_root,
        check=True,
    )
    _run_git(
        ["reset", "--mixed", head, "--", "skills"],
        repository_root,
        check=True,
    )


def is_scoped_release_commit(
    repository_root: Path,
    commit: str,
    expected_parent: str,
    skill: str,
) -> bool:
    try:
        if git_output(repository_root, "rev-parse", f"{commit}^") != expected_parent:
            return False
        changed_paths = git_output(
            repository_root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        ).splitlines()
    except subprocess.CalledProcessError:
        return False
    prefix = f"skills/{skill}/"
    return bool(changed_paths) and all(
        path == f"skills/{skill}" or path.startswith(prefix)
        for path in changed_paths
    )
