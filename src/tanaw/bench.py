"""Benchmark logic for scripts/bench_*.py (kept here so it is typed and unit-tested).

Honesty rules (CLAUDE.md §11): these functions only compute and print what was
measured. Sample counts are always reported next to every number.

Labels file format (``fixtures/labels.json``; image paths are relative to it)::

    {
      "profile": "../profiles/deltarune.json",
      "focus": [
        {"image": "battle-fight.png", "expected": "FIGHT"},
        {"image": "overworld.png", "expected": null}
      ],
      "ocr": [
        {"image": "dialogue-1.png", "expected": "The air crackles with freedom."},
        {"image": "status.png", "rect": [40, 600, 500, 60], "expected": "HP 120/160"}
      ]
    }

``focus.expected`` is the text Focus Mode should say for that screenshot, or
null if no menu is open and it should stay silent.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from tanaw.focus import (
    MSG_SELECTION_UNCLEAR,
    Decision,
    LocateStatus,
    Reading,
    crop,
    decide,
    locate_selection,
    normalize_frame,
    prepare_row_for_ocr,
    selected_row_image,
)
from tanaw.frames import Frame, Rect
from tanaw.layout import group_lines
from tanaw.ocr import OcrEngine
from tanaw.profile import LoadedProfile

# --- pure metrics -------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def levenshtein(a: str, b: str) -> int:
    """Edit distance (insertions, deletions, substitutions)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def character_error_rate(expected: str, got: str) -> float:
    """Edits needed / expected length (can exceed 1.0 for very wrong output)."""
    exp, out = normalize_text(expected), normalize_text(got)
    if not exp:
        return 0.0 if not out else 1.0
    return levenshtein(exp, out) / len(exp)


@dataclass(frozen=True, slots=True)
class Summary:
    count: int
    median: float | None
    p90: float | None  # only when count >= 10
    worst: float | None
    best: float | None


def summarize(values: Sequence[float]) -> Summary:
    if not values:
        return Summary(0, None, None, None, None)
    ordered = sorted(values)
    p90 = None
    if len(ordered) >= 10:
        # nearest-rank 90th percentile
        p90 = ordered[max(0, -(-9 * len(ordered) // 10) - 1)]
    return Summary(len(ordered), statistics.median(ordered), p90, ordered[-1], ordered[0])


# --- latency (event log) ------------------------------------------------------------------


def load_events(paths: Iterable[Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a crash can leave a half-written last line
            if isinstance(record, dict):
                records.append(record)
    return records


def speech_latencies(records: Iterable[dict[str, object]]) -> dict[str, list[float]]:
    """keypress -> speech-start latencies (ms) grouped by mode ("read", "focus")."""
    by_mode: dict[str, list[float]] = {}
    for record in records:
        if record.get("event") != "speech_start":
            continue
        latency = record.get("latency_ms")
        mode = record.get("mode")
        if isinstance(latency, int | float) and isinstance(mode, str) and latency >= 0:
            by_mode.setdefault(mode, []).append(float(latency))
    return by_mode


# --- labels -------------------------------------------------------------------------------


class FocusLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    image: str = Field(min_length=1)
    expected: str | None  # None: no menu open, Focus Mode should stay silent


class OcrLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    image: str = Field(min_length=1)
    expected: str
    rect: tuple[int, int, int, int] | None = None  # left, top, width, height


class Labels(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    profile: str | None = None
    focus: list[FocusLabel] = []
    ocr: list[OcrLabel] = []


def load_labels(path: Path) -> Labels:
    return Labels.model_validate_json(path.read_text(encoding="utf-8"))


def read_image(path: Path) -> Frame:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"could not read image: {path}")
    out: Frame = np.ascontiguousarray(image, dtype=np.uint8)
    return out


# --- Focus Mode selection accuracy --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FocusCase:
    image: str
    expected: str | None
    spoken: str | None  # what Focus Mode would say (None = silent)
    outcome: str  # correct / wrong / unclear / missed / false_alarm
    locate: str
    confidence: float | None
    ms: float


def classify_focus(expected: str | None, decision: Decision, text: str | None) -> str:
    """correct: right text, or silent when no menu. wrong: confidently said the wrong
    thing (the harmful case). unclear: said "Selection unclear" (honest abstain).
    missed: silent although a menu item was selected. false_alarm: spoke with no menu."""
    if expected is None:
        if decision is Decision.SILENT:
            return "correct"
        return "false_alarm"
    if decision is Decision.SILENT:
        return "missed"
    if decision is Decision.UNCLEAR:
        return "unclear"
    if text is not None and normalize_text(text) == normalize_text(expected):
        return "correct"
    return "wrong"


def run_focus_case(engine: OcrEngine, loaded: LoadedProfile, image: Frame,
                   label: FocusLabel) -> FocusCase:
    profile = loaded.profile
    start = time.perf_counter()
    frame = normalize_frame(image, profile.client_width, profile.client_height)
    region = crop(frame, profile.menu_region.to_rect())
    located = locate_selection(region, profile, loaded.template)
    reading: Reading | None = None
    if located.status is LocateStatus.FOUND:
        prepared = prepare_row_for_ocr(selected_row_image(region, located), profile.ocr)
        result = engine.read(prepared, upscale=profile.ocr.upscale,
                             min_confidence=profile.ocr.min_confidence)
        text = " ".join(line.text() for line in group_lines(result.boxes)).strip()
        confidence = min((b.confidence for b in result.boxes), default=0.0)
        reading = Reading(text, confidence, result.low_confidence)
    decision = decide(located, reading, None, profile.ocr.min_confidence)
    elapsed_ms = (time.perf_counter() - start) * 1000
    spoken: str | None = None
    if decision is Decision.SPEAK and reading is not None:
        spoken = reading.text
    elif decision is Decision.UNCLEAR:
        spoken = MSG_SELECTION_UNCLEAR
    return FocusCase(
        image=label.image,
        expected=label.expected,
        spoken=spoken,
        outcome=classify_focus(label.expected, decision, reading.text if reading else None),
        locate=located.status.value,
        confidence=reading.confidence if reading else None,
        ms=elapsed_ms,
    )


# --- OCR error rate -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OcrCase:
    image: str
    expected: str
    got: str
    exact: bool
    cer: float
    low_confidence_boxes: int  # boxes dropped below the confidence threshold
    boxes: int
    ms: float


def run_ocr_case(engine: OcrEngine, image: Frame, label: OcrLabel, *,
                 upscale: float, min_confidence: float) -> OcrCase:
    target = image
    if label.rect is not None:
        target = crop(image, Rect(*label.rect))
    result = engine.read(target, upscale=upscale, min_confidence=min_confidence)
    got = " ".join(line.text() for line in group_lines(result.boxes))
    return OcrCase(
        image=label.image,
        expected=label.expected,
        got=got,
        exact=normalize_text(got) == normalize_text(label.expected),
        cer=character_error_rate(label.expected, got),
        low_confidence_boxes=result.low_confidence,
        boxes=len(result.boxes),
        ms=result.elapsed_s * 1000,
    )


def format_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"
