"""Focus Mode end to end on a synthetic menu, with the real OCR models (offline).

A fake "game" draws a menu with a cursor sprite; the test moves the selection
the way arrow keys would and checks what Tanaw says. No game art involved.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest

from tanaw import netguard
from tanaw.capture import CapturedFrame
from tanaw.events import EventLog
from tanaw.focus import MSG_SELECTION_UNCLEAR, crop
from tanaw.focus_mode import MSG_FOCUS_OFF, MSG_FOCUS_ON, FocusController
from tanaw.frames import Frame, Rect
from tanaw.ocr import OcrEngine
from tanaw.profile import FocusProfile, LoadedProfile

ITEMS = ["Attack", "Defend", "Escape"]
ROW_Y = [120, 200, 280]
CURSOR_X = 60


def draw_cursor(img: Frame, x: int, y: int) -> None:
    pts = np.array([[x, y + 6], [x + 6, y], [x + 12, y + 6], [x + 18, y], [x + 24, y + 6],
                    [x + 12, y + 24]], np.int32)
    cv2.fillPoly(img, [pts], (0, 0, 255))
    cv2.rectangle(img, (x + 4, y + 4), (x + 7, y + 7), (255, 255, 255), -1)


def draw_menu(selected: int | None) -> Frame:
    img: Frame = np.zeros((400, 640, 3), dtype=np.uint8)
    for text, y in zip(ITEMS, ROW_Y, strict=True):
        cv2.putText(img, text, (110, y), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3)
    if selected is not None:
        draw_cursor(img, CURSOR_X, ROW_Y[selected] - 28)
    return img


class FakeGame:
    """Stands in for the game window: returns whatever the menu currently shows."""

    def __init__(self) -> None:
        self.selected: int | None = 0
        self.foreground = True
        self.grabs = 0

    def grab(self, region: Rect | None = None) -> CapturedFrame:
        self.grabs += 1
        return CapturedFrame(draw_menu(self.selected), Rect(0, 0, 640, 400), self.foreground,
                             0.001, "window")


class RecordingSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []

    def speak(
        self, text: str, *, remember: bool = True, on_start: Callable[[float], None] | None = None
    ) -> None:
        self.said.append(text)
        if on_start is not None:
            on_start(time.perf_counter())


@pytest.fixture(scope="module")
def engine() -> Iterator[OcrEngine]:
    netguard.install()
    try:
        yield OcrEngine()
    finally:
        netguard.uninstall()


def make_controller(
    engine: OcrEngine, tmp_path: Path
) -> tuple[FocusController, FakeGame, RecordingSpeaker, Path]:
    img = draw_menu(0)
    template = crop(img, Rect(CURSOR_X - 2, ROW_Y[0] - 30, 29, 29))
    profile = FocusProfile.model_validate({
        "name": "synthetic",
        "window": "Synthetic",
        "client_width": 640,
        "client_height": 400,
        "menu_region": {"left": 20, "top": 60, "width": 560, "height": 280},
        "strategy": {"kind": "cursor_template", "template_path": "local/x.png",
                     "row": {"dx": -6, "dy": -14, "width": 320, "height": 56}},
        # Fast timings: the fake game redraws instantly.
        "timing": {"initial_delay_s": 0.0, "frame_interval_s": 0.005,
                   "change_timeout_s": 0.05, "settle_timeout_s": 0.05},
    })
    log_path = tmp_path / "events.jsonl"
    speaker = RecordingSpeaker()
    controller = FocusController(LoadedProfile(profile, template), engine, speaker,
                                 EventLog(log_path))
    return controller, FakeGame(), speaker, log_path


def test_focus_mode_speaks_only_the_new_selection(engine: OcrEngine, tmp_path: Path) -> None:
    focus, game, speaker, log_path = make_controller(engine, tmp_path)

    focus.toggle(game, time.perf_counter())
    assert speaker.said == [f"{MSG_FOCUS_ON} Attack"]

    game.selected = 1  # player pressed Down
    focus.on_nav(game, time.perf_counter())
    assert speaker.said[-1] == "Defend"

    count = len(speaker.said)
    focus.on_nav(game, time.perf_counter())  # key did nothing (e.g. at a wall)
    assert len(speaker.said) == count  # nothing changed: stay quiet

    game.selected = 0  # back up: OCR result comes from the cache
    hits_before = focus.cache.hits
    focus.on_nav(game, time.perf_counter())
    assert speaker.said[-1] == "Attack"
    assert focus.cache.hits == hits_before + 1

    game.selected = None  # menu closed, walking around: silent, not "unclear"
    count = len(speaker.said)
    focus.on_nav(game, time.perf_counter())
    assert len(speaker.said) == count

    game.selected = 0  # menu reopened on the same item: announce it again
    focus.on_nav(game, time.perf_counter())
    assert speaker.said[-1] == "Attack"

    focus.toggle(game, time.perf_counter())
    assert speaker.said[-1] == MSG_FOCUS_OFF
    count = len(speaker.said)
    game.selected = 2
    focus.on_nav(game, time.perf_counter())  # off: ignores keys
    assert len(speaker.said) == count

    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    starts = [r for r in records if r["event"] == "speech_start"]
    assert starts and all(r["latency_ms"] >= 0 and "keypress_t" in r for r in starts)
    assert all("Attack" not in line for line in log_path.read_text().splitlines())


def test_keys_typed_into_another_app_are_ignored(engine: OcrEngine, tmp_path: Path) -> None:
    focus, game, speaker, _ = make_controller(engine, tmp_path)
    focus.toggle(game, time.perf_counter())
    game.foreground = False
    game.selected = 2
    count = len(speaker.said)
    focus.on_nav(game, time.perf_counter())
    assert len(speaker.said) == count


def test_two_cursors_say_selection_unclear(engine: OcrEngine, tmp_path: Path) -> None:
    focus, game, speaker, _ = make_controller(engine, tmp_path)
    focus.toggle(game, time.perf_counter())

    original = game.grab

    def two_cursors(region: Rect | None = None) -> CapturedFrame:
        captured = original(region)
        image = captured.image.copy()
        draw_cursor(image, CURSOR_X, ROW_Y[2] - 28)
        return CapturedFrame(image, captured.screen_rect, True, 0.001, "window")

    game.grab = two_cursors  # type: ignore[method-assign]
    focus.on_nav(game, time.perf_counter())
    assert speaker.said[-1] == MSG_SELECTION_UNCLEAR
