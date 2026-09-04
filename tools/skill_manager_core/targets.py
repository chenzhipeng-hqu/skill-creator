"""Base local Agent Target contracts for managed Stable Skill links."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentTarget:
    key: str
    config_root: Path

    @property
    def skills_root(self) -> Path:
        return self.config_root / "skills"

    def detected(self) -> bool:
        try:
            return self.config_root.is_dir() and not self.config_root.is_symlink()
        except OSError:
            return False

    def install(self, stable: Path) -> str:
        """Create one exact absolute managed link without overwriting conflicts."""

        if not self.detected():
            return "target-not-detected"
        destination = self.skills_root / stable.name
        if os.path.lexists(destination):
            return "no-change" if is_exact_managed_link(destination, stable) else "conflict"
        self.skills_root.mkdir(mode=0o755, exist_ok=True)
        destination.symlink_to(stable)
        return "installed"

    def uninstall(self, stable: Path) -> str:
        """Remove only an exact managed link, preserving every other entry."""

        destination = self.skills_root / stable.name
        if not destination.is_symlink():
            return "no-change"
        if not is_exact_managed_link(destination, stable):
            return "conflict"
        destination.unlink()
        return "uninstalled"


def base_targets(home: Path) -> dict[str, AgentTarget]:
    return {
        "codex": AgentTarget("codex", home / ".codex"),
        "claude": AgentTarget("claude", home / ".claude"),
        "agents": AgentTarget("agents", home / ".agents"),
    }


def is_exact_managed_link(destination: Path, stable: Path) -> bool:
    """Return true only for a direct absolute link to this Stable Skill."""

    if not destination.is_symlink():
        return False
    try:
        return destination.readlink().is_absolute() and destination.readlink() == stable
    except OSError:
        return False
