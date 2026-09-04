"""Pure three-screen installation TUI state machine."""

from __future__ import annotations

from dataclasses import dataclass


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
