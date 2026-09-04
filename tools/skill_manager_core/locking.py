"""Cooperating-manager repository locks."""

from __future__ import annotations

import fcntl
from contextlib import ExitStack, contextmanager
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


@contextmanager
def exclusive_target_locks(config_roots: tuple[Path, ...]):
    """Hold selected Agent Target locks in deterministic path order."""

    with ExitStack() as stack:
        locks = []
        for config_root in sorted(set(config_roots), key=lambda path: str(path)):
            lock_path = config_root / ".skill-manager.lock"
            lock = stack.enter_context(lock_path.open("a+", encoding="utf-8"))
            lock_path.chmod(0o600)
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ManagerBusyError(f"target lock is held: {config_root}") from exc
            locks.append(lock)
        try:
            yield
        finally:
            for lock in reversed(locks):
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def shared_existing_locks(lock_paths: tuple[Path, ...]):
    """Share existing manager locks without creating a dry-run side effect."""

    with ExitStack() as stack:
        locks = []
        for lock_path in sorted(set(lock_paths), key=lambda path: str(path)):
            if not lock_path.is_file():
                continue
            lock = stack.enter_context(lock_path.open("r", encoding="utf-8"))
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ManagerBusyError(f"manager lock is held: {lock_path}") from exc
            locks.append(lock)
        try:
            yield
        finally:
            for lock in reversed(locks):
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
