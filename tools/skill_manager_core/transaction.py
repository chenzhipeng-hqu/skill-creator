"""Durable filesystem primitives shared by Atomic Batch operations."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
from pathlib import Path

from .identity import IdentityError, package_identity
from .journal import JournalError, append_event
from .model import Diagnostic


def link_state(path: Path) -> dict[str, str]:
    if not os.path.lexists(path):
        return {"kind": "absent"}
    if path.is_symlink():
        return {"kind": "symlink", "target": os.readlink(path)}
    return {"kind": "other"}


def restore_link_state(path: Path, state: object) -> None:
    if not isinstance(state, dict) or state.get("kind") not in {"absent", "symlink"}:
        raise OSError(f"unsupported recorded link state: {path}")
    if os.path.lexists(path):
        if path.is_symlink():
            path.unlink()
        else:
            raise OSError(f"refusing to replace non-link during recovery: {path}")
    if state["kind"] == "symlink":
        path.parent.mkdir(mode=0o755, exist_ok=True)
        path.symlink_to(str(state["target"]))
    if path.parent.exists():
        fsync_directory(path.parent)


def rollback_installation(
    transaction: Path,
    operations: list[dict[str, object]],
    plan_hash: str,
) -> tuple[Diagnostic, ...]:
    events = transaction / "events.jsonl"
    diagnostics: list[Diagnostic] = []
    try:
        append_event(events, plan_hash, "ROLLING_BACK")
        for index in range(len(operations) - 1, -1, -1):
            operation = operations[index]
            destination = Path(str(operation["destination"]))
            current = link_state(destination)
            if current == operation["before"]:
                continue
            if current != operation["after"]:
                raise OSError(f"installation state is neither planned before nor after: {destination}")
            restore_link_state(destination, operation["before"])
            append_event(events, plan_hash, "UNDONE", {"operation": index})
        for operation in reversed(operations):
            skills_root = Path(str(operation["skills_root"]))
            if not operation["skills_root_existed"] and skills_root.is_dir():
                try:
                    skills_root.rmdir()
                    fsync_directory(skills_root.parent)
                except OSError as exc:
                    if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                        raise
        append_event(events, plan_hash, "ROLLED_BACK")
    except (OSError, JournalError) as exc:
        diagnostics.append(Diagnostic("error", "RECOVERY_REQUIRED", str(exc)))
    return tuple(diagnostics)


def clean_rolled_back_transaction(transaction: Path, plan_hash: str) -> None:
    events = transaction / "events.jsonl"
    append_event(events, plan_hash, "CLEANING")
    append_event(events, plan_hash, "CLEANED")
    shutil.rmtree(transaction)
    fsync_directory(transaction.parent)


def create_transaction_directory(root: Path, transaction_id: str) -> Path:
    manager_root = root / ".skill-manager"
    manager_root.mkdir(mode=0o700, exist_ok=True)
    manager_root.chmod(0o700)
    transactions = manager_root / "transactions"
    transactions.mkdir(mode=0o700, exist_ok=True)
    transactions.chmod(0o700)
    transaction = transactions / transaction_id
    transaction.mkdir(mode=0o700)
    transaction.chmod(0o700)
    for name in ("staging", "backup"):
        directory = transaction / name
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    fsync_directory(transactions)
    return transaction


def package_identity_or_unavailable(path: Path) -> str:
    if not os.path.lexists(path):
        return "absent"
    try:
        return package_identity(path)
    except (IdentityError, OSError):
        return "unavailable"


def tree_identity(path: Path) -> str:
    """Hash raw filesystem state without following links for recovery CAS checks."""

    if not os.path.lexists(path):
        return "absent"
    entries: list[dict[str, object]] = []
    root_mode = path.lstat().st_mode
    if not path.is_dir() or path.is_symlink():
        if path.is_symlink():
            entries.append({"path": ".", "kind": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            data = path.read_bytes()
            entries.append({"path": ".", "kind": "file", "sha256": hashlib.sha256(data).hexdigest(), "executable": bool(root_mode & 0o111)})
        else:
            entries.append({"path": ".", "kind": "other", "mode": root_mode})
    else:
        for current_root, directories, files in os.walk(path, followlinks=False):
            current = Path(current_root)
            for name in sorted((*directories, *files)):
                entry = current / name
                relative = entry.relative_to(path).as_posix()
                mode = entry.lstat().st_mode
                if entry.is_symlink():
                    entries.append({"path": relative, "kind": "symlink", "target": os.readlink(entry)})
                    if name in directories:
                        directories.remove(name)
                elif entry.is_dir():
                    entries.append({"path": relative, "kind": "directory"})
                elif entry.is_file():
                    data = entry.read_bytes()
                    entries.append({"path": relative, "kind": "file", "sha256": hashlib.sha256(data).hexdigest(), "executable": bool(mode & 0o111)})
                else:
                    entries.append({"path": relative, "kind": "other", "mode": mode})
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"tree-sha256:{hashlib.sha256(payload).hexdigest()}"


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_tree(root: Path) -> None:
    for current_root, directories, files in os.walk(root, topdown=False):
        current = Path(current_root)
        for filename in files:
            path = current / filename
            if path.is_symlink():
                continue
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        for directory in directories:
            path = current / directory
            if not path.is_symlink():
                fsync_directory(path)
        fsync_directory(current)
