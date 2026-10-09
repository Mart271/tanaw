"""Reading order for OCR boxes (pure logic, no I/O).

Groups boxes into lines by vertical overlap, orders lines top to bottom and
boxes left to right, and turns the result into text that sounds right when
spoken. This is plain geometry, not AI.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from tanaw.ocr import TextBox

# Gaps wider than this many line-heights are read with a pause (", "), e.g. the
# space between side-by-side menu options like "FIGHT      ACT".
DEFAULT_GAP_FACTOR = 1.5
_SENTENCE_END = (".", "!", "?", ":", ";", ",")
# Dialogue bullets used by many RPGs ("* You feel your sins crawling on your back.")
_LEADING_BULLET = re.compile(r"^[*•·>\-]+\s*")
_ONLY_SYMBOLS = re.compile(r"^[\W_]+$")


@dataclass(frozen=True, slots=True)
class Line:
    boxes: tuple[TextBox, ...]  # left to right

    @property
    def top(self) -> int:
        return min(b.rect.top for b in self.boxes)

    @property
    def bottom(self) -> int:
        return max(b.rect.bottom for b in self.boxes)

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def text(self, gap_factor: float = DEFAULT_GAP_FACTOR) -> str:
        """Join the boxes, inserting ", " across wide horizontal gaps."""
        if not self.boxes:
            return ""
        typical_height = statistics.median(b.rect.height for b in self.boxes)
        parts = [self.boxes[0].text]
        for prev, box in zip(self.boxes, self.boxes[1:], strict=False):
            gap = box.rect.left - prev.rect.right
            wide = gap > gap_factor * typical_height
            ends_with_punct = parts[-1].endswith(_SENTENCE_END)
            separator = ", " if wide and not ends_with_punct else " "
            parts.append(separator + box.text)
        return "".join(parts)


def _vertical_overlap_ratio(top_a: int, bottom_a: int, top_b: int, bottom_b: int) -> float:
    overlap = min(bottom_a, bottom_b) - max(top_a, top_b)
    shorter = min(bottom_a - top_a, bottom_b - top_b)
    if overlap <= 0 or shorter <= 0:
        return 0.0
    return overlap / shorter


def group_lines(boxes: Sequence[TextBox], *, min_overlap: float = 0.5) -> list[Line]:
    """Group boxes into lines, top to bottom; each line's boxes left to right.

    A box joins the existing line it overlaps most vertically, if that overlap is
    at least ``min_overlap`` of the shorter of the two heights.
    """
    if not 0.0 < min_overlap <= 1.0:
        raise ValueError("min_overlap must be in (0, 1]")
    ordered = sorted(boxes, key=lambda b: (b.rect.top + b.rect.height / 2, b.rect.left))
    rows: list[list[TextBox]] = []
    spans: list[tuple[int, int]] = []
    for box in ordered:
        best_index = -1
        best_ratio = 0.0
        for i, (top, bottom) in enumerate(spans):
            ratio = _vertical_overlap_ratio(top, bottom, box.rect.top, box.rect.bottom)
            if ratio >= min_overlap and ratio > best_ratio:
                best_index, best_ratio = i, ratio
        if best_index < 0:
            rows.append([box])
            spans.append((box.rect.top, box.rect.bottom))
        else:
            rows[best_index].append(box)
            top, bottom = spans[best_index]
            spans[best_index] = (min(top, box.rect.top), max(bottom, box.rect.bottom))

    lines = [Line(tuple(sorted(row, key=lambda b: b.rect.left))) for row in rows]
    lines.sort(key=lambda line: (line.top, line.boxes[0].rect.left))
    return lines


def clean_line_for_speech(text: str) -> str:
    """Drop dialogue bullets and lines that are only symbols; end with a full stop."""
    cleaned = _LEADING_BULLET.sub("", text.strip()).strip()
    if not cleaned or _ONLY_SYMBOLS.match(cleaned):
        return ""
    if not cleaned.endswith(_SENTENCE_END):
        cleaned += "."
    return cleaned


def speech_text(lines: Sequence[Line], *, gap_factor: float = DEFAULT_GAP_FACTOR) -> str:
    """Turn ordered lines into one utterance, with a pause between lines."""
    spoken = [clean_line_for_speech(line.text(gap_factor)) for line in lines]
    return " ".join(s for s in spoken if s)
