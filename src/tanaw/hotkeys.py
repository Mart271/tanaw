"""Global hotkeys.

``parse_hotkey`` and ``HotkeyMatcher`` are pure and unit-tested. ``HotkeyListener``
is the thin pynput adapter. It matches on Windows virtual-key codes rather than
characters, because while Ctrl+Alt is held Windows may report a letter key with
no character at all (pynput's own ``HotKey`` helper then never fires).

Callbacks run on pynput's listener thread and must return immediately.
"""

from __future__ import annotations

import enum
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pynput import keyboard

logger = logging.getLogger(__name__)


class Action(enum.Enum):
    READ = "read"
    STOP = "stop"
    REPEAT = "repeat"
    PAUSE = "pause"


class Modifier(enum.Enum):
    CTRL = "ctrl"
    ALT = "alt"
    SHIFT = "shift"


# Windows virtual-key codes for modifiers (generic, left, right).
_MODIFIER_VKS: dict[int, Modifier] = {
    0x11: Modifier.CTRL, 0xA2: Modifier.CTRL, 0xA3: Modifier.CTRL,
    0x12: Modifier.ALT, 0xA4: Modifier.ALT, 0xA5: Modifier.ALT,
    0x10: Modifier.SHIFT, 0xA0: Modifier.SHIFT, 0xA1: Modifier.SHIFT,
}

_NAMED_KEYS: dict[str, int] = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "page_down": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    **{f"f{i}": 0x70 + i - 1 for i in range(1, 13)},
}
_KEY_ALIASES = {"return": "enter", "escape": "esc", "pgup": "page_up", "pgdn": "page_down"}


def parse_key_name(name: str) -> int:
    """Virtual-key code for one non-modifier key: "up", "enter", "z", "f5", ..."""
    if not isinstance(name, str):
        raise ValueError("Key name must be a string")
    key = name.strip().strip("<>").lower()
    key = _KEY_ALIASES.get(key, key)
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return ord(key.upper())
    raise ValueError(f"Unknown key {name!r}")


@dataclass(frozen=True, slots=True)
class Hotkey:
    modifiers: frozenset[Modifier]
    vk: int

    def describe(self) -> str:
        names = [m.value.capitalize() for m in Modifier if m in self.modifiers]
        key = next((n for n, v in _NAMED_KEYS.items() if v == self.vk), chr(self.vk))
        return "+".join([*names, key.capitalize() if len(key) > 1 else key.upper()])


def parse_hotkey(spec: str) -> Hotkey:
    """Parse "ctrl+alt+r" (pynput-style "<ctrl>+<alt>+r" also accepted).

    Exactly one non-modifier key, and Ctrl or Alt must be part of it, so a
    hotkey can never steal a plain key the game needs.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("Hotkey must be a non-empty string like 'ctrl+alt+r'.")
    parts = [p.strip().strip("<>").lower() for p in spec.split("+")]
    if any(not p for p in parts):
        raise ValueError(f"Malformed hotkey: {spec!r}")
    modifiers: set[Modifier] = set()
    keys: list[int] = []
    for part in parts:
        if part in ("ctrl", "control"):
            mod = Modifier.CTRL
        elif part == "alt":
            mod = Modifier.ALT
        elif part == "shift":
            mod = Modifier.SHIFT
        else:
            try:
                keys.append(parse_key_name(part))
            except ValueError:
                raise ValueError(f"Unknown key {part!r} in hotkey {spec!r}") from None
            continue
        if mod in modifiers:
            raise ValueError(f"Repeated modifier in hotkey {spec!r}")
        modifiers.add(mod)
    if len(keys) != 1:
        raise ValueError(f"Hotkey {spec!r} must have exactly one non-modifier key")
    if not modifiers & {Modifier.CTRL, Modifier.ALT}:
        raise ValueError(f"Hotkey {spec!r} must include ctrl or alt")
    return Hotkey(frozenset(modifiers), keys[0])


class HotkeyMatcher:
    """Turns a stream of key-down/key-up virtual-key codes into actions."""

    def __init__(self, bindings: Mapping[Action, Hotkey]) -> None:
        by_hotkey: dict[Hotkey, Action] = {}
        for action, hotkey in bindings.items():
            if hotkey in by_hotkey:
                raise ValueError(
                    f"{hotkey.describe()} is bound to both {by_hotkey[hotkey].value} "
                    f"and {action.value}"
                )
            by_hotkey[hotkey] = action
        self._by_hotkey = by_hotkey
        self._held_modifier_vks: set[int] = set()
        self._held_keys: set[int] = set()

    def _modifiers(self) -> frozenset[Modifier]:
        return frozenset(_MODIFIER_VKS[vk] for vk in self._held_modifier_vks)

    def press(self, vk: int) -> Action | None:
        if vk in _MODIFIER_VKS:
            self._held_modifier_vks.add(vk)
            return None
        if vk in self._held_keys:
            return None  # auto-repeat while the key is held: fire once only
        self._held_keys.add(vk)
        return self._by_hotkey.get(Hotkey(self._modifiers(), vk))

    def release(self, vk: int) -> None:
        self._held_modifier_vks.discard(vk)
        self._held_keys.discard(vk)


def key_to_vk(key: keyboard.Key | keyboard.KeyCode | None) -> int | None:
    """Best-effort Windows virtual-key code for a pynput key."""
    if key is None:
        return None
    code = key.value if isinstance(key, keyboard.Key) else key
    if code.vk is not None:
        return int(code.vk)
    if code.char and len(code.char) == 1 and code.char.isascii() and code.char.isalnum():
        return ord(code.char.upper())
    return None


ActionCallback = Callable[[Action, float], None]


class HotkeyListener:
    """Global keyboard hook. ``on_action(action, perf_counter_at_press)`` must not block."""

    def __init__(self, bindings: Mapping[Action, Hotkey], on_action: ActionCallback) -> None:
        self._matcher = HotkeyMatcher(bindings)
        self._on_action = on_action
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)

    def start(self) -> None:
        self._listener.start()
        self._listener.wait()
        logger.info("hotkey listener started")

    def stop(self) -> None:
        self._listener.stop()

    def _press(self, key: keyboard.Key | keyboard.KeyCode | None, _injected: bool) -> None:
        pressed_at = time.perf_counter()
        vk = key_to_vk(key)
        if vk is None:
            return
        action = self._matcher.press(vk)
        if action is not None:
            try:
                self._on_action(action, pressed_at)
            except Exception:  # never let an error kill the global hook
                logger.exception("hotkey handler failed for %s", action.value)

    def _release(self, key: keyboard.Key | keyboard.KeyCode | None, _injected: bool) -> None:
        vk = key_to_vk(key)
        if vk is not None:
            self._matcher.release(vk)
