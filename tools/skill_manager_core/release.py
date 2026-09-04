"""Local-only Git release naming and preflight helpers."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path


def release_tag_name(skill: str, instant: datetime, existing_tags: set[str]) -> str:
    """Return the next immutable ``skill-YYMMDD-vN`` name without reusing holes."""

    prefix = f"{skill}-{instant.strftime('%y%m%d')}-v"
    versions = [int(match.group(1)) for tag in existing_tags if (match := re.fullmatch(re.escape(prefix) + r"(\d+)", tag))]
    return f"{prefix}{max(versions, default=0) + 1}"


def tag_is_available(tag: str, existing_tags: set[str]) -> bool:
    """Existing release tags are immutable and cannot be replaced."""

    return tag not in existing_tags


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

    branch = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repository_root, text=True, capture_output=True
    )
    if branch.returncode != 0:
        return "GIT_HEAD_NOT_BRANCH"
    index = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repository_root)
    if index.returncode != 0:
        return "GIT_INDEX_DIRTY"
    return None


def record_skill_release(repository_root: Path, stable_path: Path, message: str, tag: str, annotation: str) -> str:
    """Create one scoped local commit and immutable annotated tag; never fetches/pushes."""

    failure = git_release_preflight(repository_root)
    if failure is not None:
        raise ValueError(failure)
    subprocess.run(["git", "add", "--", str(stable_path)], cwd=repository_root, check=True)
    subprocess.run(["git", "commit", "-m", message, "--", str(stable_path)], cwd=repository_root, check=True)
    subprocess.run(["git", "tag", "-a", tag, "-m", annotation], cwd=repository_root, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository_root, check=True, text=True, capture_output=True).stdout.strip()
