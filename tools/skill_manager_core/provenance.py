"""Stable Promotion Receipt and current-provenance classification."""

from __future__ import annotations

from pathlib import Path
import json

from .identity import IdentityError, package_identity


def stable_provenance(repository_root: Path, skill: str, stable: Path) -> str:
    """Classify current Stable bytes against the latest local receipt."""

    state_path = repository_root / ".skill-manager" / "state.json"
    try:
        receipt = json.loads(state_path.read_text(encoding="utf-8"))["receipts"][skill]
        recorded = receipt["identity"]
        current = package_identity(stable)
    except (OSError, ValueError, KeyError, IdentityError):
        return "unknown"
    return "clean" if current == recorded else "drifted"
