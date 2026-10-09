from __future__ import annotations

import pytest

from tanaw.frames import Rect
from tanaw.layout import clean_line_for_speech, group_lines, speech_text
from tanaw.ocr import TextBox


def box(text: str, left: int, top: int, width: int = 60, height: int = 20) -> TextBox:
    return TextBox(text, 0.95, Rect(left, top, width, height))


def test_groups_boxes_into_lines_in_reading_order() -> None:
    boxes = [
        box("ACT", 200, 302),
        box("HP", 10, 100),
        box("FIGHT", 10, 300),
        box("120/160", 80, 98, width=90),
        box("ITEM", 400, 299),
    ]
    lines = group_lines(boxes)
    assert [[b.text for b in line.boxes] for line in lines] == [
        ["HP", "120/160"],
        ["FIGHT", "ACT", "ITEM"],
    ]


def test_slightly_misaligned_boxes_stay_on_one_line() -> None:
    lines = group_lines([box("Kris", 10, 50, height=20), box("LV1", 80, 58, height=20)])
    assert len(lines) == 1


def test_stacked_boxes_become_separate_lines() -> None:
    lines = group_lines([box("one", 10, 10), box("two", 10, 35), box("three", 10, 60)])
    assert [line.boxes[0].text for line in lines] == ["one", "two", "three"]


def test_wide_gaps_become_pauses() -> None:
    line = group_lines([box("FIGHT", 0, 0), box("ACT", 300, 0)])[0]
    assert line.text() == "FIGHT, ACT"
    close = group_lines([box("The", 0, 0, width=40), box("air", 45, 0, width=40)])[0]
    assert close.text() == "The air"


def test_speech_text_strips_bullets_and_adds_pauses() -> None:
    boxes = [
        box("*", 10, 10, width=10),
        box("The air crackles with freedom", 30, 10, width=400),
        box("FIGHT", 10, 200),
        box("ACT", 300, 200),
    ]
    assert speech_text(group_lines(boxes)) == "The air crackles with freedom. FIGHT, ACT."


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("* Hello", "Hello."),
        ("Really?", "Really?"),
        ("***", ""),
        ("  ", ""),
        ("HP 120/160", "HP 120/160."),
    ],
)
def test_clean_line_for_speech(raw: str, spoken: str) -> None:
    assert clean_line_for_speech(raw) == spoken


def test_empty_input() -> None:
    assert group_lines([]) == []
    assert speech_text([]) == ""


def test_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        group_lines([], min_overlap=0.0)
