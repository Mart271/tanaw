from __future__ import annotations

import pytest
from pynput.keyboard import Key, KeyCode

from tanaw.hotkeys import (
    Action,
    Hotkey,
    HotkeyMatcher,
    Modifier,
    key_to_vk,
    parse_hotkey,
)

CTRL_L, ALT_L, ALT_R, SHIFT_L = 0xA2, 0xA4, 0xA5, 0xA0
VK_R, VK_S, VK_SPACE = 0x52, 0x53, 0x20

DEFAULTS = {
    Action.READ: parse_hotkey("ctrl+alt+r"),
    Action.STOP: parse_hotkey("ctrl+alt+s"),
    Action.REPEAT: parse_hotkey("ctrl+alt+space"),
    Action.PAUSE: parse_hotkey("ctrl+alt+p"),
}


def test_parse_hotkey() -> None:
    assert parse_hotkey("ctrl+alt+r") == Hotkey(frozenset({Modifier.CTRL, Modifier.ALT}), VK_R)
    assert parse_hotkey("<ctrl>+<alt>+R") == parse_hotkey("Ctrl + Alt + r")
    assert parse_hotkey("ctrl+alt+space").vk == VK_SPACE
    assert parse_hotkey("alt+f5").vk == 0x74
    assert parse_hotkey("ctrl+alt+space").describe() == "Ctrl+Alt+Space"


@pytest.mark.parametrize(
    "bad",
    ["", "r", "shift+r", "ctrl+alt", "ctrl+alt+r+s", "ctrl+ctrl+r", "ctrl+alt+bogus",
     "ctrl++r", "ctrl+alt+é"],
)
def test_parse_hotkey_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_ctrl_alt_letter_fires_once() -> None:
    m = HotkeyMatcher(DEFAULTS)
    assert m.press(CTRL_L) is None
    assert m.press(ALT_L) is None
    assert m.press(VK_R) is Action.READ
    assert m.press(VK_R) is None  # auto-repeat
    m.release(VK_R)
    assert m.press(VK_R) is Action.READ


def test_modifier_order_and_side_do_not_matter() -> None:
    m = HotkeyMatcher(DEFAULTS)
    m.press(ALT_R)
    m.press(CTRL_L)
    assert m.press(VK_SPACE) is Action.REPEAT


def test_extra_or_missing_modifiers_do_not_fire() -> None:
    m = HotkeyMatcher(DEFAULTS)
    assert m.press(VK_R) is None  # plain R is the game's
    m.release(VK_R)
    m.press(CTRL_L)
    assert m.press(VK_R) is None  # ctrl+r only
    m.release(VK_R)
    m.press(ALT_L)
    m.press(SHIFT_L)
    assert m.press(VK_R) is None  # ctrl+alt+shift+r
    m.release(SHIFT_L)
    m.release(VK_R)
    assert m.press(VK_S) is Action.STOP


def test_releasing_modifier_stops_matching() -> None:
    m = HotkeyMatcher(DEFAULTS)
    m.press(CTRL_L)
    m.press(ALT_L)
    m.release(ALT_L)
    assert m.press(VK_R) is None


def test_duplicate_bindings_rejected() -> None:
    with pytest.raises(ValueError):
        HotkeyMatcher({Action.READ: parse_hotkey("ctrl+alt+r"),
                       Action.STOP: parse_hotkey("alt+ctrl+R")})


def test_key_to_vk() -> None:
    assert key_to_vk(KeyCode.from_vk(VK_R)) == VK_R  # what Windows sends with ctrl+alt
    assert key_to_vk(KeyCode.from_char("r")) == VK_R
    assert key_to_vk(Key.space) == VK_SPACE
    assert key_to_vk(None) is None


NAV = frozenset({0x26, 0x28, 0x5A})  # up, down, z
VK_UP, VK_Z = 0x26, 0x5A


def test_nav_keys_fire_including_auto_repeat() -> None:
    m = HotkeyMatcher(DEFAULTS, NAV)
    assert m.press(VK_UP) is Action.NAV
    assert m.press(VK_UP) is Action.NAV  # held arrow scrolls the menu
    assert m.press(VK_Z) is Action.NAV
    assert m.press(VK_R) is None  # not a nav key


def test_nav_keys_ignored_while_ctrl_or_alt_held() -> None:
    m = HotkeyMatcher(DEFAULTS, NAV)
    m.press(CTRL_L)
    assert m.press(VK_UP) is None
    m.release(CTRL_L)
    m.release(VK_UP)
    m.press(SHIFT_L)
    assert m.press(VK_UP) is Action.NAV  # shift alone doesn't block nav


def test_focus_toggle_hotkey() -> None:
    m = HotkeyMatcher({**DEFAULTS, Action.FOCUS_TOGGLE: parse_hotkey("ctrl+alt+f")}, NAV)
    m.press(CTRL_L)
    m.press(ALT_L)
    assert m.press(0x46) is Action.FOCUS_TOGGLE


def test_nav_cannot_be_bound_as_hotkey() -> None:
    with pytest.raises(ValueError):
        HotkeyMatcher({Action.NAV: parse_hotkey("ctrl+alt+n")})
