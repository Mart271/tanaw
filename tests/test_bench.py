"""Benchmark helpers, on synthetic data only."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tanaw.bench import (
    FocusLabel,
    OcrLabel,
    character_error_rate,
    classify_focus,
    levenshtein,
    load_events,
    load_labels,
    run_ocr_case,
    speech_latencies,
    summarize,
)
from tanaw.focus import Decision
from tanaw.frames import Frame
from tanaw.ocr import OcrEngine


def test_levenshtein_and_cer() -> None:
    assert levenshtein("No", "Ho") == 1
    assert levenshtein("", "abc") == 3
    assert levenshtein("kitten", "sitting") == 3
    assert character_error_rate("No", "Ho") == 0.5
    assert character_error_rate("Yes", " Yes ") == 0.0
    assert character_error_rate("", "") == 0.0


def test_summarize() -> None:
    assert summarize([]).count == 0
    s = summarize([30.0, 10.0, 20.0])
    assert (s.count, s.median, s.worst, s.best, s.p90) == (3, 20.0, 30.0, 10.0, None)
    s10 = summarize([float(v) for v in range(1, 11)])
    assert s10.p90 == 9.0  # nearest-rank


def test_speech_latencies_from_event_log(tmp_path: Path) -> None:
    log = tmp_path / "session-1.jsonl"
    records = [
        {"event": "speech_start", "mode": "focus", "latency_ms": 120.5},
        {"event": "speech_start", "mode": "focus", "latency_ms": 180.0},
        {"event": "speech_start", "mode": "read", "latency_ms": 900.0},
        {"event": "focus", "outcome": "speak"},
        {"event": "speech_start", "mode": "focus", "latency_ms": -1},  # clock oddity: ignored
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n{broken", encoding="utf-8")
    by_mode = speech_latencies(load_events([log]))
    assert by_mode == {"focus": [120.5, 180.0], "read": [900.0]}


@pytest.mark.parametrize(
    ("expected", "decision", "text", "outcome"),
    [
        ("Yes", Decision.SPEAK, "Yes", "correct"),
        ("No", Decision.SPEAK, "Ho", "wrong"),
        ("No", Decision.UNCLEAR, None, "unclear"),
        ("No", Decision.SILENT, None, "missed"),
        (None, Decision.SILENT, None, "correct"),
        (None, Decision.SPEAK, "Yes", "false_alarm"),
    ],
)
def test_classify_focus(expected: str | None, decision: Decision, text: str | None,
                        outcome: str) -> None:
    assert classify_focus(expected, decision, text) == outcome


def test_labels_validation(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({
        "profile": "p.json",
        "focus": [{"image": "a.png", "expected": "Yes"}, {"image": "b.png", "expected": None}],
        "ocr": [{"image": "c.png", "expected": "HP 120", "rect": [0, 0, 10, 10]}],
    }), encoding="utf-8")
    labels = load_labels(path)
    assert labels.focus[1] == FocusLabel(image="b.png", expected=None)
    path.write_text(json.dumps({"ocr": [{"image": "c.png"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_labels(path)  # missing "expected"


def test_run_ocr_case_on_synthetic_image() -> None:
    img: Frame = np.full((120, 400, 3), 255, dtype=np.uint8)
    cv2.putText(img, "HP 120", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    engine = OcrEngine()
    case = run_ocr_case(engine, img, OcrLabel(image="x", expected="HP 120"),
                        upscale=1.0, min_confidence=0.6)
    assert case.exact
    assert case.cer == 0.0
