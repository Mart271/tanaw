from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from tanaw.app import (
    MSG_NO_TEXT,
    MSG_UNCLEAR,
    DebugCaptureStore,
    ReadRequest,
    Worker,
)
from tanaw.capture import CapturedFrame, CaptureError
from tanaw.events import EventLog
from tanaw.frames import Frame, Rect
from tanaw.ocr import OcrResult, TextBox
from tanaw.settings import AppSettings


def frame(foreground: bool = True) -> CapturedFrame:
    image: Frame = np.zeros((48, 64, 3), dtype=np.uint8)
    return CapturedFrame(image, Rect(0, 0, 64, 48), foreground, 0.01, "window")


class FakeSource:
    def __init__(self, captured: CapturedFrame | Exception) -> None:
        self.captured = captured
        self.grabs = 0
        self.closed = False

    def grab_settled(
        self, region: Rect | None = None, *, timeout_s: float = 0.3
    ) -> tuple[CapturedFrame, bool]:
        self.grabs += 1
        if isinstance(self.captured, Exception):
            raise self.captured
        return self.captured, True

    def close(self) -> None:
        self.closed = True


class FakeReader:
    def __init__(self, result: OcrResult) -> None:
        self.result = result

    def read(self, image: Frame, *, upscale: float | None = None) -> OcrResult:
        return self.result


class FakeSpeaker:
    def __init__(self) -> None:
        self.said: list[tuple[str, bool]] = []

    def speak(self, text: str, *, remember: bool = True) -> None:
        self.said.append((text, remember))


def ocr(boxes: list[TextBox], low: int = 0) -> OcrResult:
    return OcrResult(boxes, low, 0.05, (64, 48))


SETTINGS = AppSettings(window="DELTARUNE")


def make_worker(
    reader: FakeReader, speaker: FakeSpeaker, tmp_path: Path, events: EventLog | None = None
) -> Worker:
    return Worker(SETTINGS, lambda: FakeSource(frame()), reader, speaker, events,
                  DebugCaptureStore(False, tmp_path / "debug"))


def test_read_speaks_text_in_reading_order(tmp_path: Path) -> None:
    boxes = [
        TextBox("ACT", 0.9, Rect(300, 200, 60, 20)),
        TextBox("FIGHT", 0.9, Rect(10, 200, 60, 20)),
        TextBox("Kris", 0.9, Rect(10, 10, 60, 20)),
    ]
    speaker = FakeSpeaker()
    make_worker(FakeReader(ocr(boxes)), speaker, tmp_path).handle_read(
        FakeSource(frame()), ReadRequest(time.perf_counter())
    )
    assert speaker.said == [("Kris. FIGHT, ACT.", True)]


def test_read_works_when_game_is_covered_but_captured_directly(tmp_path: Path) -> None:
    speaker = FakeSpeaker()
    boxes = [TextBox("Yes", 1.0, Rect(10, 10, 60, 20))]
    worker = make_worker(FakeReader(ocr(boxes)), speaker, tmp_path)
    worker.handle_read(FakeSource(frame(foreground=False)), ReadRequest(time.perf_counter()))
    assert speaker.said == [("Yes.", True)]


@pytest.mark.parametrize(("low", "message"), [(0, MSG_NO_TEXT), (3, MSG_UNCLEAR)])
def test_read_says_when_nothing_readable(tmp_path: Path, low: int, message: str) -> None:
    speaker = FakeSpeaker()
    make_worker(FakeReader(ocr([], low)), speaker, tmp_path).handle_read(
        FakeSource(frame()), ReadRequest(time.perf_counter())
    )
    assert speaker.said == [(message, False)]


def test_capture_error_is_spoken_not_raised(tmp_path: Path) -> None:
    speaker = FakeSpeaker()
    worker = make_worker(FakeReader(ocr([])), speaker, tmp_path)
    worker.handle_read(FakeSource(CaptureError("The game window was closed.")),
                       ReadRequest(time.perf_counter()))
    assert speaker.said == [("The game window was closed.", False)]


def test_event_log_gets_metadata_but_no_text(tmp_path: Path) -> None:
    speaker = FakeSpeaker()
    log = EventLog(tmp_path / "logs" / "s.jsonl")
    boxes = [TextBox("SECRET MENU TEXT", 0.9, Rect(10, 10, 200, 20))]
    make_worker(FakeReader(ocr(boxes)), speaker, tmp_path, log).handle_read(
        FakeSource(frame()), ReadRequest(time.perf_counter())
    )
    log.close()
    content = (tmp_path / "logs" / "s.jsonl").read_text(encoding="utf-8")
    assert '"event":"read"' in content
    assert '"boxes":1' in content
    assert "SECRET" not in content


def test_worker_thread_coalesces_queued_reads(tmp_path: Path) -> None:
    source = FakeSource(frame())
    speaker = FakeSpeaker()
    worker = Worker(SETTINGS, lambda: source, FakeReader(ocr([])), speaker, None,
                    DebugCaptureStore(False, tmp_path / "debug"))
    for _ in range(5):  # queued before the thread starts: should become one read
        worker.submit(ReadRequest(time.perf_counter()))
    worker.start()
    worker.stop()
    assert source.grabs == 1
    assert source.closed


def test_debug_captures_off_writes_nothing(tmp_path: Path) -> None:
    store = DebugCaptureStore(False, tmp_path / "debug")
    store.save(frame(), "read")
    assert not (tmp_path / "debug").exists()


def test_debug_captures_are_deleted_on_cleanup(tmp_path: Path) -> None:
    store = DebugCaptureStore(True, tmp_path / "debug")
    store.save(frame(), "read")
    store.save(frame(), "read")
    assert len(list((tmp_path / "debug").glob("*.png"))) == 2
    assert store.cleanup() == 2
    assert not (tmp_path / "debug").exists()
