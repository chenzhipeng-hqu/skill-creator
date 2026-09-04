"""Three-screen installation TUI and its pure confirmation state machine."""

from __future__ import annotations

import curses
from dataclasses import dataclass
from typing import TextIO

from .identity import canonical_skill_name
from .model import CandidateSource


@dataclass(frozen=True)
class TuiState:
    screen: int = 1
    cancelled: bool = False
    applied: bool = False


def advance(state: TuiState, key: str, *, has_errors: bool = False) -> TuiState:
    """Apply one key without performing any installation mutation."""

    if key == "q":
        return TuiState(screen=state.screen, cancelled=True)
    if state.cancelled or state.applied:
        return state
    if key == "\n" and state.screen < 3:
        return TuiState(screen=state.screen + 1)
    if state.screen == 3 and key == "y" and not has_errors:
        return TuiState(screen=3, applied=True)
    return state


def run_tui(manager: object, input_stream: TextIO, output: TextIO, *, dry_run: bool = False) -> int:
    """Run the line-oriented three-screen adapter over the typed manager core."""

    from .service import SkillManager

    if not isinstance(manager, SkillManager):
        raise TypeError("manager must be a SkillManager")
    recovery = manager.apply(
        manager.plan("recover", manager.scan(), dry_run=True)
    )
    if recovery.results or recovery.status in {"recovery-required", "busy"}:
        print("Recovery Required", file=output)
        for result in recovery.results:
            print(f"  transaction {result.skill}: {result.outcome}", file=output)
        for diagnostic in recovery.diagnostics:
            print(f"  {diagnostic.code}: {diagnostic.message}", file=output)
        print("Run: python3 tools/skill_manager.py recover --dry-run", file=output)
        print("Then: python3 tools/skill_manager.py recover", file=output)
        return 1
    targets = manager.detected_targets()
    if input_stream.isatty() and output.isatty():
        return curses.wrapper(
            lambda window: _run_curses_session(manager, window, targets, dry_run)
        )
    print("Screen 1/3 - Agent Targets", file=output)
    for key, target in targets.items():
        print(f"  {key:<10} {target.config_root} -> {target.skills_root}", file=output)
    print("Select comma-separated targets, or q to cancel:", file=output)
    selected_line = input_stream.readline().strip()
    if selected_line == "q" or not selected_line:
        return 0
    selected_agents = tuple(dict.fromkeys(part.strip() for part in selected_line.split(",") if part.strip()))
    if any(key not in targets for key in selected_agents):
        print("Unknown or undetected Agent Target.", file=output)
        return 1

    scan = manager.scan()
    stable_skills = tuple(
        sorted(
            name
            for candidate in scan.candidates
            if candidate.source is CandidateSource.STABLE
            and (name := canonical_skill_name(candidate)) is not None
        )
    )
    print("Screen 2/3 - Desired Installation State", file=output)
    status = manager.apply(
        manager.plan("status", scan, agents=selected_agents)
    )
    by_skill = {result.skill: result for result in status.results}
    for skill in stable_skills:
        result = by_skill[skill]
        installation = result.details.get("installation_state", {})
        states = {
            str(details.get("state"))
            for details in installation.values()
            if isinstance(details, dict)
        } if isinstance(installation, dict) else set()
        if result.details.get("validation_state") == "invalid" or "conflict" in states:
            symbol = "!"
        elif states == {"installed"}:
            symbol = "x"
        elif len(states) > 1:
            symbol = "~"
        else:
            symbol = " "
        print(f"  [{symbol}] {skill}", file=output)
    print("Enter 'install SKILL...' or 'uninstall SKILL...', or q to cancel:", file=output)
    selection = input_stream.readline().strip().split()
    if not selection or selection == ["q"]:
        return 0
    if selection[0] not in {"install", "uninstall"} or len(selection) == 1:
        print("Expected install or uninstall followed by at least one Skill.", file=output)
        return 1
    command = selection[0]
    skills = tuple(selection[1:])
    preview = manager.apply(
        manager.plan(
            command,
            scan,
            skills=skills,
            agents=selected_agents,
            dry_run=True,
        )
    )
    print("Screen 3/3 - Batch Plan", file=output)
    for result in preview.results:
        print(f"  {result.target}/{result.skill}: {result.outcome}", file=output)
    for diagnostic in preview.diagnostics:
        print(f"  {diagnostic.severity} {diagnostic.code}: {diagnostic.message}", file=output)
    if preview.status not in {"success", "no-change"}:
        print("Apply disabled because the plan has errors.", file=output)
        return 1
    if dry_run:
        return 0
    print("Type y to apply; any other input cancels:", file=output)
    if input_stream.readline().strip() != "y":
        return 0
    report = manager.apply(
        manager.plan(command, manager.scan(), skills=skills, agents=selected_agents)
    )
    print(f"Status: {report.status}", file=output)
    return 0 if report.status in {"success", "no-change"} else 1


def _run_curses_session(manager: object, window: curses.window, targets: dict[str, object], dry_run: bool) -> int:
    from .service import SkillManager

    assert isinstance(manager, SkillManager)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    selected_agents = _choose_targets(window, targets)
    if selected_agents is None:
        return 0
    scan = manager.scan()
    status = manager.apply(manager.plan("status", scan, agents=selected_agents))
    changes = _choose_skill_states(window, status.results, selected_agents)
    if changes is None or not changes:
        return 0
    desired_values = set(changes.values())
    if len(desired_values) != 1:
        _show_curses_lines(
            window,
            "Batch Plan",
            ["error: install and uninstall are separate Atomic Batches", "Apply the two desired-state groups separately."],
        )
        return 1
    command = "install" if desired_values == {True} else "uninstall"
    skills = tuple(sorted(changes))
    preview = manager.apply(
        manager.plan(command, manager.scan(), skills=skills, agents=selected_agents, dry_run=True)
    )
    if not _review_plan(window, preview):
        return 1 if preview.status == "failed" else 0
    if dry_run:
        return 0
    report = manager.apply(
        manager.plan(command, manager.scan(), skills=skills, agents=selected_agents)
    )
    _show_curses_lines(
        window,
        "Result",
        [f"{result.target}/{result.skill}: {result.outcome}" for result in report.results]
        or [f"Status: {report.status}"],
    )
    return 0 if report.status in {"success", "no-change"} else 1


def _choose_targets(window: curses.window, targets: dict[str, object]) -> tuple[str, ...] | None:
    keys = tuple(targets)
    if not keys:
        _show_curses_lines(window, "Agent Targets", ["No detected Agent Targets."])
        return None
    index = 0
    selected: set[str] = set()
    while True:
        window.erase()
        _add_line(window, 0, "Screen 1/3 - Agent Targets")
        _add_line(window, 1, "Arrows: move  Space: toggle  Enter: continue  q: cancel")
        for row, key in enumerate(keys, start=3):
            target = targets[key]
            mark = "x" if key in selected else " "
            attribute = curses.A_REVERSE if row - 3 == index else 0
            _add_line(window, row, f"[{mark}] {key:<10} {target.config_root} -> {target.skills_root}", attribute)
        window.refresh()
        key_code = window.getch()
        if key_code in (ord("q"), 27):
            return None
        if key_code in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(keys)
        elif key_code in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(keys)
        elif key_code == ord(" "):
            selected.symmetric_difference_update({keys[index]})
        elif key_code in (curses.KEY_ENTER, 10, 13) and selected:
            return tuple(key for key in keys if key in selected)


def _choose_skill_states(
    window: curses.window,
    results: tuple[object, ...],
    selected_agents: tuple[str, ...],
) -> dict[str, bool] | None:
    stable_results = tuple(result for result in results if result.details.get("stable_provenance") != "absent")
    if not stable_results:
        _show_curses_lines(window, "Stable Skills", ["No Stable Skills."])
        return None
    index = 0
    desired: dict[str, bool] = {}
    message = ""
    while True:
        window.erase()
        _add_line(window, 0, "Screen 2/3 - Desired Installation State")
        _add_line(window, 1, "Arrows: move  Space: change state  Enter: continue  q: cancel")
        for row, result in enumerate(stable_results, start=3):
            current, blocked = _aggregate_installation_state(result, selected_agents)
            if result.skill in desired:
                symbol = "x" if desired[result.skill] else " "
                suffix = "will install" if desired[result.skill] else "will uninstall"
            else:
                symbol = "!" if blocked else "~" if current is None else "x" if current else " "
                suffix = "conflict" if blocked else "mixed" if current is None else "installed" if current else "not installed"
            attribute = curses.A_REVERSE if row - 3 == index else 0
            _add_line(window, row, f"[{symbol}] {result.skill:<32} {suffix}", attribute)
        _add_line(window, len(stable_results) + 4, message)
        window.refresh()
        key_code = window.getch()
        if key_code in (ord("q"), 27):
            return None
        if key_code in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(stable_results)
        elif key_code in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(stable_results)
        elif key_code == ord(" "):
            result = stable_results[index]
            current, blocked = _aggregate_installation_state(result, selected_agents)
            if blocked:
                message = f"Resolve the conflict or invalid Stable Skill: {result.skill}"
            else:
                desired[result.skill] = not current if result.skill not in desired else not desired[result.skill]
                message = ""
        elif key_code in (curses.KEY_ENTER, 10, 13):
            return desired


def _aggregate_installation_state(result: object, selected_agents: tuple[str, ...]) -> tuple[bool | None, bool]:
    installation = result.details.get("installation_state", {})
    states = [
        installation.get(agent, {}).get("state", "not-installed")
        for agent in selected_agents
    ]
    blocked = result.details.get("stable_validation_state") == "invalid" or any(state == "conflict" for state in states)
    if all(state == "installed" for state in states):
        return True, blocked
    if all(state == "not-installed" for state in states):
        return False, blocked
    return None, blocked


def _review_plan(window: curses.window, report: object) -> bool:
    window.erase()
    _add_line(window, 0, "Screen 3/3 - Batch Plan")
    row = 2
    for result in report.results:
        _add_line(window, row, f"{result.target}/{result.skill}: {result.outcome}")
        row += 1
    for diagnostic in report.diagnostics:
        _add_line(window, row, f"{diagnostic.severity} {diagnostic.code}: {diagnostic.message}")
        row += 1
    if report.status not in {"success", "no-change"}:
        _add_line(window, row + 1, "Apply disabled. Press q or Enter to close.")
    else:
        _add_line(window, row + 1, "Type y to apply; q cancels. Enter never applies.")
    window.refresh()
    while True:
        key_code = window.getch()
        if key_code in (ord("q"), 27, curses.KEY_ENTER, 10, 13):
            return False
        if key_code == ord("y") and report.status in {"success", "no-change"}:
            return True


def _show_curses_lines(window: curses.window, title: str, lines: list[str]) -> None:
    window.erase()
    _add_line(window, 0, title)
    for row, line in enumerate(lines, start=2):
        _add_line(window, row, line)
    _add_line(window, len(lines) + 3, "Press q or Enter to close.")
    window.refresh()
    while window.getch() not in (ord("q"), 27, curses.KEY_ENTER, 10, 13):
        pass


def _add_line(window: curses.window, row: int, text: str, attribute: int = 0) -> None:
    try:
        window.addnstr(row, 0, text, max(1, window.getmaxyx()[1] - 1), attribute)
    except curses.error:
        pass
