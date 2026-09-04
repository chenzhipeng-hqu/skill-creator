"""Immutable transaction plans and hash-chained append-only events."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class JournalError(ValueError):
    """A transaction journal cannot safely be recovered."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def write_plan(path: Path, plan: dict[str, object]) -> str:
    payload = {**plan, "plan_sha256": sha256(plan)}
    with path.open("wb") as file:
        file.write(canonical_bytes(payload))
        file.flush()
        os.fsync(file.fileno())
    path.chmod(0o600)
    _fsync_directory(path.parent)
    return payload["plan_sha256"]


def read_plan(path: Path) -> tuple[dict[str, object], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        stored = payload.pop("plan_sha256")
    except (OSError, ValueError, KeyError) as exc:
        raise JournalError(f"invalid plan: {exc}") from exc
    if not isinstance(stored, str) or stored != sha256(payload):
        raise JournalError("plan hash mismatch")
    return payload, stored


def append_event(
    path: Path,
    plan_hash: str,
    event_type: str,
    details: dict[str, object] | None = None,
) -> None:
    previous = plan_hash
    sequence = 1
    if path.exists():
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            complete_length = raw.rfind(b"\n") + 1
            with path.open("r+b") as file:
                file.truncate(complete_length)
                file.flush()
                os.fsync(file.fileno())
        events = read_events(path, plan_hash)
        if events:
            previous = events[-1]["event_sha256"]
            sequence = int(events[-1]["sequence"]) + 1
    event = {"sequence": sequence, "prior_sha256": previous, "type": event_type}
    if details is not None:
        event["details"] = details
    event["event_sha256"] = sha256(event)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        file.flush()
        os.fsync(file.fileno())
    path.chmod(0o600)
    _fsync_directory(path.parent)


def read_events(path: Path, plan_hash: str) -> list[dict[str, object]]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise JournalError(f"invalid events: {exc}") from exc
    lines = text.splitlines()
    if raw and not raw.endswith(b"\n"):
        lines = lines[:-1]
    events: list[dict[str, object]] = []
    prior = plan_hash
    for sequence, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
            stored = event.pop("event_sha256")
        except (ValueError, KeyError) as exc:
            raise JournalError("invalid event") from exc
        if event.get("sequence") != sequence or event.get("prior_sha256") != prior or stored != sha256(event):
            raise JournalError("event chain mismatch")
        event["event_sha256"] = stored
        events.append(event)
        prior = stored
    return events


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
