"""Canonical Package Identity and lifecycle pairing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from pathlib import Path

from .model import Candidate, CandidateSource


SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_IGNORED_DIRECTORY_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
_IGNORED_FILE_NAMES = {".DS_Store", "Thumbs.db", ".coverage"}
_VERSION_CONTROL_DIRECTORY_NAMES = {".git", ".hg", ".svn"}


class IdentityError(ValueError):
    """A package cannot receive a canonical Package Identity."""


def canonical_skill_name(candidate: Candidate) -> str | None:
    """Return the unprefixed canonical Skill Name when candidate mapping is valid."""

    name = candidate.name
    if candidate.source is CandidateSource.DEVELOPMENT:
        if not name.startswith("sk-"):
            return None
        name = name[3:]
    if name.startswith("sk-") or not SKILL_NAME_PATTERN.fullmatch(name):
        return None
    return name


def package_identity(package_root: Path) -> str:
    """Return the SHA-256 identity of managed regular files under package_root."""

    manifest = package_manifest(package_root)
    canonical_manifest = json.dumps(
        [manifest[path] for path in sorted(manifest)],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical_manifest).hexdigest()}"


def package_manifest(
    package_root: Path,
    *,
    allow_nonregular: bool = False,
) -> dict[str, dict[str, object]]:
    """Return managed-file metadata indexed by canonical relative path."""

    root_mode = package_root.lstat().st_mode
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise IdentityError(f"package root is not a real directory: {package_root}")

    manifest: dict[str, dict[str, object]] = {}
    for current_root, directories, files in os.walk(package_root, followlinks=False):
        current = Path(current_root)
        retained_directories: list[str] = []
        for directory in directories:
            path = current / directory
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                if allow_nonregular:
                    _add_nonregular_manifest_entry(manifest, path, package_root, "symlink")
                    continue
                raise IdentityError(f"symbolic link is not managed content: {path}")
            if directory in _VERSION_CONTROL_DIRECTORY_NAMES:
                raise IdentityError(f"version-control metadata is not managed content: {path}")
            if directory in _IGNORED_DIRECTORY_NAMES:
                continue
            retained_directories.append(directory)
        directories[:] = retained_directories

        for filename in files:
            path = current / filename
            if filename in _VERSION_CONTROL_DIRECTORY_NAMES:
                raise IdentityError(f"version-control metadata is not managed content: {path}")
            if _is_ignored_file(filename):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                if allow_nonregular:
                    _add_nonregular_manifest_entry(manifest, path, package_root, "symlink")
                    continue
                raise IdentityError(f"symbolic link is not managed content: {path}")
            if not stat.S_ISREG(mode):
                if allow_nonregular:
                    _add_nonregular_manifest_entry(manifest, path, package_root, "other")
                    continue
                raise IdentityError(f"unsupported managed file type: {path}")
            relative_path = _normalized_relative_path(path.relative_to(package_root))
            data = path.read_bytes()
            _add_manifest_entry(
                manifest,
                relative_path,
                {
                "path": relative_path,
                "file_type": "regular",
                "executable": bool(mode & 0o111),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                },
            )
    return manifest


def _add_nonregular_manifest_entry(
    manifest: dict[str, dict[str, object]],
    path: Path,
    package_root: Path,
    file_type: str,
) -> None:
    relative_path = _normalized_relative_path(path.relative_to(package_root))
    _add_manifest_entry(
        manifest,
        relative_path,
        {
            "path": relative_path,
            "file_type": file_type,
            "executable": False,
            "size": 0,
            "sha256": "",
        },
    )


def _add_manifest_entry(
    manifest: dict[str, dict[str, object]],
    relative_path: str,
    entry: dict[str, object],
) -> None:
    if relative_path in manifest:
        raise IdentityError(f"paths collide after NFC normalization: {relative_path}")
    manifest[relative_path] = entry


def _normalized_relative_path(relative_path: Path) -> str:
    normalized_segments: list[str] = []
    for segment in relative_path.parts:
        normalized = unicodedata.normalize("NFC", segment)
        if segment != normalized:
            raise IdentityError(f"path is not NFC normalized: {relative_path}")
        try:
            normalized.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise IdentityError(f"path is not UTF-8 representable: {relative_path}") from exc
        if (
            not normalized
            or normalized in {".", ".."}
            or "\\" in normalized
            or any(unicodedata.category(character) == "Cc" for character in normalized)
        ):
            raise IdentityError(f"path is not portable: {relative_path}")
        normalized_segments.append(normalized)
    return "/".join(normalized_segments)


def _is_ignored_file(filename: str) -> bool:
    return (
        filename in _IGNORED_FILE_NAMES
        or filename.endswith((".pyc", ".pyo", ".swp", ".swo"))
        or filename.endswith("~")
    )
