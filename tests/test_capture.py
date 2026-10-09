from __future__ import annotations

import pytest

from tanaw.capture import (
    MSG_BRING_TO_FRONT,
    MSG_WINDOW_CAPTURE_FAILED,
    AmbiguousWindowError,
    CaptureError,
    CaptureMethod,
    WindowFrameState,
    WindowInfo,
    WindowNotFoundError,
    choose_window,
    decide_source,
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


@pytest.mark.parametrize(
    ("method", "state", "foreground", "expected"),
    [
        ("auto", "ok", False, "window"),  # covered game: its own pixels, never the cover
        ("auto", "ok", True, "window"),
        ("auto", "blank", True, "screen"),  # PrintWindow gave black; screen is safe in front
        ("auto", "failed", True, "screen"),
        ("auto", "blank", False, "window"),  # honest black frame, no other app's pixels
        ("window", "blank", False, "window"),
        ("screen", "ok", True, "screen"),
    ],
)
def test_decide_source(
    method: CaptureMethod, state: WindowFrameState, foreground: bool, expected: str
) -> None:
    assert decide_source(method, state, foreground) == expected


@pytest.mark.parametrize(
    ("method", "state", "message"),
    [
        ("auto", "failed", MSG_BRING_TO_FRONT),
        ("screen", "ok", MSG_BRING_TO_FRONT),
        ("window", "failed", MSG_WINDOW_CAPTURE_FAILED),
    ],
)
def test_screen_pixels_never_used_when_game_is_not_in_front(
    method: CaptureMethod, state: WindowFrameState, message: str
) -> None:
    with pytest.raises(CaptureError, match=message):
        decide_source(method, state, foreground=False)
