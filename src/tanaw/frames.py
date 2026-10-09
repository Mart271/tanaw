"""Frame types and pure frame logic (no screen or OS access).

Kept separate from :mod:`tanaw.capture` so the logic can be unit-tested on
synthetic arrays without a game window.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

#: A captured image: ``(height, width, 3)`` uint8 in **BGR** order (OpenCV convention).
Frame = npt.NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned rectangle in pixels."""

    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Rect must have positive size, got {self.width}x{self.height}")

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def intersect(self, other: Rect) -> Rect | None:
        """Return the overlap with ``other``, or None if they don't overlap."""
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right - left, bottom - top)


def frame_difference(a: Frame, b: Frame, *, pixel_tolerance: int = 12) -> float:
    """Fraction (0.0 to 1.0) of pixels that changed by more than ``pixel_tolerance``.

    A pixel counts as changed if any channel moved by more than the tolerance,
    which ignores tiny compression/dithering noise. Frames of different shape
    are treated as completely different (1.0).
    """
    if a.shape != b.shape:
        return 1.0
    if a.size == 0:
        return 0.0
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16))
    if delta.ndim == 3:
        delta = delta.max(axis=2)
    return float(np.count_nonzero(delta > pixel_tolerance)) / float(delta.size)


@dataclass(frozen=True, slots=True)
class ChangeResult:
    frame: Frame
    changed: bool  # False: nothing changed vs. the reference before the change timeout
    settled: bool
    frames_grabbed: int
    elapsed_s: float


def settle_after_change(
    grab: Callable[[], Frame],
    reference: Frame | None,
    *,
    change_threshold: float = 0.001,
    change_timeout_s: float = 0.25,
    settle_threshold: float = 0.002,
    settle_timeout_s: float = 0.3,
    interval_s: float = 0.035,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> ChangeResult:
    """After a keypress: wait for the image to differ from ``reference``, then to settle.

    The diff against the last stable frame is only a prefilter ("did anything
    happen?"). Games redraw a frame or two after the key, so a grab taken too
    early still shows the old state; waiting for a change avoids reading it.
    With no reference (first read), it only waits for the image to settle.
    """
    start = clock()
    frame = grab()
    count = 1
    if reference is not None:
        while frame_difference(frame, reference) <= change_threshold:
            if clock() - start >= change_timeout_s:
                return ChangeResult(frame, False, True, count, clock() - start)
            sleep(interval_s)
            frame = grab()
            count += 1
    settle_start = clock()
    while True:
        if clock() - settle_start >= settle_timeout_s:
            return ChangeResult(frame, True, False, count, clock() - start)
        sleep(interval_s)
        current = grab()
        count += 1
        if frame_difference(frame, current) < settle_threshold:
            return ChangeResult(current, True, True, count, clock() - start)
        frame = current


@dataclass(frozen=True, slots=True)
class SettleResult:
    frame: Frame
    settled: bool  # False if we hit the timeout while the image was still changing
    frames_grabbed: int
    elapsed_s: float


def settle(
    grab: Callable[[], Frame],
    *,
    threshold: float = 0.002,
    interval_s: float = 0.016,
    timeout_s: float = 0.3,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> SettleResult:
    """Grab frames until two in a row differ by less than ``threshold``.

    Menus often animate after a keypress (sliding cursors, fades). Waiting for
    the image to stop changing avoids reading a half-drawn frame. Returns the
    latest frame either way; ``settled`` says whether it actually stabilised.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if interval_s < 0 or timeout_s < 0:
        raise ValueError("interval_s and timeout_s must be non-negative")

    start = clock()
    previous = grab()
    count = 1
    while True:
        if clock() - start >= timeout_s:
            return SettleResult(previous, False, count, clock() - start)
        sleep(interval_s)
        current = grab()
        count += 1
        if frame_difference(previous, current) < threshold:
            return SettleResult(current, True, count, clock() - start)
        previous = current
