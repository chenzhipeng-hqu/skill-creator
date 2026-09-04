"""Linux filesystem capability checks for durable manager mutations."""

from __future__ import annotations

import os
import sys
from pathlib import Path


SUPPORTED_LOCAL_FILESYSTEMS = {"ext4", "xfs", "btrfs", "zfs", "tmpfs", "overlay"}


def mutation_filesystem_error(paths: tuple[Path, ...]) -> str | None:
    """Return a stable reason when mutation durability is unsupported."""

    if sys.platform != "linux":
        return "UNSUPPORTED_PLATFORM"
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError:
        return "UNSUPPORTED_FILESYSTEM"
    for path in paths:
        filesystem = _mount_type(path, mountinfo)
        if filesystem not in SUPPORTED_LOCAL_FILESYSTEMS:
            return "UNSUPPORTED_FILESYSTEM"
    return None


def _mount_type(path: Path, mountinfo: str) -> str | None:
    resolved = Path(os.path.abspath(path))
    best_mount: Path | None = None
    best_type: str | None = None
    for line in mountinfo.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            mount = Path(_unescape_mount_path(fields[4]))
            filesystem = fields[separator + 1]
        except (ValueError, IndexError):
            continue
        try:
            resolved.relative_to(mount)
        except ValueError:
            continue
        if best_mount is None or len(mount.parts) > len(best_mount.parts):
            best_mount = mount
            best_type = filesystem
    return best_type


def _unescape_mount_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )
