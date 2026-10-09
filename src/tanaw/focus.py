"""Focus Mode selection logic (pure: numpy/OpenCV only, no capture/OCR/speech).

Finds the *currently selected* menu item in a captured menu region, using the
game's profile:

* ``cursor_template``: template-match the cursor sprite, then take the row box
  at the calibrated offset from it.
* ``highlight_color``: HSV-mask the highlight colour, merge it into rows, take
  the largest.

A frame diff is only ever used by the caller as a prefilter ("did anything
change?"), never to decide which item is selected: a diff marks both the old
and the new item as changed. All of this is classic image processing, not AI.
"""

from __future__ import annotations

import enum
import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
import numpy.typing as npt

from tanaw.frames import Frame, Rect
from tanaw.profile import (
    CursorTemplateStrategy,
    FocusOcrOptions,
    FocusProfile,
    Hsv,
    RowOffset,
)

Mask = npt.NDArray[np.uint8]

MSG_SELECTION_UNCLEAR = "Selection unclear."


class ProfileMismatchError(ValueError):
    """The game window's shape no longer matches the profile."""


class LocateStatus(enum.Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"  # no cursor/highlight: probably no menu open
    AMBIGUOUS = "ambiguous"  # two equally good candidates


@dataclass(frozen=True, slots=True)
class Located:
    status: LocateStatus
    row: Rect | None  # selected row in menu-region coordinates
    score: float  # match score (template) or pixel count (highlight)


def normalize_frame(frame: Frame, width: int, height: int, *, tolerance: float = 0.02) -> Frame:
    """Resize a client-area frame to the size the profile was calibrated at.

    Same aspect ratio (within ``tolerance``) is scaled; a different shape means
    the profile no longer fits and must be recalibrated.
    """
    h, w = frame.shape[:2]
    if (w, h) == (width, height):
        return frame
    if abs((w / h) - (width / height)) > tolerance * (width / height):
        raise ProfileMismatchError(
            f"The game window is {w}x{h} but the profile was made at {width}x{height}. "
            "Recalibrate, or restore the window size."
        )
    resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_NEAREST)
    out: Frame = np.ascontiguousarray(resized, dtype=np.uint8)
    return out


def crop(image: Frame, rect: Rect) -> Frame:
    h, w = image.shape[:2]
    clipped = Rect(0, 0, w, h).intersect(rect)
    if clipped is None:
        raise ValueError("crop rectangle is outside the image")
    out: Frame = np.ascontiguousarray(
        image[clipped.top:clipped.bottom, clipped.left:clipped.right]
    )
    return out


def _clamp(rect: Rect, width: int, height: int) -> Rect | None:
    return Rect(0, 0, width, height).intersect(rect)


# --- cursor_template ------------------------------------------------------------------------


def locate_cursor(
    region: Frame,
    template: Frame,
    *,
    threshold: float,
    row: RowOffset,
    ambiguity_margin: float = 0.05,
) -> Located:
    rh, rw = region.shape[:2]
    th, tw = template.shape[:2]
    if th > rh or tw > rw:
        return Located(LocateStatus.NOT_FOUND, None, 0.0)
    scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    # Flat (zero-variance) patches can produce NaN/inf; they are never a match.
    scores = np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0)
    _, best, _, best_loc = cv2.minMaxLoc(scores)
    if best < threshold:
        return Located(LocateStatus.NOT_FOUND, None, float(best))

    # Second-best match away from the best one: two equally good cursors = ambiguous.
    x, y = best_loc
    suppressed = scores.copy()
    suppressed[max(0, y - th):y + th + 1, max(0, x - tw):x + tw + 1] = 0.0
    _, second, _, _ = cv2.minMaxLoc(suppressed)
    if second >= threshold and best - second < ambiguity_margin:
        return Located(LocateStatus.AMBIGUOUS, None, float(best))

    row_rect = _clamp(Rect(x + row.dx, y + row.dy, row.width, row.height), rw, rh)
    if row_rect is None:
        return Located(LocateStatus.NOT_FOUND, None, float(best))
    return Located(LocateStatus.FOUND, row_rect, float(best))


# --- highlight_color ------------------------------------------------------------------------


def hsv_mask(image: Frame, low: Hsv, high: Hsv) -> Mask:
    """255 where the pixel is inside the HSV range. Hue wraps if low_h > high_h."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    if low[0] <= high[0]:
        mask = cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8))
    else:
        upper = cv2.inRange(hsv, np.array(low, np.uint8),
                            np.array((179, high[1], high[2]), np.uint8))
        lower = cv2.inRange(hsv, np.array((0, low[1], low[2]), np.uint8),
                            np.array(high, np.uint8))
        mask = cv2.bitwise_or(upper, lower)
    out: Mask = np.asarray(mask, dtype=np.uint8)
    return out


def locate_highlight(
    region: Frame,
    *,
    low: Hsv,
    high: Hsv,
    min_pixels: int,
    merge_gap: int,
    padding: int,
    ambiguity_ratio: float = 0.8,
) -> Located:
    mask = hsv_mask(region, low, high)
    if int(np.count_nonzero(mask)) < min_pixels:
        return Located(LocateStatus.NOT_FOUND, None, float(np.count_nonzero(mask)))
    # Join letters / bar fragments of one row; keep rows apart vertically.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (merge_gap, max(1, merge_gap // 4)))
    merged = cv2.dilate(mask, kernel)
    count, raw_labels = cv2.connectedComponents(merged, connectivity=8)
    labels = np.asarray(raw_labels, dtype=np.int32)
    if count <= 1:
        return Located(LocateStatus.NOT_FOUND, None, 0.0)
    # Real (undilated) highlight pixels per merged group.
    pixel_counts = np.bincount(labels[mask > 0], minlength=count)
    pixel_counts[0] = 0
    order = np.argsort(pixel_counts)[::-1]
    best_label, best = int(order[0]), int(pixel_counts[order[0]])
    if best < min_pixels:
        return Located(LocateStatus.NOT_FOUND, None, float(best))
    second = int(pixel_counts[order[1]]) if count > 2 else 0
    if second >= min_pixels and second >= ambiguity_ratio * best:
        return Located(LocateStatus.AMBIGUOUS, None, float(best))

    ys, xs = np.nonzero((labels == best_label) & (mask > 0))
    rh, rw = region.shape[:2]
    box = Rect(int(xs.min()) - padding, int(ys.min()) - padding,
               int(xs.max() - xs.min()) + 1 + 2 * padding,
               int(ys.max() - ys.min()) + 1 + 2 * padding)
    row = _clamp(box, rw, rh)
    if row is None:
        return Located(LocateStatus.NOT_FOUND, None, float(best))
    return Located(LocateStatus.FOUND, row, float(best))


def locate_selection(region: Frame, profile: FocusProfile, template: Frame | None) -> Located:
    """Run the profile's strategy on a menu-region image."""
    strategy = profile.strategy
    if isinstance(strategy, CursorTemplateStrategy):
        if template is None:
            raise ValueError("cursor_template profile loaded without its template image")
        return locate_cursor(region, template, threshold=strategy.match_threshold,
                             row=strategy.row)
    return locate_highlight(region, low=strategy.hsv_low, high=strategy.hsv_high,
                            min_pixels=strategy.min_pixels, merge_gap=strategy.merge_gap,
                            padding=strategy.padding)


# --- OCR preparation and decision -----------------------------------------------------------


def preprocess_for_ocr(
    image: Frame, mode: Literal["none", "threshold"], threshold_value: int = 60
) -> Frame:
    """Optional clean-up before OCR. "threshold": light text on dark -> black on white."""
    if mode == "none":
        return image
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, threshold_value, 255, cv2.THRESH_BINARY_INV)
    out: Frame = np.ascontiguousarray(cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    return out


def border_colour(image: Frame) -> tuple[int, int, int]:
    """Median colour of the image's outer pixels (the row's background)."""
    edges = np.concatenate([image[0], image[-1], image[:, 0], image[:, -1]])
    b, g, r = np.median(edges, axis=0)
    return int(b), int(g), int(r)


def prepare_row_for_ocr(row: Frame, options: FocusOcrOptions) -> Frame:
    """Shrink to native pixel size, pad with background, optionally threshold.

    Measured on a DELTARUNE frame (36 row positions/heights per word): plain crop
    read "No" correctly 15/36 times; downscale 2 + pad 16 + threshold read it 36/36.
    """
    out = row
    if options.downscale > 1:
        h, w = out.shape[:2]
        size = (max(1, w // options.downscale), max(1, h // options.downscale))
        out = np.ascontiguousarray(cv2.resize(out, size, interpolation=cv2.INTER_AREA),
                                   dtype=np.uint8)
    if options.pad > 0:
        p = options.pad
        out = np.ascontiguousarray(
            cv2.copyMakeBorder(out, p, p, p, p, cv2.BORDER_CONSTANT, value=border_colour(out)),
            dtype=np.uint8,
        )
    return preprocess_for_ocr(out, options.preprocess, options.threshold_value)


def image_key(image: Frame, *extra: object) -> str:
    """Stable hash of an image (plus settings) for the OCR cache."""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(repr((image.shape, *extra)).encode())
    digest.update(np.ascontiguousarray(image).tobytes())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class Reading:
    text: str
    confidence: float  # lowest box confidence in the row (0 if no boxes)
    low_confidence: int  # boxes dropped for low confidence


class OcrCache:
    """Small LRU cache: crop hash -> Reading. Revisiting a menu item skips OCR."""

    def __init__(self, capacity: int = 256) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._items: OrderedDict[str, Reading] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Reading | None:
        reading = self._items.get(key)
        if reading is None:
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return reading

    def put(self, key: str, reading: Reading) -> None:
        self._items[key] = reading
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


class Decision(enum.Enum):
    SPEAK = "speak"  # new, confident selection text
    SAME = "same"  # selection text didn't change: stay quiet
    UNCLEAR = "unclear"  # say "Selection unclear."
    SILENT = "silent"  # no menu/cursor on screen: stay quiet


def decide(
    located: Located, reading: Reading | None, previous_text: str | None, min_confidence: float
) -> Decision:
    """Whether to speak. Never speaks a guess: low confidence or ambiguity -> UNCLEAR."""
    if located.status is LocateStatus.NOT_FOUND:
        return Decision.SILENT
    if located.status is LocateStatus.AMBIGUOUS:
        return Decision.UNCLEAR
    if reading is None or not reading.text or reading.low_confidence > 0:
        return Decision.UNCLEAR
    if reading.confidence < min_confidence:
        return Decision.UNCLEAR
    if previous_text is not None and reading.text == previous_text:
        return Decision.SAME
    return Decision.SPEAK
