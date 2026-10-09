"""Focus Mode at runtime (runs on the worker thread).

nav keypress -> short delay -> capture until the menu area changes and settles
-> locate the selection (profile strategy) -> OCR just that row (cached by crop
hash) -> speak it only if it changed and OCR is confident, else "Selection
unclear." No cursor on screen (e.g. walking around) -> stay silent.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from tanaw.capture import CapturedFrame, CaptureError
from tanaw.events import EventLog
from tanaw.focus import (
    MSG_SELECTION_UNCLEAR,
    Decision,
    LocateStatus,
    OcrCache,
    ProfileMismatchError,
    Reading,
    crop,
    decide,
    image_key,
    locate_selection,
    normalize_frame,
    prepare_row_for_ocr,
    selected_row_image,
)
from tanaw.frames import Frame, Rect, settle_after_change
from tanaw.layout import group_lines
from tanaw.ocr import OcrError, OcrResult
from tanaw.profile import LoadedProfile

logger = logging.getLogger(__name__)

MSG_FOCUS_ON = "Focus mode on."
MSG_FOCUS_OFF = "Focus mode off."
MSG_NO_PROFILE = "Focus mode needs a calibrated profile. Start Tanaw with dash dash profile."

SpeechStartCallback = Callable[[float], None]


class FrameGrabber(Protocol):
    def grab(self, region: Rect | None = None) -> CapturedFrame: ...


class RowReader(Protocol):
    def read(
        self, image: Frame, *, upscale: float | None = None, min_confidence: float | None = None
    ) -> OcrResult: ...


class FocusSpeaker(Protocol):
    def speak(
        self, text: str, *, remember: bool = True, on_start: SpeechStartCallback | None = None
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class FocusOutcome:
    decision: Decision | None  # None when skipped before deciding
    reason: str  # spoken / same / unclear / silent / no_change / not_foreground / error
    spoken: str | None


class FocusController:
    def __init__(
        self,
        loaded: LoadedProfile,
        reader: RowReader,
        speaker: FocusSpeaker,
        events: EventLog | None,
        *,
        verbose_text: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.profile = loaded.profile
        self._template = loaded.template
        self._reader = reader
        self._speaker = speaker
        self._events = events
        self._verbose_text = verbose_text
        self._sleep = sleep
        self.enabled = False
        self.cache = OcrCache()
        self._reference: Frame | None = None  # last stable menu-region image
        self._last_text: str | None = None  # last selection we spoke
        self._region = self.profile.menu_region.to_rect()

    # -- public, called by the worker ---------------------------------------------------

    def toggle(self, source: FrameGrabber, pressed_at: float) -> FocusOutcome:
        if self.enabled:
            self.enabled = False
            self._reference = None
            self._last_text = None
            self._speaker.speak(MSG_FOCUS_OFF, remember=False)
            self._event("focus_mode", enabled=False)
            return FocusOutcome(None, "off", MSG_FOCUS_OFF)
        self.enabled = True
        self._reference = None
        self._last_text = None
        self._event("focus_mode", enabled=True)
        return self._update(source, pressed_at, announce_prefix=MSG_FOCUS_ON)

    def on_nav(self, source: FrameGrabber, pressed_at: float) -> FocusOutcome | None:
        if not self.enabled:
            return None
        self._sleep(self.profile.timing.initial_delay_s)  # let the game process the key
        return self._update(source, pressed_at, announce_prefix=None)

    # -- internals ----------------------------------------------------------------------

    def _event(self, name: str, **fields: int | float | bool | str | None) -> None:
        if self._events is not None:
            self._events.write(name, **fields)

    def _grab_region(self, source: FrameGrabber, foreground: list[bool]) -> Frame:
        captured = source.grab()
        foreground[0] = captured.foreground
        frame = normalize_frame(captured.image, self.profile.client_width,
                                self.profile.client_height)
        return crop(frame, self._region)

    def _read_row(self, row: Frame) -> tuple[Reading, bool, float]:
        options = self.profile.ocr
        prepared = prepare_row_for_ocr(row, options)
        key = image_key(prepared, options.upscale, options.min_confidence)
        cached = self.cache.get(key)
        if cached is not None:
            return cached, True, 0.0
        result = self._reader.read(prepared, upscale=options.upscale,
                                   min_confidence=options.min_confidence)
        text = " ".join(line.text() for line in group_lines(result.boxes)).strip()
        confidence = min((b.confidence for b in result.boxes), default=0.0)
        reading = Reading(text, confidence, result.low_confidence)
        self.cache.put(key, reading)
        return reading, False, result.elapsed_s

    def _update(
        self, source: FrameGrabber, pressed_at: float, *, announce_prefix: str | None
    ) -> FocusOutcome:
        timing = self.profile.timing
        foreground = [True]
        try:
            change = settle_after_change(
                lambda: self._grab_region(source, foreground),
                self._reference,
                change_threshold=timing.change_threshold,
                change_timeout_s=timing.change_timeout_s,
                settle_timeout_s=timing.settle_timeout_s,
                interval_s=timing.frame_interval_s,
            )
            if announce_prefix is None and not foreground[0]:
                # Nav keys typed into another app aren't game navigation.
                self._event("focus", outcome="not_foreground", keypress_t=pressed_at)
                return FocusOutcome(None, "not_foreground", None)
            self._reference = change.frame
            if not change.changed:
                self._event("focus", outcome="no_change", frames=change.frames_grabbed,
                            settle_ms=change.elapsed_s * 1000, keypress_t=pressed_at)
                return FocusOutcome(None, "no_change", None)

            located = locate_selection(change.frame, self.profile, self._template)
            reading: Reading | None = None
            cached = False
            ocr_s = 0.0
            if located.status is LocateStatus.FOUND and located.row is not None:
                reading, cached, ocr_s = self._read_row(selected_row_image(change.frame, located))
        except (CaptureError, ProfileMismatchError, OcrError) as exc:
            self._speaker.speak(str(exc), remember=False)
            self._event("error", mode="focus", kind=type(exc).__name__)
            return FocusOutcome(None, "error", str(exc))

        decision = decide(located, reading, self._last_text, self.profile.ocr.min_confidence)
        say: str | None = None
        remember = False
        if decision is Decision.SPEAK and reading is not None:
            self._last_text = reading.text
            say, remember = reading.text, True
        elif decision is Decision.UNCLEAR:
            self._last_text = None
            say = MSG_SELECTION_UNCLEAR
        elif decision is Decision.SILENT:
            self._last_text = None  # menu closed: announce the selection again when it reopens
        if announce_prefix is not None:
            say = f"{announce_prefix} {say}" if say else announce_prefix

        if say is not None:
            self._speaker.speak(say, remember=remember,
                                on_start=self._speech_start_logger(pressed_at, decision))
        self._event(
            "focus",
            outcome=decision.value,
            locate=located.status.value,
            score=located.score,
            cached=cached,
            ocr_ms=ocr_s * 1000,
            confidence=reading.confidence if reading else None,
            frames=change.frames_grabbed,
            settled=change.settled,
            settle_ms=change.elapsed_s * 1000,
            keypress_t=pressed_at,
            hotkey_to_speak_call_ms=(time.perf_counter() - pressed_at) * 1000,
        )
        if self._verbose_text and reading is not None:
            logger.info("focus read %r (%.2f)", reading.text, reading.confidence)
        return FocusOutcome(decision, decision.value, say)

    def _speech_start_logger(
        self, pressed_at: float, decision: Decision
    ) -> SpeechStartCallback | None:
        if self._events is None:
            return None
        events = self._events

        def on_start(started_at: float) -> None:
            events.write("speech_start", mode="focus", outcome=decision.value,
                         keypress_t=pressed_at, speech_start_t=started_at,
                         latency_ms=(started_at - pressed_at) * 1000)

        return on_start
