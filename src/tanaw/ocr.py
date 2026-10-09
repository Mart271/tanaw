"""Typed adapter around RapidOCR (PaddleOCR PP-OCRv6 models on ONNX Runtime).

Everything runs locally. The model files ship inside the ``rapidocr`` wheel and
we pass their paths explicitly, so the library never reaches its download path.

Debug CLI::

    python -m tanaw.ocr debug/capture.png --upscale 2
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from tanaw import netguard
from tanaw.frames import Frame, Rect

logger = logging.getLogger(__name__)

DET_MODEL = "PP-OCRv6_det_small.onnx"
REC_MODEL = "PP-OCRv6_rec_small.onnx"
# rapidocr refuses images above this side length by shrinking them; we raise it
# so upscaled game frames keep their extra detail.
MAX_SIDE_LEN = 4096
MAX_UPSCALE = 4.0
# The detector shrinks frames so the longest side is at most this. rapidocr's
# default ("min side >= 736") blows small crops up >10x and made a 160x60 crop
# take ~1.7 s; capping the long side brought it to ~0.2 s on our test laptop.
DET_MAX_SIDE = 960
# ONNX Runtime's default (all cores) was slower than 4 threads on a 16-thread
# i5-12500H in our quick checks, and 4 is safe on smaller laptops too.
ORT_THREADS = 4

Interpolation = Literal["cubic", "nearest"]
_INTERPOLATION = {"cubic": cv2.INTER_CUBIC, "nearest": cv2.INTER_NEAREST}


class OcrError(RuntimeError):
    """OCR could not run. The message is safe to speak aloud."""


@dataclass(frozen=True, slots=True)
class TextBox:
    text: str
    confidence: float  # 0.0 to 1.0, from the recognition model
    rect: Rect  # bounding box in the coordinates of the image passed to read()


@dataclass(frozen=True, slots=True)
class OcrResult:
    boxes: list[TextBox]  # boxes at or above the confidence threshold
    low_confidence: int  # how many boxes were dropped for low confidence
    elapsed_s: float
    image_size: tuple[int, int]  # (width, height) of the input, before upscaling


def quad_to_rect(quad: Sequence[Sequence[float]], scale: float) -> Rect:
    """Axis-aligned bounding Rect of a 4-point polygon, mapped back by ``1/scale``."""
    xs = [float(p[0]) / scale for p in quad]
    ys = [float(p[1]) / scale for p in quad]
    left, top = int(np.floor(min(xs))), int(np.floor(min(ys)))
    right, bottom = int(np.ceil(max(xs))), int(np.ceil(max(ys)))
    return Rect(left, top, max(1, right - left), max(1, bottom - top))


def validate_upscale(upscale: float) -> float:
    if not 1.0 <= upscale <= MAX_UPSCALE:
        raise ValueError(f"upscale must be between 1 and {MAX_UPSCALE:g}, got {upscale}")
    return upscale


def validate_confidence(value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"confidence threshold must be between 0 and 1, got {value}")
    return value


def _model_dir() -> Path:
    import rapidocr

    return Path(rapidocr.__file__).resolve().parent / "models"


class OcrEngine:
    """Loads the OCR models once; call :meth:`read` from a single worker thread."""

    def __init__(
        self,
        *,
        min_confidence: float = 0.6,
        upscale: float = 1.0,
        interpolation: Interpolation = "cubic",
    ) -> None:
        self.min_confidence = validate_confidence(min_confidence)
        self.upscale = validate_upscale(upscale)
        self.interpolation: Interpolation = interpolation

        model_dir = _model_dir()
        det, rec = model_dir / DET_MODEL, model_dir / REC_MODEL
        for path in (det, rec):
            if not path.is_file():
                raise OcrError(
                    f"OCR model file missing: {path.name}. Reinstall with "
                    '`python -m pip install -e ".[dev]"`.'
                )

        from rapidocr import RapidOCR

        self._engine = RapidOCR(
            params={
                "Global.log_level": "error",
                # We filter by confidence ourselves so we can count what we drop.
                "Global.text_score": 0.0,
                # Game text is never upside down; skipping the direction model saves time.
                "Global.use_cls": False,
                "Global.max_side_len": MAX_SIDE_LEN,
                "Det.limit_type": "max",
                "Det.limit_side_len": DET_MAX_SIDE,
                "EngineConfig.onnxruntime.intra_op_num_threads": ORT_THREADS,
                "Det.model_path": str(det),
                "Rec.model_path": str(rec),
            }
        )

    def warm_up(self) -> float:
        """Run once on a tiny image so model loading doesn't delay the first hotkey."""
        start = time.perf_counter()
        blank: Frame = np.full((64, 256, 3), 255, dtype=np.uint8)
        cv2.putText(blank, "Tanaw", (10, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
        self.read(blank, upscale=1.0)
        return time.perf_counter() - start

    def read(self, image: Frame, *, upscale: float | None = None) -> OcrResult:
        """Find and read all text in a BGR image."""
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise OcrError("OCR expects a BGR uint8 image.")
        height, width = image.shape[:2]
        if width == 0 or height == 0:
            raise OcrError("OCR got an empty image.")

        scale = validate_upscale(self.upscale if upscale is None else upscale)
        start = time.perf_counter()
        prepared = image
        if scale != 1.0:
            resized = cv2.resize(
                image, None, fx=scale, fy=scale, interpolation=_INTERPOLATION[self.interpolation]
            )
            prepared = np.asarray(resized, dtype=np.uint8)

        raw = self._engine(prepared)
        boxes, low = self._convert(raw, scale)
        elapsed = time.perf_counter() - start
        logger.debug(
            "ocr %dx%d upscale=%.1f boxes=%d low_conf=%d in %.0f ms",
            width, height, scale, len(boxes), low, elapsed * 1000,
        )
        return OcrResult(boxes, low, elapsed, (width, height))

    def _convert(self, raw: object, scale: float) -> tuple[list[TextBox], int]:
        quads = getattr(raw, "boxes", None)
        texts = getattr(raw, "txts", None)
        scores = getattr(raw, "scores", None)
        if quads is None or texts is None or scores is None:
            return [], 0  # rapidocr returns None fields when it finds no text

        kept: list[TextBox] = []
        low = 0
        for quad, text, score in zip(quads, texts, scores, strict=True):
            confidence = float(score)
            cleaned = str(text).strip()
            if not cleaned:
                continue
            if confidence < self.min_confidence:
                low += 1
                continue
            points = [(float(p[0]), float(p[1])) for p in quad]
            kept.append(TextBox(cleaned, confidence, quad_to_rect(points, scale)))
        return kept, low


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tanaw.ocr", description="OCR an image file and print the results."
    )
    parser.add_argument("image", type=Path, help="Path to a PNG/JPG image")
    parser.add_argument("--upscale", type=float, default=1.0, help="Scale factor, 1 to 4")
    parser.add_argument("--nearest", action="store_true",
                        help="Nearest-neighbour upscaling (try for pixel fonts)")
    parser.add_argument("--min-confidence", type=float, default=0.6)
    args = parser.parse_args(argv)

    netguard.install()
    try:
        validate_upscale(args.upscale)
        validate_confidence(args.min_confidence)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.image.is_file():
        parser.error(f"not a file: {args.image}")
    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        parser.error(f"could not read image: {args.image}")

    t0 = time.perf_counter()
    try:
        engine = OcrEngine(
            min_confidence=args.min_confidence,
            upscale=args.upscale,
            interpolation="nearest" if args.nearest else "cubic",
        )
        load_s = time.perf_counter() - t0
        warm_s = engine.warm_up()
        result = engine.read(np.ascontiguousarray(image, dtype=np.uint8))
    except OcrError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    w, h = result.image_size
    print(f"image {w}x{h}  upscale {args.upscale:g}  "
          f"init {load_s * 1000:.0f} ms  warm-up {warm_s * 1000:.0f} ms  "
          f"OCR {result.elapsed_s * 1000:.0f} ms")
    print(f"{len(result.boxes)} boxes kept, {result.low_confidence} dropped below "
          f"{args.min_confidence:.2f}")
    for box in result.boxes:
        r = box.rect
        where = f"x={r.left:<5} y={r.top:<5} {r.width}x{r.height:<4}"
        print(f"  {box.confidence:.3f}  {where}  {box.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
