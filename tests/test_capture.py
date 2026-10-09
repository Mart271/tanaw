from __future__ import annotations

import pytest

from tanaw.capture import (
    AmbiguousWindowError,
    WindowInfo,
    WindowNotFoundError,
    choose_window,
    validate_title_query,
)

GAME = WindowInfo(1, "DELTARUNE", "YYGameMakerYY")
TERMINAL = WindowInfo(2, "python -m tanaw --window DELTARUNE", "CASCADIA_HOSTING_WINDOW_CLASS")
EXPLORER = WindowInfo(3, "DELTARUNE", "CabinetWClass")
BROWSER = WindowInfo(4, "DELTARUNE by Toby Fox - itch.io - Chrome", "Chrome_WidgetWin_1")
NOTEPAD = WindowInfo(5, "notes.txt - Notepad", "Notepad")


def test_picks_game_and_skips_terminal_and_explorer() -> None:
    assert choose_window([TERMINAL, EXPLORER, GAME, NOTEPAD], "deltarune", set()) == GAME


def test_exact_title_wins_over_partial_matches() -> None:
    assert choose_window([BROWSER, GAME], "DELTARUNE", set()) == GAME


def test_ambiguous_partial_matches_are_refused() -> None:
    other = WindowInfo(6, "DELTARUNE wiki - Firefox", "MozillaWindowClass")
    with pytest.raises(AmbiguousWindowError):
        choose_window([BROWSER, other], "DELTA", set())


def test_own_console_is_excluded() -> None:
    console = WindowInfo(7, "DELTARUNE", "PseudoConsoleWindow")
    assert choose_window([console, GAME], "DELTARUNE", {7}) == GAME


def test_not_found() -> None:
    with pytest.raises(WindowNotFoundError):
        choose_window([NOTEPAD], "DELTARUNE", set())


@pytest.mark.parametrize("bad", ["", "   ", "x" * 300, "bad\x00title"])
def test_title_query_validation(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_title_query(bad)
