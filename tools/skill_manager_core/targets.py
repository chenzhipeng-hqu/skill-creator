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
        if os.path.lexists(self.skills_root) and (
            self.skills_root.is_symlink() or not self.skills_root.is_dir()
        ):
            return "target-root-conflict"
        destination = self.skills_root / stable.name
        if os.path.lexists(destination):
            return "no-change" if is_exact_managed_link(destination, stable) else "conflict"
        self.skills_root.mkdir(mode=0o755, exist_ok=True)
        destination.symlink_to(stable)
        return "installed"

    def uninstall(self, stable: Path) -> str:
        """Remove only an exact managed link, preserving every other entry."""

        destination = self.skills_root / stable.name
        if os.path.lexists(self.skills_root) and (
            self.skills_root.is_symlink() or not self.skills_root.is_dir()
        ):
            return "target-root-conflict"
        if not os.path.lexists(destination):
            return "no-change"
        if not destination.is_symlink():
            return "conflict"
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


def hermes_target(home: Path, active_home: Path | None = None) -> AgentTarget:
    """Resolve only the active Hermes home, never a Hub registry."""

    return AgentTarget("hermes", active_home or home / ".hermes")


def opencode_target(
    home: Path,
    config_home: Path | None = None,
    config_dir: Path | None = None,
) -> AgentTarget:
    """Resolve the native OpenCode configuration root."""

    return AgentTarget(
        "opencode",
        config_dir or (config_home or home / ".config") / "opencode",
    )


def opencode_discovery_roots(
    home: Path,
    config_home: Path | None = None,
    config_dir: Path | None = None,
) -> tuple[Path, ...]:
    """Return native plus compatibility roots used for duplicate-exposure preflight."""

    native = opencode_target(home, config_home, config_dir).skills_root
    return native, home / ".agents" / "skills", home / ".claude" / "skills"


def is_exact_managed_link(destination: Path, stable: Path) -> bool:
    """Return true only for a direct absolute link to this Stable Skill."""

    if not destination.is_symlink():
        return False
    try:
        return destination.readlink().is_absolute() and destination.readlink() == stable
    except OSError:
        return False


def apply_installation(target: AgentTarget, stable: Path, *, uninstall: bool = False, dry_run: bool = False) -> str:
    """Plan-equivalent one-link operation used by CLI and TUI adapters."""

    if not target.detected():
        return "target-not-detected"
    if os.path.lexists(target.skills_root) and (
        target.skills_root.is_symlink() or not target.skills_root.is_dir()
    ):
        return "target-root-conflict"
    destination = target.skills_root / stable.name
    if dry_run:
        if uninstall:
            if is_exact_managed_link(destination, stable):
                return "would-uninstall"
            return "conflict" if os.path.lexists(destination) else "no-change"
        if os.path.lexists(destination):
            return "no-change" if is_exact_managed_link(destination, stable) else "conflict"
        return "would-install"
    return target.uninstall(stable) if uninstall else target.install(stable)
