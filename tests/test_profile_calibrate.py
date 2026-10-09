"""Profile validation and calibration logic, on synthetic images only (no game art)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from tanaw.calibrate import (
    CalibrationCancelledError,
    PickFn,
    build_profile,
    drag_to_rect,
    fit_scale,
    profile_name_from_path,
    row_offset,
    sample_hsv_range,
    write_calibration,
)
from tanaw.frames import Frame, Rect
from tanaw.profile import (
    CursorTemplateStrategy,
    FocusProfile,
    HighlightColorStrategy,
    ProfileError,
    RectModel,
    RowOffset,
    load_profile,
    resolve_template_path,
)


def synthetic_menu() -> Frame:
    """Black screen, a red 'cursor' square, yellow selected text, white other text."""
    img: Frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(img, (200, 200), (215, 215), (0, 0, 255), -1)  # red cursor
    cv2.putText(img, "Yes", (230, 216), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    cv2.putText(img, "No", (230, 256), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return img


def make_profile(**overrides: object) -> FocusProfile:
    data: dict[str, object] = {
        "name": "test-game",
        "window": "Test Game",
        "client_width": 640,
        "client_height": 480,
        "menu_region": {"left": 100, "top": 150, "width": 400, "height": 200},
        "strategy": {"kind": "cursor_template", "template_path": "local/test-cursor.png",
                     "row": {"dx": -4, "dy": -6, "width": 160, "height": 30}},
    }
    data.update(overrides)
    return FocusProfile.model_validate(data)


# --- pure calibration helpers ----------------------------------------------------------


def test_fit_scale() -> None:
    assert fit_scale(1280, 960, 1400, 850) == pytest.approx(850 / 960)
    assert fit_scale(640, 480, 1400, 850) == 1.0


def test_drag_to_rect_scales_normalises_and_clamps() -> None:
    assert drag_to_rect(50, 40, 10, 20, 0.5, 640, 480) == Rect(20, 40, 80, 40)
    assert drag_to_rect(-10, -10, 2000, 2000, 1.0, 640, 480) == Rect(0, 0, 640, 480)
    assert drag_to_rect(10, 10, 11, 11, 1.0, 640, 480) is None  # accidental click


def test_row_offset() -> None:
    assert row_offset(Rect(200, 200, 16, 16), Rect(196, 194, 160, 30)) == RowOffset(
        dx=-4, dy=-6, width=160, height=30
    )


def test_sample_hsv_range_on_coloured_text() -> None:
    img = synthetic_menu()
    low, high = sample_hsv_range(img[195:225, 225:300])  # the yellow "Yes"
    # OpenCV yellow is hue 30; range should bracket it and exclude the black background.
    assert low[0] <= 30 <= high[0]
    assert low[2] > 0


def test_sample_hsv_range_rejects_dark_box() -> None:
    with pytest.raises(ValueError):
        sample_hsv_range(np.zeros((20, 20, 3), dtype=np.uint8))


def test_profile_name_from_path() -> None:
    assert profile_name_from_path(Path("profiles/DELTARUNE ch1.json")) == "deltarune-ch1"


# --- profile validation ------------------------------------------------------------------


def test_valid_profile_round_trips() -> None:
    p = make_profile()
    again = FocusProfile.model_validate_json(p.model_dump_json())
    assert again == p
    assert 0x26 in p.nav_vks()  # "up"


@pytest.mark.parametrize(
    "overrides",
    [
        {"menu_region": {"left": 500, "top": 150, "width": 400, "height": 200}},  # off right
        {"menu_region": {"left": -1, "top": 0, "width": 10, "height": 10}},
        {"name": "Bad Name!"},
        {"nav_keys": ["up", "bogus"]},
        {"nav_keys": ["up", "UP"]},
        {"nav_keys": []},
        {"strategy": {"kind": "highlight_color", "hsv_low": [200, 0, 0],
                      "hsv_high": [10, 255, 255]}},
        {"strategy": {"kind": "highlight_color", "hsv_low": [20, 200, 0],
                      "hsv_high": [40, 100, 255]}},
        {"strategy": {"kind": "teleport"}},
        {"unexpected": 1},
    ],
)
def test_invalid_profiles_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_profile(**overrides)


def test_hue_wrap_is_allowed_for_reds() -> None:
    p = make_profile(strategy={"kind": "highlight_color", "hsv_low": [170, 100, 100],
                               "hsv_high": [10, 255, 255]})
    assert isinstance(p.strategy, HighlightColorStrategy)


def test_template_path_cannot_escape_profile_folder(tmp_path: Path) -> None:
    profile_file = tmp_path / "profiles" / "game.json"
    with pytest.raises(ProfileError):
        resolve_template_path(profile_file, "../../secret.png")
    with pytest.raises(ProfileError):
        resolve_template_path(profile_file, "local/cursor.exe")
    assert resolve_template_path(profile_file, "local/c.png").name == "c.png"


# --- end-to-end calibration with a scripted "helper" -------------------------------------


def scripted(*rects: Rect | None) -> PickFn:
    answers = list(rects)

    def pick(image: Frame, prompt: str, allow_whole: bool) -> Rect | None:
        return answers.pop(0)

    return pick


def test_cursor_calibration_writes_profile_and_template(tmp_path: Path) -> None:
    img = synthetic_menu()
    out = tmp_path / "profiles" / "test-game.json"
    pick = scripted(Rect(100, 150, 400, 200), Rect(200, 200, 16, 16), Rect(196, 194, 160, 30))
    profile, template = build_profile(img, name="test-game", window="Test Game",
                                      strategy="cursor_template", out_path=out,
                                      pick=pick)
    write_calibration(profile, template, out)

    loaded = load_profile(out)
    assert loaded.template is not None
    assert loaded.template.shape == (16, 16, 3)
    assert isinstance(loaded.profile.strategy, CursorTemplateStrategy)
    assert loaded.profile.strategy.row == RowOffset(dx=-4, dy=-6, width=160, height=30)
    assert (tmp_path / "profiles" / "local" / "test-game-cursor.png").is_file()
    assert "local/test-game-cursor.png" in json.loads(out.read_text())["strategy"]["template_path"]


def test_cursor_outside_menu_area_is_rejected(tmp_path: Path) -> None:
    pick = scripted(Rect(0, 0, 100, 100), Rect(200, 200, 16, 16), Rect(196, 194, 160, 30))
    with pytest.raises(ProfileError):
        build_profile(synthetic_menu(), name="g", window="G", strategy="cursor_template",
                      out_path=tmp_path / "g.json", pick=pick)


def test_cancel_saves_nothing(tmp_path: Path) -> None:
    with pytest.raises(CalibrationCancelledError):
        build_profile(synthetic_menu(), name="g", window="G", strategy="cursor_template",
                      out_path=tmp_path / "g.json", pick=scripted(None))
    assert not any(tmp_path.iterdir())


def test_highlight_calibration(tmp_path: Path) -> None:
    pick = scripted(Rect(100, 150, 400, 200), Rect(225, 195, 75, 30))
    profile, template = build_profile(synthetic_menu(), name="g", window="G",
                                      strategy="highlight_color", out_path=tmp_path / "g.json",
                                      pick=pick)
    assert template is None
    assert isinstance(profile.strategy, HighlightColorStrategy)


def test_missing_template_gives_clear_error(tmp_path: Path) -> None:
    out = tmp_path / "game.json"
    out.write_text(make_profile().model_dump_json(), encoding="utf-8")
    with pytest.raises(ProfileError, match="missing"):
        load_profile(out)


def test_rect_model_round_trip() -> None:
    assert RectModel.from_rect(Rect(1, 2, 3, 4)).to_rect() == Rect(1, 2, 3, 4)
