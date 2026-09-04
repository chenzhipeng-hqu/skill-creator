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


def append_event(path: Path, plan_hash: str, event_type: str) -> None:
    previous = plan_hash
    sequence = 1
    if path.exists():
        events = read_events(path, plan_hash)
        if events:
            previous = events[-1]["event_sha256"]
            sequence = int(events[-1]["sequence"]) + 1
    event = {"sequence": sequence, "prior_sha256": previous, "type": event_type}
    event["event_sha256"] = sha256(event)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        file.flush()
        os.fsync(file.fileno())
    path.chmod(0o600)


def read_events(path: Path, plan_hash: str) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise JournalError(f"invalid events: {exc}") from exc
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
