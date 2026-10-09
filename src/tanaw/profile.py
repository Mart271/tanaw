"""Per-game Focus Mode profiles: schema, validation, loading (pydantic v2).

A profile is created once by a sighted helper with ``python -m tanaw.calibrate``.
The JSON holds only numbers and settings. Images cut from the game (the cursor
template) live in ``profiles/local/`` which is gitignored, because game art is
copyrighted.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal, Self

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tanaw.frames import Frame, Rect
from tanaw.hotkeys import parse_key_name

PROFILE_VERSION: Literal[1] = 1
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class ProfileError(ValueError):
    """The profile is invalid. The message is safe to speak aloud."""


class RectModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    left: int = Field(ge=0)
    top: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    def to_rect(self) -> Rect:
        return Rect(self.left, self.top, self.width, self.height)

    @classmethod
    def from_rect(cls, rect: Rect) -> RectModel:
        return cls(left=rect.left, top=rect.top, width=rect.width, height=rect.height)


class RowOffset(BaseModel):
    """The selected row's box relative to the cursor's top-left corner (may be negative)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dx: int = Field(ge=-4000, le=4000)
    dy: int = Field(ge=-4000, le=4000)
    width: int = Field(gt=0, le=4000)
    height: int = Field(gt=0, le=4000)


Hsv = tuple[int, int, int]


def _check_hsv(value: Hsv) -> Hsv:
    h, s, v = value
    if not (0 <= h <= 179 and 0 <= s <= 255 and 0 <= v <= 255):
        raise ValueError(f"HSV values must be H 0-179, S 0-255, V 0-255; got {value}")
    return value


class CursorTemplateStrategy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["cursor_template"] = "cursor_template"
    template_path: str = Field(min_length=1, max_length=200)  # relative to the profile file
    match_threshold: float = Field(default=0.8, ge=0.5, le=1.0)
    row: RowOffset


class HighlightColorStrategy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["highlight_color"] = "highlight_color"
    # OpenCV HSV (H 0-179). If hsv_low[0] > hsv_high[0] the hue range wraps (reds).
    hsv_low: Hsv
    hsv_high: Hsv
    min_pixels: int = Field(default=30, ge=1, le=1_000_000)
    merge_gap: int = Field(default=12, ge=1, le=200)  # px gap still counted as one row
    padding: int = Field(default=4, ge=0, le=100)

    @field_validator("hsv_low", "hsv_high")
    @classmethod
    def _valid_hsv(cls, value: Hsv) -> Hsv:
        return _check_hsv(value)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.hsv_low[1] > self.hsv_high[1] or self.hsv_low[2] > self.hsv_high[2]:
            raise ValueError("hsv_low S and V must not exceed hsv_high S and V")
        return self


Strategy = Annotated[
    CursorTemplateStrategy | HighlightColorStrategy, Field(discriminator="kind")
]


class FocusOcrOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Pixel-art games are often drawn at 2x/3x; shrinking the row back to the native
    # size made DELTARUNE's "No" read correctly (it often read as "Ho" at 2x).
    downscale: int = Field(default=1, ge=1, le=4)
    pad: int = Field(default=16, ge=0, le=64)  # background border; OCR dislikes tight crops
    upscale: float = Field(default=1.0, ge=1.0, le=4.0)
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    # "threshold": light text on dark background -> black on white before OCR.
    preprocess: Literal["none", "threshold"] = "none"
    threshold_value: int = Field(default=60, ge=1, le=254)


class FocusTiming(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_delay_s: float = Field(default=0.05, ge=0.0, le=1.0)  # let the game redraw
    frame_interval_s: float = Field(default=0.035, ge=0.005, le=0.5)  # > one 30 fps frame
    change_timeout_s: float = Field(default=0.25, ge=0.0, le=2.0)
    settle_timeout_s: float = Field(default=0.3, ge=0.0, le=2.0)
    change_threshold: float = Field(default=0.001, ge=0.0, le=1.0)


DEFAULT_NAV_KEYS = ("up", "down", "left", "right", "enter", "esc")


class FocusProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = PROFILE_VERSION
    name: str
    window: str = Field(min_length=1, max_length=256)
    client_width: int = Field(gt=0, le=10_000)
    client_height: int = Field(gt=0, le=10_000)
    menu_region: RectModel
    strategy: Strategy
    ocr: FocusOcrOptions = FocusOcrOptions()
    timing: FocusTiming = FocusTiming()
    nav_keys: tuple[str, ...] = DEFAULT_NAV_KEYS

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("name must be lowercase letters, digits, '-' or '_' (max 40)")
        return value

    @field_validator("nav_keys")
    @classmethod
    def _valid_nav_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("nav_keys must not be empty")
        vks = [parse_key_name(k) for k in value]  # raises ValueError on unknown keys
        if len(set(vks)) != len(vks):
            raise ValueError("nav_keys contains duplicates")
        return value

    @model_validator(mode="after")
    def _region_inside_window(self) -> Self:
        r = self.menu_region
        if r.left + r.width > self.client_width or r.top + r.height > self.client_height:
            raise ValueError(
                f"menu_region {r.left},{r.top} {r.width}x{r.height} is outside the "
                f"{self.client_width}x{self.client_height} window"
            )
        return self

    def nav_vks(self) -> frozenset[int]:
        return frozenset(parse_key_name(k) for k in self.nav_keys)


def resolve_template_path(profile_file: Path, template_path: str) -> Path:
    """Resolve a template path relative to the profile, refusing to leave its folder."""
    base = profile_file.resolve().parent
    candidate = (base / template_path).resolve()
    if not candidate.is_relative_to(base):
        raise ProfileError("The cursor template path must stay inside the profiles folder.")
    if candidate.suffix.lower() != ".png":
        raise ProfileError("The cursor template must be a PNG file.")
    return candidate


class LoadedProfile:
    """A validated profile plus the images it refers to."""

    def __init__(self, profile: FocusProfile, template: Frame | None) -> None:
        self.profile = profile
        self.template = template


def load_profile(path: Path) -> LoadedProfile:
    if not path.is_file():
        raise ProfileError(f"Profile not found: {path}")
    if path.stat().st_size > 64_000:
        raise ProfileError("Profile file is too large to be a Tanaw profile.")
    try:
        profile = FocusProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        raise ProfileError(f"Profile {path.name} is invalid: {exc}") from exc

    template: Frame | None = None
    strategy = profile.strategy
    if isinstance(strategy, CursorTemplateStrategy):
        template_file = resolve_template_path(path, strategy.template_path)
        if not template_file.is_file():
            raise ProfileError(
                f"Cursor template {template_file.name} is missing. Run calibration on this "
                "computer (templates are not stored in the repo)."
            )
        loaded = cv2.imread(str(template_file), cv2.IMREAD_COLOR)
        if loaded is None:
            raise ProfileError(f"Cursor template {template_file.name} could not be read.")
        template = np.ascontiguousarray(loaded, dtype=np.uint8)
        th, tw = template.shape[:2]
        region = profile.menu_region
        if tw > region.width or th > region.height:
            raise ProfileError("The cursor template is bigger than the menu region.")
    return LoadedProfile(profile, template)


def save_profile(profile: FocusProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
