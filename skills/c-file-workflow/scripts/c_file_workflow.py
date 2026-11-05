#!/usr/bin/env python3
"""Create, format, and check self-authored C source and header files."""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
BUILTIN_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    "build",
    "out",
    "dist",
    "third",
    "third_party",
    "third-party",
    "vendor",
    "generated",
    "gen",
}
MAX_DIFF_BYTES = 1024 * 1024
MAX_DIFF_LINES = 10_000


class WorkflowError(Exception):
    """A usage, dependency, analysis, or I/O failure."""


class AnalysisError(WorkflowError):
    """The lightweight scanner cannot analyze the requested structure safely."""


@dataclass(frozen=True)
class SourceFile:
    requested: Path
    path: Path
    data: bytes
    text: str
    newline: str
    bom: bool
    mode: int


@dataclass(frozen=True)
class Finding:
    severity: str
    path: Path
    message: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create, format, and check self-authored C/H files."
    )
    parser.add_argument("inputs", nargs="*", help="bare creation names or existing .c/.h paths")
    parser.add_argument("--plain", action="store_true", help="create a plain C/H pair")
    parser.add_argument("--new", metavar="FILE", help="create one plain .c or .h file")
    parser.add_argument("--output", type=Path, default=Path.cwd(), help="creation output directory")
    parser.add_argument("--author", default="chenzhipeng3472", help="author for newly created files")
    parser.add_argument("--force", action="store_true", help="replace conflicting creation targets")
    parser.add_argument("--check", action="store_true", help="check without modifying files")
    parser.add_argument("--component", action="store_true", help="check Pub/CB/Ops component structure")
    parser.add_argument("--register", action="store_true", help="check register structures")
    parser.add_argument("--recursive", action="store_true", help="scan directory inputs recursively")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    parser.add_argument("--follow-symlinks", action="store_true")
    parser.add_argument("--full", action="store_true", help="do not truncate check diffs")
    parser.add_argument("--style", type=Path, help="clang-format YAML for this invocation")
    parser.add_argument("--clang-format", dest="clang_format", help="clang-format executable path")
    args = parser.parse_args(argv)

    creating = args.new is not None or bool(
        args.inputs and all(_is_creation_name(x) for x in args.inputs)
    )
    if args.new is not None and args.inputs:
        parser.error("--new cannot be combined with positional inputs")
    if not args.new and not args.inputs:
        parser.error("provide a creation name, an existing path, or --new FILE")
    if args.check and (args.plain or args.new is not None or args.force):
        parser.error("--check cannot be combined with --plain, --new, or --force")
    if (args.component or args.register or args.full) and not args.check:
        parser.error("--component, --register, and --full require --check")
    if args.plain and not creating:
        parser.error("--plain applies only to bare-name pair creation")
    if args.recursive and creating:
        parser.error("--recursive applies only to directory processing")
    if args.output != Path.cwd() and not creating:
        parser.error("--output applies only to creation")
    if args.author != "chenzhipeng3472" and not creating:
        parser.error("--author applies only to creation")
    if args.force and not creating:
        parser.error("--force applies only to creation")
    if any(token in args.author for token in ("\n", "\r", "\x00", "*/")):
        parser.error("--author must be safe single-line text")
    return args


def _is_bare_name(value: str) -> bool:
    return NAME_RE.fullmatch(value) is not None


def _is_creation_name(value: str) -> bool:
    path = Path(value).expanduser()
    return _is_bare_name(value) and not path.exists() and not path.is_symlink()


def _symbol_prefix(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _macro_prefix(name: str) -> str:
    return name.upper()


def _asset_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def _default_style() -> Path:
    return _asset_dir() / "clang-format.yaml"


def _resolve_clang_format(value: str | None) -> str:
    executable = value or shutil.which("clang-format")
    if not executable:
        raise WorkflowError(
            "clang-format 17.x was not found; install it or pass --clang-format PATH"
        )
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise WorkflowError(f"cannot execute clang-format: {exc}") from exc
    match = re.search(r"clang-format version\s+(\d+)(?:\.\d+)*", result.stdout + result.stderr)
    if result.returncode != 0 or match is None:
        raise WorkflowError("cannot determine clang-format version")
    if int(match.group(1)) != 17:
        raise WorkflowError(
            f"clang-format major version 17 is required; selected version is {match.group(1)}"
        )
    return executable


def _validate_style(path: Path | None) -> Path:
    style = (path or _default_style()).expanduser().resolve()
    if not style.is_file():
        raise WorkflowError(f"style file does not exist: {style}")
    return style


def _format_text(executable: str, style: Path, text: str, assume: Path) -> str:
    command = [
        executable,
        f"--style=file:{style}",
        f"--assume-filename={assume}",
        "--Werror",
    ]
    try:
        result = subprocess.run(
            command,
            input=text,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise WorkflowError(f"cannot run clang-format: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "clang-format failed"
        raise WorkflowError(f"clang-format failed for {assume}: {detail}")
    return result.stdout.rstrip("\n") + "\n"


def _validate_creation_file(value: str) -> tuple[str, str]:
    path = Path(value)
    if path.name != value or path.suffix not in {".c", ".h"}:
        raise WorkflowError("--new requires one lowercase basename ending in .c or .h")
    stem = path.stem
    if not NAME_RE.fullmatch(stem):
        raise WorkflowError(f"invalid creation name: {stem}")
    return stem, path.suffix


def _render_asset(asset: str, name: str, suffix: str, author: str, pair: bool) -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    symbol = _symbol_prefix(name)
    values = {
        "@FILE@": f"{name}{suffix}",
        "@HEADER@": f"{name}.h",
        "@AUTHOR@": author or "chenzhipeng3472",
        "@DATE@": f"{now.day:02d}-{months[now.month - 1]}-{now.year:04d}",
        "@YEAR@": str(now.year),
        "@SYMBOL@": symbol,
        "@MACRO@": _macro_prefix(name),
        "@PLURAL@": f"{symbol}s",
        "@GUARD@": f"__{_macro_prefix(name)}_H__",
        "@LOCAL_INCLUDE@": f'#include "{name}.h"\n\n' if pair and suffix == ".c" else "",
    }
    template_path = _asset_dir() / asset
    try:
        text = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowError(f"cannot read asset {template_path}: {exc}") from exc
    for token, replacement in values.items():
        text = text.replace(token, replacement)
    leftovers = sorted(set(re.findall(r"@[A-Z_]+@", text)))
    if leftovers:
        raise WorkflowError(f"unresolved template tokens in {asset}: {', '.join(leftovers)}")
    return text


def _created_directories(path: Path) -> list[Path]:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    return missing


def _cleanup_empty_directories(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.rmdir()
        except OSError:
            pass


def _temporary_file(path: Path, data: bytes, mode: int) -> Path:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, mode)
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _atomic_batch_replace(items: list[tuple[Path, bytes, int]]) -> None:
    prepared: list[tuple[Path, Path, bytes | None, int | None]] = []
    replaced: list[tuple[Path, bytes | None, int | None]] = []
    try:
        for path, data, mode in items:
            old_data = path.read_bytes() if path.exists() else None
            old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
            prepared.append((path, _temporary_file(path, data, mode), old_data, old_mode))
        for path, temporary, old_data, old_mode in prepared:
            os.replace(temporary, path)
            replaced.append((path, old_data, old_mode))
    except Exception as exc:
        rollback_failures: list[str] = []
        for path, old_data, old_mode in reversed(replaced):
            restore: Path | None = None
            try:
                if old_data is None:
                    path.unlink(missing_ok=True)
                else:
                    restore = _temporary_file(path, old_data, old_mode or 0o644)
                    os.replace(restore, path)
            except OSError as rollback_exc:
                rollback_failures.append(f"{path}: {rollback_exc}")
            finally:
                if restore is not None:
                    restore.unlink(missing_ok=True)
        if rollback_failures:
            raise WorkflowError(
                "atomic file replacement failed and rollback failed: "
                + "; ".join(rollback_failures)
            ) from exc
        raise WorkflowError(f"atomic file replacement failed: {exc}") from exc
    finally:
        for _, temporary, _, _ in prepared:
            temporary.unlink(missing_ok=True)


def create_files(args: argparse.Namespace, executable: str, style: Path) -> int:
    specifications: list[tuple[str, str, str, bool]] = []
    if args.new is not None:
        name, suffix = _validate_creation_file(args.new)
        specifications.append((name, suffix, f"plain{suffix}", False))
    else:
        for name in args.inputs:
            if not NAME_RE.fullmatch(name):
                raise WorkflowError(f"invalid creation name: {name}")
            source_asset = "plain.c" if args.plain else "template.c"
            header_asset = "plain.h" if args.plain else "template.h"
            specifications.extend(
                [(name, ".c", source_asset, True), (name, ".h", header_asset, True)]
            )

    output = args.output.expanduser().resolve()
    made_dirs: list[Path] = []
    if output.exists() and not output.is_dir():
        raise WorkflowError(f"output is not a directory: {output}")
    if not output.exists():
        made_dirs = _created_directories(output)

    targets: list[tuple[Path, bytes, int, str]] = []
    try:
        seen: set[Path] = set()
        for name, suffix, asset, pair in specifications:
            requested = output / f"{name}{suffix}"
            if requested in seen:
                raise WorkflowError(f"duplicate creation target: {requested}")
            seen.add(requested)
            target = requested
            if requested.is_symlink():
                if not args.follow_symlinks:
                    raise WorkflowError(f"symbolic link is not allowed: {requested}")
                try:
                    target = requested.resolve(strict=True)
                except OSError as exc:
                    raise WorkflowError(f"cannot resolve symbolic link {requested}: {exc}") from exc
            if target.exists():
                if not target.is_file():
                    raise WorkflowError(f"creation target is not a regular file: {requested}")
                if target.stat().st_nlink > 1:
                    raise WorkflowError(f"hard-linked file is not supported: {requested}")
                if not args.force:
                    raise WorkflowError(f"creation target already exists: {requested}")
            rendered = _render_asset(asset, name, suffix, args.author, pair)
            formatted = _format_text(executable, style, rendered, requested)
            targets.append((target, formatted.encode("utf-8"), 0o644, "replaced" if target.exists() else "created"))
        _atomic_batch_replace([(path, data, mode) for path, data, mode, _ in targets])
    except Exception:
        _cleanup_empty_directories(made_dirs)
        raise

    for path, _, _, outcome in targets:
        print(f"{outcome}: {display_path(path)}")
    return EXIT_CLEAN


def _matches_exclude(relative: str, patterns: list[str]) -> bool:
    path = relative.replace(os.sep, "/")
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _skip_directory(name: str) -> bool:
    return name.startswith(".") or name in BUILTIN_EXCLUDES


def _warn_explicit_exclusion(path: Path, patterns: list[str]) -> None:
    if any(_skip_directory(part) for part in path.parts):
        warning(path, "explicitly selected path is under a built-in excluded directory")
    if _matches_exclude(path.as_posix(), patterns):
        warning(path, "explicitly selected path matches --exclude")


def _scan_directory(root: Path, patterns: list[str]) -> list[Path]:
    selected: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        retained: list[str] = []
        for name in dirs:
            candidate = current_path / name
            relative = (relative_current / name).as_posix()
            if candidate.is_symlink() or _skip_directory(name) or _matches_exclude(relative, patterns):
                continue
            retained.append(name)
        dirs[:] = retained
        for name in files:
            path = current_path / name
            if path.suffix not in {".c", ".h"}:
                continue
            relative = (relative_current / name).as_posix()
            if _matches_exclude(relative, patterns):
                continue
            selected.append(path)
    return sorted(selected)


def _expand_inputs(args: argparse.Namespace) -> list[Path]:
    selected: list[Path] = []
    for value in args.inputs:
        path = Path(value).expanduser()
        if not path.exists() and not path.is_symlink():
            raise WorkflowError(f"input does not exist: {path}")
        if path.is_symlink() and path.is_dir() and args.recursive:
            raise WorkflowError(
                f"symbolic-link directory is not supported for recursive input: {path}"
            )
        if path.is_dir():
            if not args.recursive:
                raise WorkflowError(f"directory input requires --recursive: {path}")
            selected.extend(_scan_directory(path, args.exclude))
        else:
            if path.suffix not in {".c", ".h"}:
                raise WorkflowError(f"only lowercase .c and .h files are supported: {path}")
            _warn_explicit_exclusion(path, args.exclude)
            selected.append(path)

    if args.component:
        original = list(selected)
        selected_paths = {path.absolute() for path in selected}
        for path in original:
            mate = path.with_suffix(".h" if path.suffix == ".c" else ".c")
            if (mate.exists() or mate.is_symlink()) and mate.absolute() not in selected_paths:
                selected.append(mate)
                selected_paths.add(mate.absolute())
    return selected


def _load_source(requested: Path, follow_symlinks: bool) -> SourceFile:
    path = requested
    if requested.is_symlink():
        if not follow_symlinks:
            raise WorkflowError(f"symbolic link is not allowed: {requested}")
        try:
            path = requested.resolve(strict=True)
        except OSError as exc:
            raise WorkflowError(f"cannot resolve symbolic link {requested}: {exc}") from exc
    if not path.is_file():
        raise WorkflowError(f"input is not a regular file: {requested}")
    info = path.stat()
    if info.st_nlink > 1:
        raise WorkflowError(f"hard-linked file is not supported: {requested}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise WorkflowError(f"cannot read {requested}: {exc}") from exc
    bom = data.startswith(b"\xef\xbb\xbf")
    body = data[3:] if bom else data
    has_crlf = b"\r\n" in body
    without_crlf = body.replace(b"\r\n", b"")
    if b"\r" in without_crlf or (has_crlf and b"\n" in without_crlf):
        raise WorkflowError(f"mixed newline conventions are not supported: {requested}")
    newline = "\r\n" if has_crlf else "\n"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowError(f"input is not valid UTF-8: {requested}") from exc
    if newline == "\r\n":
        text = text.replace("\r\n", "\n")
    return SourceFile(
        requested=requested,
        path=path,
        data=data,
        text=text,
        newline=newline,
        bom=bom,
        mode=stat.S_IMODE(info.st_mode),
    )


def _load_unique_sources(paths: list[Path], follow_symlinks: bool) -> list[SourceFile]:
    sources: list[SourceFile] = []
    identities: dict[tuple[int, int], SourceFile] = {}
    for requested in paths:
        source = _load_source(requested, follow_symlinks)
        info = source.path.stat()
        identity = (info.st_dev, info.st_ino)
        if identity in identities:
            warning(requested, f"duplicate input aliases {identities[identity].requested}")
            continue
        identities[identity] = source
        sources.append(source)
    return sources


def _encoded_result(source: SourceFile, formatted: str) -> bytes:
    text = formatted.replace("\n", source.newline)
    encoded = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + encoded) if source.bom else encoded


def _strip_noncode(text: str) -> str:
    output = list(text)
    index = 0
    state = "code"
    line_start = True
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if line_start and char in " \t":
                index += 1
                continue
            if line_start and char == "#":
                state = "preprocessor"
                output[index] = " "
            elif char == "/" and nxt == "/":
                state = "line_comment"
                output[index] = output[index + 1] = " "
                index += 1
            elif char == "/" and nxt == "*":
                state = "block_comment"
                output[index] = output[index + 1] = " "
                index += 1
            elif char == '"':
                state = "string"
                output[index] = " "
            elif char == "'":
                state = "character"
                output[index] = " "
        elif state in {"line_comment", "preprocessor"}:
            if char == "\n":
                state = "code"
            else:
                output[index] = " "
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                output[index] = output[index + 1] = " "
                index += 1
                state = "code"
            elif char != "\n":
                output[index] = " "
        elif state in {"string", "character"}:
            delimiter = '"' if state == "string" else "'"
            if char == "\\" and nxt:
                output[index] = output[index + 1] = " "
                index += 1
            elif char == delimiter:
                output[index] = " "
                state = "code"
            elif char == "\n":
                raise AnalysisError("unterminated string or character literal")
            else:
                output[index] = " "
        line_start = char == "\n"
        index += 1
    if state in {"block_comment", "string", "character"}:
        raise AnalysisError("unterminated comment, string, or character literal")
    cleaned = "".join(output)
    if cleaned.count("{") != cleaned.count("}"):
        raise AnalysisError("unbalanced braces")
    return cleaned


def _extract_braced(text: str, pattern: str, start: int = 0) -> tuple[str, int, int] | None:
    match = re.search(pattern, text[start:], re.MULTILINE)
    if match is None:
        return None
    opening = start + match.end() - 1
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index], opening, index
    raise AnalysisError("unbalanced braced declaration")


def _section_positions(raw: str, names: list[str]) -> list[int | None]:
    lower = raw.lower()
    positions: list[int | None] = []
    cursor = 0
    for name in names:
        match = re.search(rf"\b{re.escape(name)}\b", lower[cursor:])
        if match is None:
            positions.append(None)
        else:
            position = cursor + match.start()
            positions.append(position)
            cursor = position + len(name)
    return positions


def _check_sections(path: Path, raw: str, suffix: str) -> list[Finding]:
    names = (
        ["include", "define", "typedef", "function prototypes", "variable", "function"]
        if suffix == ".c"
        else ["include", "define", "typedef", "function prototypes", "inline", "variable"]
    )
    findings: list[Finding] = []
    positions = _section_positions(raw, names)
    for name, position in zip(names, positions):
        if position is None:
            findings.append(Finding("error", path, f"missing '{name}' section"))
    if suffix == ".c":
        for name, position in zip(
            ["ops function", "static function", "public function"],
            _section_positions(raw, ["ops function", "static function", "public function"]),
        ):
            if position is None:
                findings.append(Finding("error", path, f"missing '{name}' subsection"))
    return findings


def _include_findings(path: Path, raw: str) -> list[Finding]:
    includes: list[tuple[int, str]] = []
    for number, line in enumerate(raw.splitlines()):
        match = re.match(r"\s*#\s*include\s*([<\"])", line)
        if match:
            includes.append((number, "system" if match.group(1) == "<" else "local"))
    if not includes:
        return []
    kinds = [kind for _, kind in includes]
    findings: list[Finding] = []
    if "local" in kinds and any(kind == "system" for kind in kinds[kinds.index("local") + 1 :]):
        findings.append(Finding("warning", path, "system includes should precede local includes"))
    system_lines = [line for line, kind in includes if kind == "system"]
    local_lines = [line for line, kind in includes if kind == "local"]
    if system_lines and local_lines and min(local_lines) == max(system_lines) + 1:
        findings.append(Finding("warning", path, "system and local includes should have a blank line"))
    return findings


def _has_doxygen(raw: str, function_name: str, terminator: str) -> bool:
    pattern = re.compile(
        rf"/\*\*(?P<body>(?:(?!/\*|\*/).)*)\*/\s*[A-Za-z_][A-Za-z0-9_\s*]*\b{re.escape(function_name)}\s*\([^;{{}}]*\)\s*{re.escape(terminator)}",
        re.DOTALL,
    )
    for match in pattern.finditer(raw):
        body = match.group("body")
        if all(tag in body for tag in ("@brief", "@param", "@retval")):
            return True
    return False


def _component_pair_findings(source: SourceFile, header: SourceFile) -> list[Finding]:
    findings = _check_sections(source.requested, source.text, ".c")
    findings.extend(_check_sections(header.requested, header.text, ".h"))
    findings.extend(_include_findings(source.requested, source.text))
    findings.extend(_include_findings(header.requested, header.text))
    try:
        source_code = _strip_noncode(source.text)
        header_code = _strip_noncode(header.text)
    except AnalysisError as exc:
        raise AnalysisError(f"unable to analyze component structure: {exc}") from exc

    name = source.requested.stem
    if not NAME_RE.fullmatch(name):
        findings.append(Finding("error", source.requested, "component basename is not valid snake_case"))
        return findings
    symbol = _symbol_prefix(name)
    macro = _macro_prefix(name)
    plural = f"{symbol}s"

    required_header = [
        rf"typedef\s+struct\s+_{symbol}Pub\s+{symbol}Pub_t\s*;",
        rf"typedef\s+struct\s+_{symbol}CB\s+{symbol}CB_t\s*;",
        rf"struct\s+_{symbol}Pub\s*\{{",
        rf"struct\s+_{symbol}CB\s*\{{",
        rf"\b{macro}_CMD_NONE\b",
        rf"\b{macro}_CMD_MAX\b",
        rf"\b{macro}_NUM\b",
    ]
    for pattern in required_header:
        if re.search(pattern, header_code) is None:
            findings.append(Finding("error", header.requested, f"missing required declaration matching {pattern}"))

    callback = _extract_braced(header_code, rf"struct\s+_{symbol}CB\s*\{{")
    if callback is not None:
        for member in ("start", "done"):
            pattern = rf"int\s*\(\s*\*\s*{member}\s*\)\s*\(\s*{symbol}Pub_t\s*\*\s*pub\s*\)\s*;"
            if re.search(pattern, callback[0]) is None:
                findings.append(Finding("error", header.requested, f"callback type requires {member}"))

    internal = _extract_braced(source_code, rf"typedef\s+struct\s+_{symbol}\s*\{{")
    if internal is None:
        findings.append(Finding("error", source.requested, f"missing internal {symbol}_t structure"))
    else:
        body, _, closing = internal
        alias = re.match(rf"\s*{symbol}_t\s*;", source_code[closing + 1 :])
        if alias is None:
            findings.append(Finding("error", source.requested, f"internal structure alias must be {symbol}_t"))
        if re.match(rf"\s*{symbol}Pub_t\s+pub\s*;", body) is None:
            findings.append(Finding("error", source.requested, "public structure must be the first internal member"))
        required_members = {
            "cb": rf"\bconst\s+{symbol}CB_t\s*\*\s*cb\s*;",
            "ops": rf"\bconst\s+{symbol}Ops_t\s*\*\s*ops\s*;",
            "tick": r"\buint32_t\b[^;]*\btick\b[^;]*;",
            f"{symbol}Tick": rf"\buint32_t\b[^;]*\b{symbol}Tick\b[^;]*;",
            "state": r"\buint8_t\b[^;]*\bstate\b[^;]*;",
            "id": r"\buint8_t\b[^;]*\bid\b[^;]*;",
            "cmd": r"\buint8_t\b[^;]*\bcmd\b[^;]*;",
            "loglvl": r"\buint8_t\b[^;]*\bloglvl\b[^;]*;",
        }
        for member, pattern in required_members.items():
            if re.search(pattern, body) is None:
                findings.append(Finding("error", source.requested, f"internal structure requires {member}"))
        flag = _extract_braced(body, r"\bstruct\s*\{")
        if flag is None or re.match(r"\s*flag\s*;", body[flag[2] + 1 :]) is None:
            findings.append(Finding("error", source.requested, "internal structure requires flag"))
        else:
            for member in ("start", "busy"):
                if re.search(rf"\buint32_t\s+{member}\s*:\s*1\s*;", flag[0]) is None:
                    findings.append(Finding("error", source.requested, f"flag requires one-bit {member}"))

    operations = _extract_braced(source_code, rf"struct\s+_{symbol}Ops\s*\{{")
    if operations is not None:
        init_member = rf"int\s*\(\s*\*\s*init\s*\)\s*\(\s*{symbol}_t\s*\*\s*me\s*\)\s*;"
        if re.search(init_member, operations[0]) is None:
            findings.append(Finding("error", source.requested, "operations type requires init"))

    required_source = [
        rf"typedef\s+struct\s+_{symbol}Ops\s+{symbol}Ops_t\s*;",
        rf"struct\s+_{symbol}Ops\s*\{{",
        rf"typedef\s+int\s*\(\s*\*\s*handler_t\s*\)\s*\(\s*{symbol}_t\s*\*\s*me\s*\)\s*;",
        rf"static\s+{symbol}_t\s+{plural}\s*\[\s*{macro}_NUM\s*\]",
    ]
    for pattern in required_source:
        if re.search(pattern, source_code) is None:
            findings.append(Finding("error", source.requested, f"missing required declaration matching {pattern}"))

    enum_block = None
    cursor = 0
    while True:
        candidate = _extract_braced(source_code, r"\benum(?:\s+[A-Za-z_]\w*)?\s*\{", cursor)
        if candidate is None:
            break
        if "ST_IDLE" in candidate[0] and "ST_MAX" in candidate[0]:
            enum_block = candidate
            break
        cursor = candidate[2] + 1
    states: list[str] = []
    if enum_block is None:
        findings.append(Finding("error", source.requested, "missing state enum"))
    else:
        entries = [entry.strip() for entry in enum_block[0].split(",") if entry.strip()]
        expected_value = 0
        for entry in entries:
            match = re.fullmatch(r"(ST_[A-Z0-9_]+)(?:\s*=\s*(\d+))?", entry)
            if match is None:
                findings.append(Finding("error", source.requested, f"unsupported state enumerator: {entry}"))
                states = []
                break
            value = int(match.group(2)) if match.group(2) is not None else expected_value
            if value != expected_value:
                findings.append(Finding("error", source.requested, "state enumerators must be contiguous"))
            states.append(match.group(1))
            expected_value = value + 1
        first_entry = entries[0] if entries else ""
        if states and (states[0] != "ST_IDLE" or re.fullmatch(r"ST_IDLE\s*=\s*0", first_entry) is None):
            findings.append(Finding("error", source.requested, "state enum must start with ST_IDLE = 0"))
        if states and states[-1] != "ST_MAX":
            findings.append(Finding("error", source.requested, "ST_MAX must be the final state sentinel"))
        if "ST_END" not in states:
            findings.append(Finding("error", source.requested, "state enum must contain ST_END"))

    array = _extract_braced(
        source_code,
        rf"static\s+const\s+handler_t\s+{symbol}_handler\s*\[\s*ST_MAX\s*\]\s*=\s*\{{",
    )
    if array is None:
        findings.append(Finding("error", source.requested, f"missing static const {symbol}_handler[ST_MAX]"))
    elif states:
        handlers = [entry.strip() for entry in array[0].split(",") if entry.strip()]
        expected_handlers = [state.lower() for state in states[:-1]]
        if handlers != expected_handlers:
            findings.append(Finding("error", source.requested, "handler names or order do not match the state enum"))
        for handler in expected_handlers:
            signature = rf"static\s+int\s+{handler}\s*\(\s*{symbol}_t\s*\*\s*me\s*\)"
            if re.search(signature, source_code) is None:
                findings.append(Finding("error", source.requested, f"missing state handler {handler}"))

    functions = {
        f"{symbol}_getPub": rf"{symbol}Pub_t\s*\*\s*{symbol}_getPub\s*\(",
        f"{symbol}_setCallback": rf"\bint\s+{symbol}_setCallback\s*\(",
        f"{symbol}_status": rf"\bint\s+{symbol}_status\s*\(",
        f"{symbol}_init": rf"\bint\s+{symbol}_init\s*\(\s*void\s*\)",
        f"{symbol}_routine": rf"\bint\s+{symbol}_routine\s*\(\s*void\s*\)",
    }
    for function, signature in functions.items():
        if re.search(signature, source_code) is None or re.search(signature, header_code) is None:
            findings.append(Finding("error", source.requested, f"missing public API {function}"))
        if not _has_doxygen(source.text, function, "{"):
            findings.append(Finding("error", source.requested, f"incomplete Doxygen for {function} definition"))
        if not _has_doxygen(header.text, function, ";"):
            findings.append(Finding("error", header.requested, f"incomplete Doxygen for {function} declaration"))
    return findings


def _register_findings(source: SourceFile) -> tuple[list[Finding], int]:
    try:
        code = _strip_noncode(source.text)
    except AnalysisError as exc:
        raise AnalysisError(f"unable to analyze register structure: {exc}") from exc
    findings: list[Finding] = []
    recognized = 0
    cursor = 0
    while True:
        union = _extract_braced(code, r"typedef\s+union(?:\s+[A-Za-z_]\w*)?\s*\{", cursor)
        if union is None:
            break
        body, _, closing = union
        cursor = closing + 1
        alias_match = re.match(r"\s*([A-Za-z_]\w*)\s*;", code[closing + 1 :])
        if alias_match is None or re.search(r"\buint8_t\s+value\s*;", body) is None:
            if re.search(r"\b(?:uint16_t|uint32_t|uint64_t)\s+value\s*;", body):
                recognized += 1
                alias = alias_match.group(1) if alias_match else "multi-byte register union"
                findings.append(
                    Finding(
                        "error",
                        source.requested,
                        f"{alias} must use explicit multi-byte encode/decode instead of a raw union value",
                    )
                )
            continue
        recognized += 1
        alias = alias_match.group(1)
        bit_struct = _extract_braced(body, r"\bstruct\s*\{")
        if bit_struct is None or re.match(r"\s*bit\s*;", body[bit_struct[2] + 1 :]) is None:
            findings.append(Finding("error", source.requested, f"{alias} must contain a .bit structure"))
        else:
            widths = [int(value) for value in re.findall(r"\buint8_t\s+\w+\s*:\s*(\d+)\s*;", bit_struct[0])]
            if not widths or sum(widths) != 8:
                findings.append(Finding("error", source.requested, f"{alias} bit-field widths must total 8"))
        assertion = rf"_Static_assert\s*\(\s*sizeof\s*\(\s*{re.escape(alias)}\s*\)\s*==\s*1U?\b"
        if re.search(assertion, code) is None:
            findings.append(Finding("error", source.requested, f"{alias} requires a one-byte _Static_assert"))
        findings.append(
            Finding(
                "warning",
                source.requested,
                f"{alias} reset value, reserved bits, field order, and bus use require datasheet review",
            )
        )
    for enum in re.finditer(r"\benum(?:\s+\w+)?\s*\{([^{}]*)\}", code, re.DOTALL):
        if "<<" in enum.group(1):
            findings.append(Finding("error", source.requested, "register field enum values must not be pre-shifted"))
    return findings, recognized


def _component_pairs(sources: list[SourceFile]) -> tuple[list[tuple[SourceFile, SourceFile]], list[Finding]]:
    by_key: dict[tuple[Path, str], dict[str, SourceFile]] = {}
    for source in sources:
        key = (source.requested.parent.resolve(), source.requested.stem)
        by_key.setdefault(key, {})[source.requested.suffix] = source
    pairs: list[tuple[SourceFile, SourceFile]] = []
    findings: list[Finding] = []
    for members in by_key.values():
        if ".c" not in members or ".h" not in members:
            member = next(iter(members.values()))
            findings.append(Finding("error", member.requested, "component source/header mate is missing"))
        else:
            pairs.append((members[".c"], members[".h"]))
    return pairs, findings


def _unified_diff(source: SourceFile, formatted: str) -> list[str]:
    return list(
        difflib.unified_diff(
            source.text.splitlines(keepends=True),
            formatted.splitlines(keepends=True),
            fromfile=f"a/{display_path(source.requested)}",
            tofile=f"b/{display_path(source.requested)}",
        )
    )


def process_files(args: argparse.Namespace, executable: str, style: Path) -> int:
    paths = _expand_inputs(args)
    if not paths:
        raise WorkflowError("no matching .c or .h files were found")
    sources = _load_unique_sources(paths, args.follow_symlinks)
    formatted_by_path: dict[Path, str] = {}
    changed: list[SourceFile] = []
    for source in sources:
        formatted = _format_text(executable, style, source.text, source.requested)
        formatted_by_path[source.path] = formatted
        if _encoded_result(source, formatted) != source.data:
            changed.append(source)

    if not args.check:
        _atomic_batch_replace(
            [
                (source.path, _encoded_result(source, formatted_by_path[source.path]), source.mode)
                for source in changed
            ]
        )
        changed_paths = {source.path for source in changed}
        for source in sources:
            outcome = "formatted" if source.path in changed_paths else "unchanged"
            print(f"{outcome}: {display_path(source.requested)}")
        return EXIT_CLEAN

    findings: list[Finding] = []
    if args.component:
        pairs, pair_findings = _component_pairs(sources)
        findings.extend(pair_findings)
        for source, header in pairs:
            findings.extend(_component_pair_findings(source, header))
    if args.register:
        recognized = 0
        for source in sources:
            register_findings, count = _register_findings(source)
            findings.extend(register_findings)
            recognized += count
        if recognized == 0:
            warning(Path("."), "no one-byte register structures were recognized")

    diff_lines = 0
    diff_bytes = 0
    truncated = False
    for source in changed:
        print(f"needs formatting: {display_path(source.requested)}")
        for line in _unified_diff(source, formatted_by_path[source.path]):
            encoded_size = len(line.encode("utf-8"))
            if not args.full and (
                diff_lines + 1 > MAX_DIFF_LINES or diff_bytes + encoded_size > MAX_DIFF_BYTES
            ):
                truncated = True
                break
            sys.stdout.write(line)
            diff_lines += 1
            diff_bytes += encoded_size
        if truncated:
            break
    if truncated:
        print("diff truncated; pass --full to show all content")

    for finding in findings:
        stream = sys.stderr
        print(
            f"{finding.severity}: {display_path(finding.path)}: {finding.message}",
            file=stream,
        )
    for source in sources:
        if source not in changed:
            print(f"unchanged: {display_path(source.requested)}")
    return EXIT_FINDINGS if changed or any(item.severity == "error" for item in findings) else EXIT_CLEAN


def display_path(path: Path) -> str:
    try:
        return os.path.relpath(path, Path.cwd())
    except ValueError:
        return str(path)


def warning(path: Path, message: str) -> None:
    print(f"warning: {display_path(path)}: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        executable = _resolve_clang_format(args.clang_format)
        style = _validate_style(args.style)
        creating = args.new is not None or bool(
            args.inputs and all(_is_creation_name(x) for x in args.inputs)
        )
        if creating:
            return create_files(args, executable, style)
        return process_files(args, executable, style)
    except WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"error: I/O failure: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
