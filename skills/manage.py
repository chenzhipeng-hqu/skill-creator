#!/usr/bin/env python3
"""Manage repository skills installed into supported Agent skill roots."""

from __future__ import annotations

import argparse
import curses
import os
import re
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence


SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class InstallState(Enum):
    """Installation state for one skill across one or more targets."""

    NOT_INSTALLED = "not-installed"
    INSTALLED = "installed"
    PARTIAL = "partial"
    CONFLICT = "conflict"


class ManagerError(RuntimeError):
    """A user-facing manager error."""


@dataclass(frozen=True)
class AgentTarget:
    """A supported Agent and its skill installation directory."""

    key: str
    label: str
    skills_dir: Path
    config_dir: Path
    command: str | None = None
    conflicts_with: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Skill:
    """A validated repository skill."""

    name: str
    path: Path


@dataclass(frozen=True)
class Operation:
    """One planned filesystem change."""

    action: str
    skill: str
    agent: str
    destination: Path


def default_agent_targets(home: Path) -> list[AgentTarget]:
    """Return the supported Agent targets for a home directory."""

    return [
        AgentTarget(
            key="codex",
            label="Codex",
            command="codex",
            config_dir=home / ".codex",
            skills_dir=home / ".codex" / "skills",
            conflicts_with=frozenset({"agents"}),
        ),
        AgentTarget(
            key="claude",
            label="Claude Code",
            command="claude",
            config_dir=home / ".claude",
            skills_dir=home / ".claude" / "skills",
        ),
        AgentTarget(
            key="agents",
            label="Shared Agent Skills",
            config_dir=home / ".agents",
            skills_dir=home / ".agents" / "skills",
            conflicts_with=frozenset({"codex"}),
        ),
    ]


def agent_is_detected(agent: AgentTarget) -> bool:
    """Return whether an Agent executable or configuration directory exists."""

    command_found = bool(agent.command and shutil.which(agent.command))
    return command_found or agent.config_dir.is_dir()


def detected_agent_targets(home: Path) -> list[AgentTarget]:
    """Return only Agent targets detected on the current system."""

    return [agent for agent in default_agent_targets(home) if agent_is_detected(agent)]


def _frontmatter(text: str) -> str | None:
    """Return SKILL.md YAML frontmatter without its delimiters."""

    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    return text[4:end]


def _frontmatter_field(text: str, field: str) -> str | None:
    """Read a simple scalar field from SKILL.md YAML frontmatter."""

    frontmatter = _frontmatter(text)
    if frontmatter is None:
        return None
    match = re.search(rf"^{re.escape(field)}:\s*([^\s>#][^\n]*)$", frontmatter, re.MULTILINE)
    return match.group(1).strip().strip("\"'") if match else None


def _has_frontmatter_description(text: str) -> bool:
    """Return whether frontmatter has a non-empty scalar or block description."""

    frontmatter = _frontmatter(text)
    if frontmatter is None:
        return False
    lines = frontmatter.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("description:"):
            continue
        value = line.partition(":")[2].strip()
        if value in {">", ">-", ">+", "|", "|-", "|+"}:
            for continuation in lines[index + 1 :]:
                if continuation and not continuation[0].isspace():
                    break
                if continuation.strip():
                    return True
            return False
        return bool(value.strip("\"'"))
    return False


def discover_skills(skills_root: Path) -> tuple[list[Skill], list[str]]:
    """Discover valid repository skills and return validation errors."""

    skills: list[Skill] = []
    errors: list[str] = []
    if not skills_root.is_dir():
        return skills, [f"skills directory does not exist: {skills_root}"]

    for entry in sorted(skills_root.iterdir(), key=lambda path: path.name):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        if entry.is_symlink():
            errors.append(f"{entry.name}: repository skill directories may not be symlinks")
            continue
        if not SKILL_NAME_PATTERN.fullmatch(entry.name):
            errors.append(f"{entry.name}: invalid skill directory name")
            continue

        skill_file = entry / "SKILL.md"
        openai_file = entry / "agents" / "openai.yaml"
        if not skill_file.is_file():
            errors.append(f"{entry.name}: missing SKILL.md")
            continue
        if not openai_file.is_file():
            errors.append(f"{entry.name}: missing agents/openai.yaml")
            continue

        try:
            text = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{entry.name}: cannot read SKILL.md: {exc}")
            continue

        declared_name = _frontmatter_field(text, "name")
        description_present = _has_frontmatter_description(text)
        if declared_name != entry.name:
            errors.append(
                f"{entry.name}: SKILL.md name is {declared_name!r}, expected {entry.name!r}"
            )
            continue
        if not description_present:
            errors.append(f"{entry.name}: missing non-empty description in SKILL.md")
            continue
        if openai_file.stat().st_size == 0:
            errors.append(f"{entry.name}: agents/openai.yaml is empty")
            continue

        skills.append(Skill(entry.name, entry.resolve()))

    return skills, errors


def _path_exists(path: Path) -> bool:
    """Like Path.exists(), but true for broken symbolic links."""

    return os.path.lexists(path)


def installation_state(skill: Skill, agent: AgentTarget) -> InstallState:
    """Return the installation state for one skill and Agent target."""

    destination = agent.skills_dir / skill.name
    if not _path_exists(destination):
        return InstallState.NOT_INSTALLED
    if not destination.is_symlink():
        return InstallState.CONFLICT
    try:
        target = destination.resolve(strict=False)
    except OSError:
        return InstallState.CONFLICT
    return InstallState.INSTALLED if target == skill.path else InstallState.CONFLICT


def aggregate_state(skill: Skill, agents: Sequence[AgentTarget]) -> InstallState:
    """Aggregate one skill's state across selected targets."""

    states = [installation_state(skill, agent) for agent in agents]
    if not states:
        return InstallState.NOT_INSTALLED
    if InstallState.CONFLICT in states:
        return InstallState.CONFLICT
    if all(state is InstallState.INSTALLED for state in states):
        return InstallState.INSTALLED
    if all(state is InstallState.NOT_INSTALLED for state in states):
        return InstallState.NOT_INSTALLED
    return InstallState.PARTIAL


def validate_agent_selection(agents: Sequence[AgentTarget]) -> None:
    """Reject target combinations that expose duplicate skill roots."""

    keys = {agent.key for agent in agents}
    for agent in agents:
        overlap = keys.intersection(agent.conflicts_with)
        if overlap:
            other = sorted(overlap)[0]
            raise ManagerError(
                f"targets {agent.key!r} and {other!r} overlap; choose only one of them"
            )


def plan_skill_state(
    skill: Skill,
    agents: Sequence[AgentTarget],
    should_install: bool,
) -> list[Operation]:
    """Preflight and plan changes for one skill without mutating the filesystem."""

    validate_agent_selection(agents)
    states = [(agent, installation_state(skill, agent)) for agent in agents]
    conflicts = [agent for agent, state in states if state is InstallState.CONFLICT]
    if conflicts:
        destinations = ", ".join(str(agent.skills_dir / skill.name) for agent in conflicts)
        raise ManagerError(f"refusing to overwrite conflicting path(s): {destinations}")

    operations: list[Operation] = []
    for agent, state in states:
        destination = agent.skills_dir / skill.name
        if should_install and state is InstallState.NOT_INSTALLED:
            operations.append(Operation("link", skill.name, agent.key, destination))
        elif not should_install and state is InstallState.INSTALLED:
            operations.append(Operation("unlink", skill.name, agent.key, destination))
    return operations


def apply_operations(skill: Skill, operations: Iterable[Operation], dry_run: bool) -> list[str]:
    """Apply preflighted link operations and return user-facing result lines."""

    results: list[str] = []
    for operation in operations:
        prefix = "would" if dry_run else "did"
        try:
            if operation.action == "link":
                if not dry_run:
                    operation.destination.parent.mkdir(parents=True, exist_ok=True)
                    if _path_exists(operation.destination):
                        raise ManagerError(
                            f"destination changed after preflight: {operation.destination}"
                        )
                    operation.destination.symlink_to(skill.path, target_is_directory=True)
                results.append(f"{prefix} link {operation.destination} -> {skill.path}")
                continue

            current_agent = AgentTarget(
                key=operation.agent,
                label=operation.agent,
                skills_dir=operation.destination.parent,
                config_dir=operation.destination.parent.parent,
            )
            if installation_state(skill, current_agent) is not InstallState.INSTALLED:
                raise ManagerError(
                    f"destination changed after preflight: {operation.destination}"
                )
            if not dry_run:
                operation.destination.unlink()
            results.append(f"{prefix} unlink {operation.destination}")
        except OSError as exc:
            raise ManagerError(
                f"{operation.action} failed for {operation.destination}: {exc}"
            ) from exc
    return results


def set_skill_state(
    skill: Skill,
    agents: Sequence[AgentTarget],
    should_install: bool,
    dry_run: bool = False,
) -> list[str]:
    """Preflight and apply one skill's desired state."""

    operations = plan_skill_state(skill, agents, should_install)
    if not operations:
        state = "installed" if should_install else "not installed"
        return [f"no change: {skill.name} is already {state}"]
    return apply_operations(skill, operations, dry_run)


def _state_symbol(state: InstallState) -> str:
    return {
        InstallState.INSTALLED: "x",
        InstallState.NOT_INSTALLED: " ",
        InstallState.PARTIAL: "~",
        InstallState.CONFLICT: "!",
    }[state]


def _add_line(window: curses.window, row: int, text: str, attr: int = 0) -> None:
    """Draw one clipped line without failing on a small terminal."""

    height, width = window.getmaxyx()
    if row >= height or width <= 1:
        return
    try:
        window.addnstr(row, 0, text, width - 1, attr)
    except curses.error:
        pass


def choose_agents(window: curses.window, agents: Sequence[AgentTarget]) -> list[AgentTarget] | None:
    """Show the Agent selection screen."""

    index = 0
    selected: set[str] = set()
    if len(agents) == 1:
        selected.add(agents[0].key)
    message = ""

    while True:
        window.erase()
        _add_line(window, 0, "Select target agents")
        _add_line(window, 1, "Space: toggle  Enter: continue  q: quit")
        for row, agent in enumerate(agents, start=3):
            mark = "x" if agent.key in selected else " "
            line = f"  [{mark}] {agent.label:<20} {agent.skills_dir}"
            attr = curses.A_REVERSE if row - 3 == index else 0
            _add_line(window, row, line, attr)
        _add_line(window, len(agents) + 4, message)
        window.refresh()

        key = window.getch()
        if key in (ord("q"), 27):
            return None
        if key in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(agents)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(agents)
        elif key == ord(" "):
            agent = agents[index]
            if agent.key in selected:
                selected.remove(agent.key)
                message = ""
                continue
            candidate = [item for item in agents if item.key in selected or item.key == agent.key]
            try:
                validate_agent_selection(candidate)
            except ManagerError as exc:
                message = str(exc)
            else:
                selected.add(agent.key)
                message = ""
        elif key in (curses.KEY_ENTER, 10, 13):
            if selected:
                return [agent for agent in agents if agent.key in selected]
            message = "Select at least one target."


def choose_skills(
    window: curses.window,
    skills: Sequence[Skill],
    agents: Sequence[AgentTarget],
) -> dict[str, bool] | None:
    """Show the skill desired-state screen."""

    index = 0
    desired: dict[str, bool] = {}
    touched: set[str] = set()
    message = ""

    while True:
        window.erase()
        labels = ", ".join(agent.label for agent in agents)
        _add_line(window, 0, f"Manage skills for: {labels}")
        _add_line(window, 1, "Space: toggle  Enter: apply  q: cancel")
        for row, skill in enumerate(skills, start=3):
            state = aggregate_state(skill, agents)
            if skill.name in touched:
                symbol = "x" if desired[skill.name] else " "
                suffix = "will install" if desired[skill.name] else "will uninstall"
            else:
                symbol = _state_symbol(state)
                suffix = state.value
            line = f"  [{symbol}] {skill.name:<32} {suffix}"
            attr = curses.A_REVERSE if row - 3 == index else 0
            _add_line(window, row, line, attr)
        _add_line(window, len(skills) + 4, message)
        window.refresh()

        key = window.getch()
        if key in (ord("q"), 27):
            return None
        if key in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(skills)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(skills)
        elif key == ord(" "):
            skill = skills[index]
            state = aggregate_state(skill, agents)
            if state is InstallState.CONFLICT:
                message = f"Resolve the destination conflict for {skill.name} first."
                continue
            current = desired.get(skill.name)
            if skill.name not in touched:
                desired[skill.name] = state is not InstallState.INSTALLED
                touched.add(skill.name)
            else:
                desired[skill.name] = not bool(current)
            message = ""
        elif key in (curses.KEY_ENTER, 10, 13):
            return {name: desired[name] for name in touched}


def _show_results(window: curses.window, lines: Sequence[str]) -> None:
    window.erase()
    _add_line(window, 0, "Result")
    for row, line in enumerate(lines, start=2):
        _add_line(window, row, line)
    _add_line(window, len(lines) + 3, "Press Enter or q to close.")
    window.refresh()
    while window.getch() not in (ord("q"), 27, curses.KEY_ENTER, 10, 13):
        pass


def run_tui(skills: Sequence[Skill], agents: Sequence[AgentTarget], dry_run: bool) -> int:
    """Run the interactive manager."""

    def session(window: curses.window) -> int:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        selected_agents = choose_agents(window, agents)
        if selected_agents is None:
            return 0
        changes = choose_skills(window, skills, selected_agents)
        if changes is None:
            return 0

        by_name = {skill.name: skill for skill in skills}
        results: list[str] = []
        try:
            for name, should_install in changes.items():
                results.extend(
                    set_skill_state(by_name[name], selected_agents, should_install, dry_run)
                )
        except ManagerError as exc:
            results.append(f"error: {exc}")
            _show_results(window, results)
            return 1
        _show_results(window, results or ["No changes selected."])
        return 0

    return curses.wrapper(session)


def _select_agents(agents: Sequence[AgentTarget], keys: Sequence[str]) -> list[AgentTarget]:
    """Resolve CLI Agent keys against detected targets."""

    known = {agent.key: agent for agent in agents}
    missing = [key for key in keys if key not in known]
    if missing:
        raise ManagerError(f"Agent target(s) not detected: {', '.join(missing)}")
    selected: list[AgentTarget] = []
    seen: set[str] = set()
    for key in keys:
        if key not in seen:
            selected.append(known[key])
            seen.add(key)
    validate_agent_selection(selected)
    return selected


def print_status(skills: Sequence[Skill], agents: Sequence[AgentTarget]) -> None:
    """Print detected targets and per-skill status."""

    print("Detected agents:")
    for agent in agents:
        print(f"  {agent.key:<8} {agent.label:<20} {agent.skills_dir}")
    print("Skills:")
    if not skills:
        print("  (none)")
        return
    for skill in skills:
        states = ", ".join(
            f"{agent.key}={installation_state(skill, agent).value}" for agent in agents
        )
        print(f"  {skill.name}: {states}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", dest="list_only", help="list status")
    action.add_argument("--install", metavar="SKILL", help="install one skill")
    action.add_argument("--uninstall", metavar="SKILL", help="uninstall one skill")
    parser.add_argument(
        "--agent",
        action="append",
        default=[],
        metavar="AGENT",
        help="target Agent key; repeat to select multiple",
    )
    parser.add_argument("--dry-run", action="store_true", help="show changes without applying")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    skills_root = Path(__file__).resolve().parent
    skills, validation_errors = discover_skills(skills_root)
    agents = detected_agent_targets(Path.home())

    for error in validation_errors:
        print(f"warning: {error}", file=sys.stderr)
    if not agents:
        print("error: no supported Agent installation detected", file=sys.stderr)
        return 1

    if args.list_only:
        try:
            selected = _select_agents(agents, args.agent) if args.agent else agents
        except ManagerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print_status(skills, selected)
        return 1 if validation_errors else 0

    requested_name = args.install or args.uninstall
    if requested_name:
        if not args.agent:
            parser.error("--agent is required with --install or --uninstall")
        by_name = {skill.name: skill for skill in skills}
        if requested_name not in by_name:
            print(f"error: unknown or invalid skill: {requested_name}", file=sys.stderr)
            return 1
        try:
            selected = _select_agents(agents, args.agent)
            lines = set_skill_state(
                by_name[requested_name],
                selected,
                should_install=bool(args.install),
                dry_run=args.dry_run,
            )
        except ManagerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for line in lines:
            print(line)
        return 0

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("error: TUI requires a terminal; use --list or a CLI action", file=sys.stderr)
        return 2
    if not skills:
        print("error: no valid repository skills found", file=sys.stderr)
        return 1
    return run_tui(skills, agents, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
