"""Deterministic structural validation for one Skill candidate."""

from __future__ import annotations

import stat
import re
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
import yaml

from .identity import IdentityError, canonical_skill_name, package_manifest
from .model import Candidate, Diagnostic


def validate_candidate(
    candidate: Candidate,
    repository_root: Path | None = None,
) -> tuple[Diagnostic, ...]:
    """Return structural-validation diagnostics without running Skill tests."""

    diagnostics: list[Diagnostic] = []
    skill_name = canonical_skill_name(candidate)
    if skill_name is None:
        diagnostics.append(_error("INVALID_SKILL_NAME", f"invalid candidate name: {candidate.name}"))
        return tuple(diagnostics)
    if candidate.entry_kind != "directory":
        diagnostics.append(_error("CANDIDATE_NOT_DIRECTORY", f"candidate is not a directory: {candidate.path}"))
        return tuple(diagnostics)

    skill_file = candidate.path / "SKILL.md"
    metadata_file = candidate.path / "agents" / "openai.yaml"
    if not _is_regular_file(skill_file):
        diagnostics.append(_error("MISSING_SKILL_MD", f"missing regular SKILL.md: {candidate.path}"))
    if not _is_regular_file(metadata_file):
        diagnostics.append(
            _error("MISSING_OPENAI_METADATA", f"missing regular agents/openai.yaml: {candidate.path}")
        )
    if diagnostics:
        return tuple(diagnostics)

    try:
        frontmatter = _frontmatter(skill_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return (_error("INVALID_SKILL_MD", f"cannot read SKILL.md: {exc}"),)
    if frontmatter is None:
        return (_error("INVALID_SKILL_MD", "SKILL.md requires YAML frontmatter"),)
    if frontmatter.get("name") != skill_name:
        diagnostics.append(_error("SKILL_NAME_MISMATCH", "SKILL.md name does not match directory mapping"))
    if not isinstance(frontmatter.get("description"), str) or not frontmatter["description"].strip():
        diagnostics.append(_error("MISSING_SKILL_DESCRIPTION", "SKILL.md requires non-empty description"))

    try:
        metadata = yaml.safe_load(metadata_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        diagnostics.append(_error("INVALID_OPENAI_METADATA", f"cannot parse agents/openai.yaml: {exc}"))
        return tuple(diagnostics)
    interface = metadata.get("interface") if isinstance(metadata, dict) else None
    if not isinstance(interface, dict):
        diagnostics.append(_error("INVALID_OPENAI_METADATA", "metadata requires interface mapping"))
    else:
        for field in ("display_name", "short_description"):
            if not isinstance(interface.get(field), str) or not interface[field].strip():
                diagnostics.append(_error("INVALID_OPENAI_METADATA", f"interface requires non-empty {field}"))
    if isinstance(metadata, dict):
        _validate_optional_metadata(metadata, candidate.path, diagnostics)
    diagnostics.extend(_validate_managed_files(candidate.path))
    diagnostics.extend(_validate_skill_manager_manifest(candidate.path))
    try:
        package_manifest(candidate.path)
    except (IdentityError, OSError) as exc:
        if "path" in str(exc):
            diagnostics.append(_error("INVALID_MANAGED_PATH", str(exc)))
    if repository_root is not None and _has_managed_script(candidate.path):
        skill_name = canonical_skill_name(candidate)
        entrypoint = repository_root / "tests" / str(skill_name) / "run"
        if not _is_regular_file(entrypoint) or not bool(entrypoint.lstat().st_mode & 0o111):
            diagnostics.append(
                _error(
                    "MISSING_SKILL_TEST_ENTRYPOINT",
                    f"managed scripts require executable test entrypoint: {entrypoint}",
                )
            )
    diagnostics.extend(_validate_markdown_references(candidate.path))
    return tuple(diagnostics)


def _validate_skill_manager_manifest(skill_root: Path) -> list[Diagnostic]:
    path = skill_root / "skill-manager.yaml"
    if not path.exists():
        return []
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return [_error("HOOK_MANIFEST_INVALID", f"cannot parse skill-manager.yaml: {exc}")]
    hooks = document.get("hooks") if isinstance(document, dict) else None
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(hooks, list):
        return [_error("HOOK_MANIFEST_INVALID", "skill-manager.yaml requires schema_version: 1 and hooks list")]
    diagnostics: list[Diagnostic] = []
    ids: set[str] = set()
    for hook in hooks:
        if not isinstance(hook, dict):
            diagnostics.append(_error("HOOK_MANIFEST_INVALID", "hook entry must be mapping"))
            continue
        identifier, script, targets = hook.get("id"), hook.get("script"), hook.get("targets")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            diagnostics.append(_error("HOOK_MANIFEST_INVALID", "hook id must be unique non-empty string"))
        else:
            ids.add(identifier)
        script_path = _lexically_contained_path(skill_root, skill_root, script) if isinstance(script, str) else None
        if script_path is None or not _is_contained_regular_file(skill_root, script_path) or not bool(script_path.lstat().st_mode & 0o111):
            diagnostics.append(_error("HOOK_MANIFEST_INVALID", "hook script must be executable regular file inside Skill"))
        if hook.get("event") != "PreToolUse" or hook.get("activation") != "optional" or not isinstance(targets, dict) or not targets:
            diagnostics.append(_error("HOOK_MANIFEST_INVALID", "hook requires PreToolUse, optional activation, and targets"))
        elif any(agent not in {"codex", "claude"} or not isinstance(value, dict) or not isinstance(value.get("matcher"), str) for agent, value in targets.items()):
            diagnostics.append(_error("HOOK_MANIFEST_INVALID", "hook targets must be supported Agent matcher mappings"))
    return diagnostics


def _validate_markdown_references(skill_root: Path) -> list[Diagnostic]:
    parser = MarkdownIt()
    diagnostics: list[Diagnostic] = []
    for markdown_file in skill_root.rglob("*.md"):
        if markdown_file.is_symlink():
            continue
        try:
            tokens = parser.parse(markdown_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            continue
        for token in _flatten_tokens(tokens):
            if token.type not in {"link_open", "image"}:
                continue
            href = token.attrGet("href") or token.attrGet("src")
            if not href or href.startswith("#"):
                continue
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc:
                continue
            relative = unquote(parsed.path)
            if not relative:
                continue
            target = _lexically_contained_path(skill_root, markdown_file.parent, relative)
            if target is None:
                diagnostics.append(_error("REFERENCE_ESCAPES_SKILL", f"reference escapes Skill: {href}"))
                continue
            if not _is_contained_regular_file(skill_root, target):
                diagnostics.append(_error("MISSING_LOCAL_REFERENCE", f"missing local reference: {href}"))
    return diagnostics


def _flatten_tokens(tokens):
    for token in tokens:
        yield token
        if token.children:
            yield from _flatten_tokens(token.children)


def _validate_optional_metadata(
    metadata: dict,
    skill_root: Path,
    diagnostics: list[Diagnostic],
) -> None:
    known = {"interface", "icon_small", "icon_large", "brand_color", "default_prompt", "policy", "dependencies"}
    for field in metadata:
        if field not in known:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    code="UNKNOWN_OPENAI_METADATA_FIELD",
                    message=f"unknown metadata field: {field}",
                )
            )
    color = metadata.get("brand_color")
    if color is not None and (
        not isinstance(color, str) or re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None
    ):
        diagnostics.append(_error("INVALID_OPENAI_METADATA", "brand_color must match #RRGGBB"))
    prompt = metadata.get("default_prompt")
    if prompt is not None and (not isinstance(prompt, str) or not prompt.strip()):
        diagnostics.append(_error("INVALID_OPENAI_METADATA", "default_prompt must be non-empty string"))
    policy = metadata.get("policy")
    if policy is not None:
        if not isinstance(policy, dict) or (
            "allow_implicit_invocation" in policy
            and not isinstance(policy["allow_implicit_invocation"], bool)
        ):
            diagnostics.append(_error("INVALID_OPENAI_METADATA", "policy has invalid type"))
    for field in ("icon_small", "icon_large"):
        icon = metadata.get(field)
        if icon is None:
            continue
        if not isinstance(icon, str) or not icon:
            diagnostics.append(_error("INVALID_OPENAI_METADATA", f"{field} must be a path string"))
            continue
        target = _lexically_contained_path(skill_root, skill_root, icon)
        if target is None:
            diagnostics.append(_error("INVALID_OPENAI_METADATA", f"{field} escapes Skill"))
        else:
            if not _is_contained_regular_file(skill_root, target):
                diagnostics.append(_error("INVALID_OPENAI_METADATA", f"{field} must name regular file"))
    dependencies = metadata.get("dependencies")
    if dependencies is not None:
        tools = dependencies.get("tools") if isinstance(dependencies, dict) else None
        if not isinstance(tools, list):
            diagnostics.append(_error("INVALID_OPENAI_METADATA", "dependencies.tools must be a list"))
        else:
            for tool in tools:
                if (
                    not isinstance(tool, dict)
                    or tool.get("type") not in {"mcp", "app"}
                    or not isinstance(tool.get("value"), str)
                    or not tool["value"].strip()
                    or "optional" in tool
                ):
                    diagnostics.append(_error("INVALID_OPENAI_METADATA", "invalid dependency tool"))
                    continue
                if any(
                    field in tool and not isinstance(tool[field], str)
                    for field in ("description", "transport", "command", "url")
                ):
                    diagnostics.append(_error("INVALID_OPENAI_METADATA", "invalid dependency tool field"))


def _validate_managed_files(skill_root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for current, directories, files in os.walk(skill_root, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            path = current_path / directory
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                diagnostics.append(_error("SYMBOLIC_LINK_NOT_ALLOWED", f"symbolic link: {path}"))
            elif directory in {".git", ".hg", ".svn"}:
                diagnostics.append(
                    _error("VERSION_CONTROL_METADATA_NOT_ALLOWED", f"version-control metadata: {path}")
                )
        for filename in files:
            path = current_path / filename
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                diagnostics.append(_error("SYMBOLIC_LINK_NOT_ALLOWED", f"symbolic link: {path}"))
                continue
            if filename in {".git", ".hg", ".svn"}:
                diagnostics.append(
                    _error("VERSION_CONTROL_METADATA_NOT_ALLOWED", f"version-control metadata: {path}")
                )
                continue
            if not stat.S_ISREG(mode):
                diagnostics.append(_error("UNSUPPORTED_MANAGED_FILE_TYPE", f"unsupported file type: {path}"))
                continue
            if (
                path.is_relative_to(skill_root / "scripts")
                and bool(mode & 0o111)
            ):
                try:
                    first_line = path.read_text(encoding="utf-8").splitlines()[0]
                except (OSError, UnicodeError, IndexError):
                    first_line = ""
                if not first_line.startswith("#!"):
                    diagnostics.append(
                        _error("INVALID_SCRIPT_SHEBANG", f"executable script needs shebang: {path}")
                    )
    return diagnostics


def _has_managed_script(skill_root: Path) -> bool:
    scripts = skill_root / "scripts"
    if not scripts.is_dir() or scripts.is_symlink():
        return False
    return any(path.is_file() and not path.is_symlink() for path in scripts.rglob("*"))


def _frontmatter(text: str) -> dict[str, object] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    value = yaml.safe_load(text[4:end])
    return value if isinstance(value, dict) else None


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _lexically_contained_path(skill_root: Path, base: Path, relative: str) -> Path | None:
    """Resolve ``relative`` lexically, never through a symbolic link."""

    if "\x00" in relative:
        return None
    root = Path(os.path.normpath(os.fspath(skill_root)))
    target = Path(os.path.normpath(os.fspath(base / relative)))
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _is_contained_regular_file(skill_root: Path, target: Path) -> bool:
    """Check a local file without traversing any symbolic-link component."""

    try:
        relative = target.relative_to(skill_root)
        current = skill_root
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            return False
        for segment in relative.parts:
            current /= segment
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                return False
        return stat.S_ISREG(mode)
    except OSError:
        return False


def _error(code: str, message: str) -> Diagnostic:
    return Diagnostic(severity="error", code=code, message=message)
