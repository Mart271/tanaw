"""Validated application settings (pydantic v2)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tanaw.capture import validate_title_query
from tanaw.hotkeys import Action, Hotkey, HotkeyMatcher, parse_hotkey


class HotkeySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    read: str = "ctrl+alt+r"
    stop: str = "ctrl+alt+s"
    repeat: str = "ctrl+alt+space"
    pause: str = "ctrl+alt+p"

    @field_validator("read", "stop", "repeat", "pause")
    @classmethod
    def _valid_hotkey(cls, value: str) -> str:
        parse_hotkey(value)  # raises ValueError with a readable message
        return value

    @model_validator(mode="after")
    def _no_duplicates(self) -> Self:
        HotkeyMatcher(self.bindings())  # raises ValueError on a clash
        return self

    def bindings(self) -> dict[Action, Hotkey]:
        return {
            Action.READ: parse_hotkey(self.read),
            Action.STOP: parse_hotkey(self.stop),
            Action.REPEAT: parse_hotkey(self.repeat),
            Action.PAUSE: parse_hotkey(self.pause),
        }


class AppSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    window: str = Field(description="Case-insensitive part of the game window's title")
    debug_captures: bool = False
    verbose_text: bool = False
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    upscale: float = Field(default=1.0, ge=1.0, le=4.0)
    interpolation: Literal["cubic", "nearest"] = "cubic"
    speech_rate: int = Field(default=0, ge=-10, le=10)
    settle_timeout_s: float = Field(default=0.3, ge=0.0, le=2.0)
    capture_method: Literal["auto", "window", "screen"] = "auto"
    hotkeys: HotkeySettings = HotkeySettings()

    @field_validator("window")
    @classmethod
    def _valid_window(cls, value: str) -> str:
        return validate_title_query(value)
