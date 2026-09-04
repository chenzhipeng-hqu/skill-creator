"""Cooperating-manager repository locks."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path


class ManagerBusyError(RuntimeError):
    """A cooperating manager already holds the repository lock."""


@contextmanager
def exclusive_repository_lock(repository_root: Path):
    """Hold the manager-owned lock without blocking concurrent operations."""

    manager_root = repository_root / ".skill-manager"
    manager_root.mkdir(mode=0o700, exist_ok=True)
    manager_root.chmod(0o700)
    lock_path = manager_root / "repository.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        lock_path.chmod(0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ManagerBusyError("repository lock is held") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
