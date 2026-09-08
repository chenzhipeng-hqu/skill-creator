"""Narrow, opt-in hook adapters for installed Stable Skills."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import yaml

from .model import Diagnostic, Result
from .targets import AgentTarget, is_exact_managed_link
from .identity import package_identity


def discover_hook_integrations(stable: Path) -> tuple[tuple[dict[str, object], ...], tuple[Diagnostic, ...]]:
    """Read the opt-in integration manifest shipped with one Stable Skill."""

    path = stable / "skill-manager.yaml"
    if not path.exists():
        return (), ()
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return (), (Diagnostic("error", "HOOK_MANIFEST_INVALID", str(exc)),)
    hooks = document.get("hooks") if isinstance(document, dict) else None
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(hooks, list) or not all(isinstance(item, dict) for item in hooks):
        return (), (Diagnostic("error", "HOOK_MANIFEST_INVALID", f"invalid hook manifest: {path}"),)
    return tuple(hooks), ()


def manage_commit_gate(action: str, skill: str, target: AgentTarget, stable: Path, dry_run: bool) -> tuple[Result, tuple[Diagnostic, ...]]:
    """Manage the one supported integration: git-commit on Codex hooks.json."""

    result = Result("stable", skill, str(stable), "unsupported", target=target.key)
    if skill != "git-commit" or target.key != "codex":
        return result, (Diagnostic("error", "HOOK_ADAPTER_UNSUPPORTED", f"no hook adapter for {skill} on {target.key}"),)
    integrations, manifest_diagnostics = discover_hook_integrations(stable)
    if manifest_diagnostics:
        return result, manifest_diagnostics
    declared = next((item for item in integrations if item.get("id") == "commit-gate"), None)
    targets = declared.get("targets") if isinstance(declared, dict) else None
    codex = targets.get("codex") if isinstance(targets, dict) else None
    if declared is None or declared.get("event") != "PreToolUse" or declared.get("script") != "scripts/commit-gate.sh" or not isinstance(codex, dict) or codex.get("matcher") != "Bash":
        return result, (Diagnostic("error", "HOOK_INTEGRATION_UNDECLARED", f"{skill} does not declare the Codex commit-gate integration"),)
    script = target.skills_root / skill / "scripts" / "commit-gate.sh"
    installed = is_exact_managed_link(target.skills_root / skill, stable)
    if (not installed and not dry_run) or not (script.is_file() if installed else (stable / "scripts" / "commit-gate.sh").is_file()):
        return result, (Diagnostic("error", "HOOK_SKILL_NOT_INSTALLED", f"install {skill} on {target.key} before registering its hook"),)
    path = target.config_root / "hooks.json"
    try:
        document = {} if not path.exists() else json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return result, (Diagnostic("error", "HOOK_CONFIG_INVALID", str(exc)),)
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return result, (Diagnostic("error", "HOOK_CONFIG_INVALID", "hooks.json hooks must be an object"),)
    entries = hooks.setdefault("PreToolUse", [])
    if not isinstance(entries, list):
        return result, (Diagnostic("error", "HOOK_CONFIG_INVALID", "hooks.PreToolUse must be an array"),)
    expected = {"matcher": "Bash", "hooks": [{"type": "command", "command": f"bash {script}", "timeout": 10}]}
    matching = [entry for entry in entries if entry == expected]
    same_script = [entry for entry in entries if _contains_command(entry, str(script))]
    receipt = _receipt_path(target, skill)
    if action == "status":
        outcome = "installed" if matching else ("conflict" if same_script else "not-installed")
        return Result("stable", skill, str(stable), outcome, {"config_path": str(path), "script": str(script), "receipt_path": str(receipt), "receipt_state": "present" if receipt.is_file() else "absent"}, target.key), ()
    if action == "install":
        if matching:
            outcome = "no-change"
        elif same_script:
            return result, (Diagnostic("error", "HOOK_CONFIGURATION_CONFLICT", f"existing hook for {script} differs from managed contract"),)
        else:
            outcome = "would-install" if dry_run else "installed"
            if not dry_run:
                entries.append(expected)
                _write_json_atomic(path, document)
                _write_json_atomic(receipt, {"schema_version": 1, "skill": skill, "agent": target.key, "script": str(script), "stable_identity": package_identity(stable), "entry": expected})
        return Result("stable", skill, str(stable), outcome, {"config_path": str(path), "script": str(script), "receipt_path": str(receipt)}, target.key), ()
    if action == "uninstall":
        receipt_document = _read_receipt(receipt)
        if receipt_document is None or receipt_document != {"schema_version": 1, "skill": skill, "agent": target.key, "script": str(script), "stable_identity": package_identity(stable), "entry": expected}:
            return result, (Diagnostic("error", "HOOK_RECEIPT_CONFLICT", f"missing or mismatched hook receipt: {receipt}"),)
        if not matching:
            return result, (Diagnostic("error", "HOOK_CONFIGURATION_CONFLICT", f"managed hook entry missing or changed: {path}"),)
        else:
            outcome = "would-uninstall" if dry_run else "uninstalled"
            if not dry_run:
                hooks["PreToolUse"] = [entry for entry in entries if entry != expected]
                _write_json_atomic(path, document)
                receipt.unlink()
        return Result("stable", skill, str(stable), outcome, {"config_path": str(path), "script": str(script), "receipt_path": str(receipt)}, target.key), ()
    raise ValueError(f"unsupported hook action: {action}")


def _contains_command(entry: object, command: str) -> bool:
    return isinstance(entry, dict) and any(
        isinstance(handler, dict) and handler.get("command") == f"bash {command}"
        for handler in entry.get("hooks", []) if isinstance(entry.get("hooks"), list)
    )


def _write_json_atomic(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".skill-manager-hooks-", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _receipt_path(target: AgentTarget, skill: str) -> Path:
    return target.config_root / ".skill-manager" / "hook-receipts" / f"{skill}.json"


def _read_receipt(path: Path) -> dict[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None
