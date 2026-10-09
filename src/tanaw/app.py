"""Wires everything together: hotkeys -> worker thread -> capture/OCR -> speech.

Speech controls (stop, repeat, pause) go straight from the key listener to the
speaker's own queue, so "stop" works instantly even while OCR is busy. Anything
that captures or runs OCR (Read Mode, Focus Mode) goes through the worker queue,
where bursts of the same request are merged (latest wins).
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
from tanaw.focus_mode import MSG_NO_PROFILE, FocusController
from tanaw.frames import Frame, Rect
from tanaw.hotkeys import Action, HotkeyListener
from tanaw.layout import group_lines, speech_text
from tanaw.ocr import OcrEngine, OcrError, OcrResult
from tanaw.profile import LoadedProfile, ProfileError, load_profile
from tanaw.settings import AppSettings
from tanaw.speech import SapiVoice, Speaker, SpeechError, SpeechStartCallback

logger = logging.getLogger(__name__)

MSG_NO_TEXT = "No text found."
MSG_UNCLEAR = "I can't read that clearly."
MSG_UNEXPECTED = "Something went wrong reading the screen."

DEBUG_DIR = Path("debug")


# --- narrow interfaces so the worker can be tested without a screen or models ----------


class FrameSource(Protocol):
    def grab(self, region: Rect | None = None) -> CapturedFrame: ...

    def grab_settled(
        self, region: Rect | None = None, *, timeout_s: float = 0.3
    ) -> tuple[CapturedFrame, bool]: ...

    def close(self) -> None: ...


class TextReader(Protocol):
    def read(
        self, image: Frame, *, upscale: float | None = None, min_confidence: float | None = None
    ) -> OcrResult: ...


class Announcer(Protocol):
    def speak(
        self, text: str, *, remember: bool = True, on_start: SpeechStartCallback | None = None
    ) -> None: ...


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


@dataclass(frozen=True, slots=True)
class FocusToggleRequest:
    pressed_at: float


@dataclass(frozen=True, slots=True)
class NavRequest:
    pressed_at: float  # a game navigation key (arrow, confirm, cancel)


class _StopWorker:
    pass


WorkItem = ReadRequest | FocusToggleRequest | NavRequest | _StopWorker


def coalesce(items: list[WorkItem]) -> list[WorkItem]:
    """Merge runs of the same request: holding an arrow sends many NavRequests, but
    only the latest state of the menu matters. Toggles are never merged.
    Anything after a stop request is dropped."""
    out: list[WorkItem] = []
    for item in items:
        if isinstance(item, _StopWorker):
            out.append(item)
            break
        if out and type(out[-1]) is type(item) and isinstance(item, ReadRequest | NavRequest):
            out[-1] = item  # latest wins
        else:
            out.append(item)
    return out


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
        focus: FocusController | None = None,
    ) -> None:
        self.settings = settings
        self.focus = focus
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
                batch = [self.queue.get()]
                while True:  # take everything that piled up while we were busy
                    try:
                        batch.append(self.queue.get_nowait())
                    except queue.Empty:
                        break
                for item in coalesce(batch):
                    if isinstance(item, _StopWorker):
                        return
                    self._handle(source, item)
        finally:
            source.close()

    def _handle(self, source: FrameSource, item: WorkItem) -> None:
        try:
            if isinstance(item, ReadRequest):
                self.handle_read(source, item)
            elif isinstance(item, FocusToggleRequest):
                if self.focus is None:
                    self._speaker.speak(MSG_NO_PROFILE, remember=False)
                else:
                    self.focus.toggle(source, item.pressed_at)
            elif isinstance(item, NavRequest) and self.focus is not None:
                self.focus.on_nav(source, item.pressed_at)
        except Exception:
            logger.exception("worker item failed: %s", type(item).__name__)
            self._speaker.speak(MSG_UNEXPECTED, remember=False)
            self._event("error", mode=type(item).__name__, kind="unexpected")

    def _speech_start_logger(self, mode: str, pressed_at: float) -> SpeechStartCallback | None:
        if self._events is None:
            return None
        events = self._events

        def on_start(started_at: float) -> None:
            events.write("speech_start", mode=mode, keypress_t=pressed_at,
                         speech_start_t=started_at, latency_ms=(started_at - pressed_at) * 1000)

        return on_start

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
        on_start = self._speech_start_logger("read", request.pressed_at)
        if text:
            self._speaker.speak(text, on_start=on_start)
        elif result.low_confidence:
            self._speaker.speak(MSG_UNCLEAR, remember=False, on_start=on_start)
        else:
            self._speaker.speak(MSG_NO_TEXT, remember=False, on_start=on_start)

        latency_ms = (time.perf_counter() - request.pressed_at) * 1000
        width, height = result.image_size
        self._event(
            "read",
            width=width, height=height, settled=settled, source=captured.source,
            foreground=captured.foreground,
            capture_ms=captured.elapsed_s * 1000, ocr_ms=result.elapsed_s * 1000,
            boxes=len(result.boxes), low_conf=result.low_confidence, lines=len(lines),
            chars=len(text), keypress_t=request.pressed_at, hotkey_to_speak_call_ms=latency_ms,
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
    """Start Tanaw (Read Mode, plus Focus Mode with a profile). Blocks until Ctrl+C."""
    netguard.install()
    dpi_mode = enable_dpi_awareness()
    events = EventLog.for_new_session()
    events.write("start", dpi=dpi_mode.replace("(", "_").replace(")", ""),
                 debug_captures=settings.debug_captures, verbose_text=settings.verbose_text)
    # Deliberately try to reach a public address; the guard must block it before any
    # packet is sent. If it doesn't, refuse to run (fail closed).
    guard_ok = netguard.self_test()
    events.write("netguard_selftest", passed=guard_ok,
                 target=":".join(str(p) for p in netguard.SELF_TEST_ADDRESS))
    print(f"Network guard self-test (connect to {netguard.SELF_TEST_ADDRESS[0]}): "
          f"{'PASS - blocked' if guard_ok else 'FAIL'}")
    if not guard_ok:
        events.close()
        return _fail(None, "The network guard did not block a test connection. Not starting.")

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
        loaded: LoadedProfile | None = None
        if settings.profile is not None:
            try:
                loaded = load_profile(settings.profile)
            except ProfileError as exc:
                events.write("error", mode="startup", kind="ProfileError")
                return _fail(speaker, str(exc))
            print(f"Profile: {loaded.profile.name} ({loaded.profile.strategy.kind})")
            events.write("profile", name=loaded.profile.name,
                         strategy=loaded.profile.strategy.kind)
        window_query = settings.window or (loaded.profile.window if loaded else None)
        if window_query is None:  # settings validation guarantees one of the two
            return _fail(speaker, "No game window given.")
        try:
            window: WindowInfo = find_window(window_query)
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

        focus = (FocusController(loaded, engine, speaker, events,
                                 verbose_text=settings.verbose_text)
                 if loaded is not None else None)
        worker = Worker(
            settings,
            source_factory=lambda: WindowCapturer(window, settings.capture_method),
            reader=engine,
            speaker=speaker,
            events=events,
            debug_store=debug_store,
            focus=focus,
        )
        worker.start()

        active_worker = worker

        def on_action(action: Action, pressed_at: float) -> None:
            # Runs on the key-listener thread: only enqueue, never block.
            if action is Action.READ:
                active_worker.submit(ReadRequest(pressed_at))
            elif action is Action.NAV:
                active_worker.submit(NavRequest(pressed_at))
            elif action is Action.FOCUS_TOGGLE:
                active_worker.submit(FocusToggleRequest(pressed_at))
            elif action is Action.STOP:
                speaker.stop()
            elif action is Action.REPEAT:
                speaker.repeat_last()
            elif action is Action.PAUSE:
                speaker.toggle_pause()

        bindings = settings.hotkeys.bindings()
        nav_vks = loaded.profile.nav_vks() if loaded is not None else frozenset[int]()
        hotkeys = HotkeyListener(bindings, on_action, nav_vks)
        hotkeys.start()
        read_key = bindings[Action.READ].describe().replace("+", " ")
        focus_key = bindings[Action.FOCUS_TOGGLE].describe().replace("+", " ")
        ready = f"Tanaw ready. {read_key} reads the screen."
        if loaded is not None:
            ready += f" {focus_key} turns on focus mode."
        speaker.speak(ready, remember=False)
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
