"""Wires everything together: hotkeys -> worker thread -> capture/OCR -> speech.

Speech controls (stop, repeat, pause) go straight from the key listener to the
speaker's own queue, so "stop" works instantly even while OCR is busy. Anything
that captures or runs OCR goes through the worker queue.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2

from tanaw import netguard
from tanaw.capture import (
    CapturedFrame,
    CaptureError,
    WindowCapturer,
    WindowInfo,
    enable_dpi_awareness,
    find_window,
)
from tanaw.events import EventLog
from tanaw.frames import Frame, Rect
from tanaw.hotkeys import Action, HotkeyListener
from tanaw.layout import group_lines, speech_text
from tanaw.ocr import OcrEngine, OcrError, OcrResult
from tanaw.settings import AppSettings
from tanaw.speech import SapiVoice, Speaker, SpeechError

logger = logging.getLogger(__name__)

MSG_NO_TEXT = "No text found."
MSG_UNCLEAR = "I can't read that clearly."
MSG_UNEXPECTED = "Something went wrong reading the screen."

DEBUG_DIR = Path("debug")


# --- narrow interfaces so the worker can be tested without a screen or models ----------


class FrameSource(Protocol):
    def grab_settled(
        self, region: Rect | None = None, *, timeout_s: float = 0.3
    ) -> tuple[CapturedFrame, bool]: ...

    def close(self) -> None: ...


class TextReader(Protocol):
    def read(self, image: Frame, *, upscale: float | None = None) -> OcrResult: ...


class Announcer(Protocol):
    def speak(self, text: str, *, remember: bool = True) -> None: ...


class DebugCaptureStore:
    """Writes frames only when --debug-captures is on; deletes them on clean exit."""

    def __init__(self, enabled: bool, directory: Path = DEBUG_DIR) -> None:
        self.enabled = enabled
        self.directory = directory
        self._written: list[Path] = []
        self._lock = threading.Lock()

    def save(self, captured: CapturedFrame, label: str) -> None:
        if not self.enabled:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.perf_counter_ns()}"
        path = self.directory / f"{label}-{stamp}.png"
        if cv2.imwrite(str(path), captured.image):
            with self._lock:
                self._written.append(path)

    def cleanup(self) -> int:
        with self._lock:
            removed = 0
            for path in self._written:
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    logger.warning("could not delete a debug capture")
            self._written.clear()
        try:
            if self.directory.exists() and not any(self.directory.iterdir()):
                self.directory.rmdir()
        except OSError:
            pass
        return removed


# --- worker ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadRequest:
    pressed_at: float  # time.perf_counter() when the hotkey went down


class _StopWorker:
    pass


WorkItem = ReadRequest | _StopWorker


class Worker:
    """Runs capture + OCR off the listener thread. Owns its FrameSource."""

    def __init__(
        self,
        settings: AppSettings,
        source_factory: Callable[[], FrameSource],
        reader: TextReader,
        speaker: Announcer,
        events: EventLog | None,
        debug_store: DebugCaptureStore,
    ) -> None:
        self.settings = settings
        self._source_factory = source_factory
        self._reader = reader
        self._speaker = speaker
        self._events = events
        self._debug = debug_store
        self.queue: queue.Queue[WorkItem] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="tanaw-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        if self._thread.is_alive():
            self.queue.put(_StopWorker())
            self._thread.join(timeout_s)

    def submit(self, item: WorkItem) -> None:
        self.queue.put(item)

    def _event(self, name: str, **fields: int | float | bool | str | None) -> None:
        if self._events is not None:
            self._events.write(name, **fields)

    def _run(self) -> None:
        try:
            source = self._source_factory()
        except Exception:
            logger.exception("worker could not open the capture source")
            self._speaker.speak("Could not start screen capture.", remember=False)
            return
        try:
            while True:
                item = self.queue.get()
                if isinstance(item, _StopWorker):
                    break
                item, stop = self._coalesce(item)
                self.handle_read(source, item)
                if stop:
                    break
        finally:
            source.close()

    def _coalesce(self, item: ReadRequest) -> tuple[ReadRequest, bool]:
        """If the player pressed Read several times while we were busy, read once."""
        latest = item
        stop = False
        while True:
            try:
                extra = self.queue.get_nowait()
            except queue.Empty:
                return latest, stop
            if isinstance(extra, _StopWorker):
                stop = True
            else:
                latest = extra

    def handle_read(self, source: FrameSource, request: ReadRequest) -> None:
        try:
            # The capturer refuses (CaptureError) rather than return another app's pixels.
            captured, settled = source.grab_settled(timeout_s=self.settings.settle_timeout_s)
            self._debug.save(captured, "read")
            result = self._reader.read(captured.image)
        except (CaptureError, OcrError) as exc:
            self._speaker.speak(str(exc), remember=False)
            self._event("error", mode="read", kind=type(exc).__name__)
            return
        except Exception:
            logger.exception("read failed")
            self._speaker.speak(MSG_UNEXPECTED, remember=False)
            self._event("error", mode="read", kind="unexpected")
            return

        lines = group_lines(result.boxes)
        text = speech_text(lines)
        if text:
            self._speaker.speak(text)
        elif result.low_confidence:
            self._speaker.speak(MSG_UNCLEAR, remember=False)
        else:
            self._speaker.speak(MSG_NO_TEXT, remember=False)

        latency_ms = (time.perf_counter() - request.pressed_at) * 1000
        width, height = result.image_size
        self._event(
            "read",
            width=width, height=height, settled=settled, source=captured.source,
            foreground=captured.foreground,
            capture_ms=captured.elapsed_s * 1000, ocr_ms=result.elapsed_s * 1000,
            boxes=len(result.boxes), low_conf=result.low_confidence, lines=len(lines),
            chars=len(text), hotkey_to_speak_call_ms=latency_ms,
        )
        logger.info(
            "read: %d boxes (%d low-confidence), %d lines, %d chars, ocr %.0f ms, "
            "hotkey->speak call %.0f ms",
            len(result.boxes), result.low_confidence, len(lines), len(text),
            result.elapsed_s * 1000, latency_ms,
        )
        if self.settings.verbose_text:
            logger.info("read text: %r", text)


# --- application -------------------------------------------------------------------------


def _fail(speaker: Speaker | None, message: str) -> int:
    print(f"Error: {message}")
    if speaker is not None:
        speaker.speak(message, remember=False)
        speaker.flush(timeout_s=10.0, wait_for_audio_ms=8000)
    return 1


def run(settings: AppSettings) -> int:
    """Start Tanaw Read Mode. Blocks until Ctrl+C. Returns a process exit code."""
    netguard.install()
    dpi_mode = enable_dpi_awareness()
    events = EventLog.for_new_session()
    events.write("start", dpi=dpi_mode.replace("(", "_").replace(")", ""),
                 debug_captures=settings.debug_captures, verbose_text=settings.verbose_text)

    speaker = Speaker(lambda: SapiVoice(rate=settings.speech_rate), log_text=settings.verbose_text)
    try:
        speaker.start()
    except SpeechError as exc:
        events.write("error", mode="startup", kind="speech")
        events.close()
        return _fail(None, str(exc))

    debug_store = DebugCaptureStore(settings.debug_captures)
    hotkeys: HotkeyListener | None = None
    worker: Worker | None = None
    try:
        try:
            window: WindowInfo = find_window(settings.window)
        except (CaptureError, ValueError) as exc:
            events.write("error", mode="startup", kind=type(exc).__name__)
            return _fail(speaker, str(exc))
        print(f'Game window: "{window.title}"  (DPI awareness: {dpi_mode})')

        speaker.speak("Tanaw is loading.", remember=False)
        t0 = time.perf_counter()
        engine = OcrEngine(
            min_confidence=settings.min_confidence,
            upscale=settings.upscale,
            interpolation=settings.interpolation,
        )
        warm_s = engine.warm_up()
        load_s = time.perf_counter() - t0
        events.write("ocr_ready", load_ms=load_s * 1000, warm_ms=warm_s * 1000)
        print(f"OCR models loaded in {load_s:.1f} s")

        worker = Worker(
            settings,
            source_factory=lambda: WindowCapturer(window, settings.capture_method),
            reader=engine,
            speaker=speaker,
            events=events,
            debug_store=debug_store,
        )
        worker.start()

        active_worker = worker

        def on_action(action: Action, pressed_at: float) -> None:
            # Runs on the key-listener thread: only enqueue, never block.
            if action is Action.READ:
                active_worker.submit(ReadRequest(pressed_at))
            elif action is Action.STOP:
                speaker.stop()
            elif action is Action.REPEAT:
                speaker.repeat_last()
            elif action is Action.PAUSE:
                speaker.toggle_pause()

        bindings = settings.hotkeys.bindings()
        hotkeys = HotkeyListener(bindings, on_action)
        hotkeys.start()
        read_key = bindings[Action.READ].describe()
        speaker.speak(f"Tanaw ready. Press {read_key.replace('+', ' ')} to read the screen.",
                      remember=False)
        print("Ready. Hotkeys:")
        for action, hotkey in bindings.items():
            print(f"  {hotkey.describe():<16} {action.value}")
        print("Press Ctrl+C here to quit.")
        events.write("ready")

        stop = threading.Event()
        try:
            while not stop.wait(0.5):  # short waits keep Ctrl+C responsive on Windows
                pass
        except KeyboardInterrupt:
            print("\nStopping...")
        return 0
    except OcrError as exc:
        events.write("error", mode="startup", kind="OcrError")
        return _fail(speaker, str(exc))
    finally:
        if hotkeys is not None:
            hotkeys.stop()
        if worker is not None:
            worker.stop()
        speaker.close()
        removed = debug_store.cleanup()
        events.write("stop", debug_captures_deleted=removed)
        events.close()
