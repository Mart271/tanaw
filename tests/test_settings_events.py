from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tanaw.events import EventLog
from tanaw.hotkeys import Action
from tanaw.settings import AppSettings, HotkeySettings


def test_defaults_are_valid() -> None:
    s = AppSettings(window="  DELTARUNE ")
    assert s.window == "DELTARUNE"
    assert set(s.hotkeys.bindings()) == set(Action)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"window": ""},
        {"window": "x", "min_confidence": 1.5},
        {"window": "x", "upscale": 0.5},
        {"window": "x", "upscale": 8},
        {"window": "x", "speech_rate": 20},
        {"window": "x", "unknown_option": True},
    ],
)
def test_invalid_settings_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AppSettings.model_validate(kwargs)


def test_hotkeys_validated() -> None:
    with pytest.raises(ValidationError):
        HotkeySettings(read="r")  # no modifier: would steal a game key
    with pytest.raises(ValidationError):
        HotkeySettings(read="ctrl+alt+s")  # clashes with stop


def test_event_log_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "session.jsonl"
    with EventLog(path) as log:
        log.write("read", boxes=3, ocr_ms=12.3456, settled=True, kind="CaptureError")
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["event"] == "read"
    assert record["boxes"] == 3
    assert record["ocr_ms"] == 12.346
    assert record["kind"] == "CaptureError"


def test_event_log_refuses_free_text(tmp_path: Path) -> None:
    with EventLog(tmp_path / "s.jsonl") as log, pytest.raises(ValueError):
        log.write("read", text="The air crackles with freedom.")
