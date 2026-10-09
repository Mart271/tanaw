"""Focus Mode selection logic on synthetic images drawn here (no game art in the repo)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from tanaw.focus import (
    Decision,
    Located,
    LocateStatus,
    OcrCache,
    ProfileMismatchError,
    Reading,
    crop,
    decide,
    hsv_mask,
    image_key,
    locate_cursor,
    locate_highlight,
    normalize_frame,
    preprocess_for_ocr,
)
from tanaw.frames import Frame, Rect
from tanaw.profile import RowOffset

YELLOW = (0, 255, 255)  # BGR
WHITE = (255, 255, 255)
RED = (0, 0, 255)
ITEMS = ["Yes", "No", "Maybe"]
ROW_Y = [100, 150, 200]  # text baselines
CURSOR_X = 60


def draw_cursor(img: Frame, x: int, y: int) -> None:
    """A small two-tone 'heart-ish' sprite (needs internal contrast for template matching)."""
    pts = np.array([[x, y + 4], [x + 4, y], [x + 8, y + 4], [x + 12, y], [x + 16, y + 4],
                    [x + 8, y + 16]], np.int32)
    cv2.fillPoly(img, [pts], RED)
    cv2.rectangle(img, (x + 3, y + 3), (x + 5, y + 5), WHITE, -1)  # shine


def menu(selected: int, *, cursor: bool = True, highlight: bool = True) -> Frame:
    img: Frame = np.zeros((300, 400, 3), dtype=np.uint8)
    for i, (text, y) in enumerate(zip(ITEMS, ROW_Y, strict=True)):
        colour = YELLOW if (highlight and i == selected) else WHITE
        cv2.putText(img, text, (90, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, colour, 2)
    if cursor:
        draw_cursor(img, CURSOR_X, ROW_Y[selected] - 18)
    return img


def cursor_template() -> Frame:
    img = menu(0)
    return crop(img, Rect(CURSOR_X - 2, ROW_Y[0] - 20, 21, 21))


ROW = RowOffset(dx=-4, dy=-10, width=200, height=40)


@pytest.mark.parametrize("selected", [0, 1, 2])
def test_cursor_found_on_selected_row(selected: int) -> None:
    located = locate_cursor(menu(selected), cursor_template(), threshold=0.8, row=ROW)
    assert located.status is LocateStatus.FOUND
    assert located.row is not None
    # The row box must contain the selected item's baseline and no other item's.
    top, bottom = located.row.top, located.row.bottom
    assert top <= ROW_Y[selected] - 10 and bottom >= ROW_Y[selected]
    others = [y for i, y in enumerate(ROW_Y) if i != selected]
    assert all(not (top <= y - 10 and y <= bottom) for y in others)


def test_no_cursor_means_not_found() -> None:
    located = locate_cursor(menu(0, cursor=False), cursor_template(), threshold=0.8, row=ROW)
    assert located.status is LocateStatus.NOT_FOUND


def test_flat_black_screen_is_not_a_match() -> None:
    black: Frame = np.zeros((300, 400, 3), dtype=np.uint8)
    located = locate_cursor(black, cursor_template(), threshold=0.8, row=ROW)
    assert located.status is LocateStatus.NOT_FOUND


def test_two_identical_cursors_are_ambiguous() -> None:
    img = menu(0)
    draw_cursor(img, CURSOR_X, ROW_Y[2] - 18)
    located = locate_cursor(img, cursor_template(), threshold=0.8, row=ROW)
    assert located.status is LocateStatus.AMBIGUOUS


def test_row_is_clamped_to_region() -> None:
    wide = RowOffset(dx=-100, dy=-10, width=2000, height=40)
    located = locate_cursor(menu(1), cursor_template(), threshold=0.8, row=wide)
    assert located.row is not None
    assert located.row.left == 0 and located.row.right == 400


# Yellow in OpenCV HSV is hue 30.
YELLOW_LOW, YELLOW_HIGH = (20, 100, 100), (40, 255, 255)


@pytest.mark.parametrize("selected", [0, 1, 2])
def test_highlight_colour_finds_selected_row(selected: int) -> None:
    located = locate_highlight(menu(selected, cursor=False), low=YELLOW_LOW, high=YELLOW_HIGH,
                               min_pixels=20, merge_gap=12, padding=4)
    assert located.status is LocateStatus.FOUND
    assert located.row is not None
    assert located.row.top <= ROW_Y[selected] - 15 <= located.row.bottom
    assert located.row.left <= 95 and located.row.right >= 120


def test_no_highlight_means_not_found() -> None:
    located = locate_highlight(menu(0, highlight=False), low=YELLOW_LOW, high=YELLOW_HIGH,
                               min_pixels=20, merge_gap=12, padding=4)
    assert located.status is LocateStatus.NOT_FOUND


def test_two_equal_highlights_are_ambiguous() -> None:
    img: Frame = np.zeros((300, 400, 3), dtype=np.uint8)
    cv2.putText(img, "Yes", (90, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.9, YELLOW, 2)
    cv2.putText(img, "Yes", (90, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.9, YELLOW, 2)
    located = locate_highlight(img, low=YELLOW_LOW, high=YELLOW_HIGH, min_pixels=20,
                               merge_gap=12, padding=4)
    assert located.status is LocateStatus.AMBIGUOUS


def test_hsv_mask_wraps_hue_for_red() -> None:
    img: Frame = np.zeros((10, 20, 3), dtype=np.uint8)
    img[:, :10] = RED  # hue 0
    mask = hsv_mask(img, (170, 100, 100), (10, 255, 255))
    assert mask[:, :10].all()
    assert not mask[:, 10:].any()


def test_normalize_frame() -> None:
    small: Frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert normalize_frame(small, 640, 480) is small
    assert normalize_frame(small, 1280, 960).shape == (960, 1280, 3)
    with pytest.raises(ProfileMismatchError):
        normalize_frame(small, 1280, 720)


def test_crop_clips_and_rejects_outside() -> None:
    img = menu(0)
    assert crop(img, Rect(390, 290, 50, 50)).shape == (10, 10, 3)
    with pytest.raises(ValueError):
        crop(img, Rect(500, 500, 10, 10))


def test_threshold_preprocess_makes_dark_text_on_white() -> None:
    img = menu(0)
    out = preprocess_for_ocr(img, "threshold", 60)
    assert out.shape == img.shape
    assert int(out[5, 5, 0]) == 255  # background was black -> white
    assert preprocess_for_ocr(img, "none") is img


def test_image_key_changes_with_pixels_and_settings() -> None:
    a = menu(0)
    b = menu(1)
    assert image_key(a, 1.0) == image_key(a.copy(), 1.0)
    assert image_key(a, 1.0) != image_key(b, 1.0)
    assert image_key(a, 1.0) != image_key(a, 2.0)


def test_ocr_cache_is_lru() -> None:
    cache = OcrCache(capacity=2)
    r = Reading("Yes", 0.9, 0)
    cache.put("a", r)
    cache.put("b", r)
    assert cache.get("a") == r  # a is now most recent
    cache.put("c", r)  # evicts b
    assert cache.get("b") is None
    assert cache.get("c") == r
    assert (cache.hits, cache.misses, len(cache)) == (2, 1, 2)


FOUND = Located(LocateStatus.FOUND, Rect(0, 0, 10, 10), 0.95)


@pytest.mark.parametrize(
    ("located", "reading", "previous", "expected"),
    [
        (FOUND, Reading("Yes", 0.95, 0), None, Decision.SPEAK),
        (FOUND, Reading("No", 0.95, 0), "Yes", Decision.SPEAK),
        (FOUND, Reading("Yes", 0.95, 0), "Yes", Decision.SAME),
        (FOUND, Reading("Yes", 0.40, 0), None, Decision.UNCLEAR),  # low confidence
        (FOUND, Reading("Yes", 0.95, 1), None, Decision.UNCLEAR),  # part of row unreadable
        (FOUND, Reading("", 0.0, 0), None, Decision.UNCLEAR),  # cursor but no text
        (FOUND, None, None, Decision.UNCLEAR),
        (Located(LocateStatus.AMBIGUOUS, None, 0.9), Reading("Yes", 0.95, 0), None,
         Decision.UNCLEAR),
        (Located(LocateStatus.NOT_FOUND, None, 0.1), None, "Yes", Decision.SILENT),
    ],
)
def test_decide(
    located: Located, reading: Reading | None, previous: str | None, expected: Decision
) -> None:
    assert decide(located, reading, previous, 0.6) is expected


def test_prepare_row_downscales_pads_with_background_and_thresholds() -> None:
    from tanaw.focus import border_colour, prepare_row_for_ocr
    from tanaw.profile import FocusOcrOptions

    row = crop(menu(0), Rect(80, 80, 120, 40))
    assert border_colour(row) == (0, 0, 0)
    plain = prepare_row_for_ocr(row, FocusOcrOptions(downscale=1, pad=0))
    assert plain.shape == row.shape
    out = prepare_row_for_ocr(row, FocusOcrOptions(downscale=2, pad=16, preprocess="threshold"))
    assert out.shape == (20 + 32, 60 + 32, 3)
    assert int(out[0, 0, 0]) == 255  # black padding became white after thresholding


def test_selected_row_image_removes_the_cursor() -> None:
    from tanaw.focus import selected_row_image

    located = locate_cursor(menu(0), cursor_template(), threshold=0.8, row=ROW)
    assert located.cursor is not None
    row = selected_row_image(menu(0), located)
    hsv_red = hsv_mask(row, (170, 100, 100), (10, 255, 255))
    assert not hsv_red.any()  # no red cursor pixels left
    assert row.shape[:2] == (ROW.height, ROW.width)
