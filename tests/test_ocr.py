from __future__ import annotations

from collections.abc import Iterator

import cv2
import numpy as np
import pytest

from tanaw import netguard
from tanaw.frames import Frame, Rect
from tanaw.ocr import OcrEngine, OcrError, quad_to_rect, validate_confidence, validate_upscale


def test_quad_to_rect_maps_back_from_upscaled_coordinates() -> None:
    quad = [(20.0, 10.0), (120.0, 12.0), (118.0, 50.0), (22.0, 48.0)]
    assert quad_to_rect(quad, 1.0) == Rect(20, 10, 100, 40)
    assert quad_to_rect(quad, 2.0) == Rect(10, 5, 50, 20)


def test_quad_to_rect_never_returns_empty_rect() -> None:
    assert quad_to_rect([(5.0, 5.0)] * 4, 1.0) == Rect(5, 5, 1, 1)


@pytest.mark.parametrize("value", [0.5, 0.0, 5.0])
def test_upscale_range(value: float) -> None:
    with pytest.raises(ValueError):
        validate_upscale(value)


def test_confidence_range() -> None:
    assert validate_confidence(0.6) == 0.6
    with pytest.raises(ValueError):
        validate_confidence(1.5)


@pytest.fixture(scope="module")
def engine() -> Iterator[OcrEngine]:
    # The guard is on while the real models load and run: proves OCR is offline.
    netguard.install()
    try:
        yield OcrEngine(min_confidence=0.6)
    finally:
        netguard.uninstall()


def _text_image() -> Frame:
    img: Frame = np.full((220, 640, 3), 255, dtype=np.uint8)
    cv2.putText(img, "FIGHT", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    cv2.putText(img, "ITEM", (360, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    cv2.putText(img, "HP 120", (20, 180), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    return img


def test_real_model_reads_synthetic_text_offline(engine: OcrEngine) -> None:
    result = engine.read(_text_image())
    texts = [b.text for b in result.boxes]
    assert "FIGHT" in texts
    assert "ITEM" in texts
    assert any("120" in t for t in texts)
    for box in result.boxes:
        assert box.confidence >= 0.6
        assert 0 <= box.rect.left < 640
        assert 0 <= box.rect.top < 220


def test_upscaled_boxes_are_in_original_coordinates(engine: OcrEngine) -> None:
    plain = {b.text: b.rect for b in engine.read(_text_image()).boxes}
    scaled = {b.text: b.rect for b in engine.read(_text_image(), upscale=2.0).boxes}
    assert "FIGHT" in plain
    assert "FIGHT" in scaled
    assert abs(plain["FIGHT"].left - scaled["FIGHT"].left) <= 4
    assert abs(plain["FIGHT"].top - scaled["FIGHT"].top) <= 4


def test_blank_image_returns_no_boxes(engine: OcrEngine) -> None:
    result = engine.read(np.full((100, 100, 3), 255, dtype=np.uint8))
    assert result.boxes == []


def test_rejects_wrong_image_type(engine: OcrEngine) -> None:
    with pytest.raises(OcrError):
        engine.read(np.zeros((10, 10), dtype=np.uint8))
