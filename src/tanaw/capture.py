"""Find the game window and capture its client area (Windows only).

Only the selected window's client rectangle is captured, never the whole
desktop. Frames stay in memory; the debug CLI writes a PNG only when ``--save``
is passed.

Debug CLI::

    python -m tanaw.capture --list
    python -m tanaw.capture --window "DELTARUNE" --save
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import mss
import numpy as np
import win32gui

from tanaw import netguard
from tanaw.frames import Frame, Rect, settle

logger = logging.getLogger(__name__)

DEBUG_DIR = Path("debug")
MAX_TITLE_QUERY_LEN = 256

# Windows whose titles often contain the game's name but are never the game:
# terminals (the command line itself says "DELTARUNE"), and Explorer folders.
_EXCLUDED_CLASSES = frozenset(
    {
        "ConsoleWindowClass",  # classic console host
        "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
        "CabinetWClass",  # File Explorer folder window
    }
)

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the pseudo-handle -4.
_DPI_CONTEXT_PER_MONITOR_V2 = -4
_PROCESS_PER_MONITOR_DPI_AWARE = 2


class CaptureError(RuntimeError):
    """The window could not be captured. The message is safe to speak aloud."""


class WindowNotFoundError(CaptureError):
    pass


class AmbiguousWindowError(CaptureError):
    pass


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    image: Frame  # (h, w, 3) BGR
    screen_rect: Rect  # where the captured area is on the virtual desktop, physical pixels
    foreground: bool  # False means another window may be covering the game
    elapsed_s: float


def enable_dpi_awareness() -> str:
    """Make window rectangles report real (physical) pixels on scaled displays.

    Must run before any window or screen query. Returns which mode is active.
    Calling it twice is harmless: Windows refuses a second change, and we just
    report the current mode.
    """
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_DPI_CONTEXT_PER_MONITOR_V2))
    except AttributeError:  # Windows older than 10 1703
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE)
        except (AttributeError, OSError):
            user32.SetProcessDPIAware()
    return dpi_awareness_mode()


def dpi_awareness_mode() -> str:
    user32 = ctypes.windll.user32
    try:
        ctx = user32.GetThreadDpiAwarenessContext()
        awareness = int(user32.GetAwarenessFromDpiAwarenessContext(ctx))
    except AttributeError:
        return "unknown"
    return {0: "unaware", 1: "system", 2: "per-monitor"}.get(awareness, f"unknown({awareness})")


def _own_console_hwnd() -> int:
    return int(ctypes.windll.kernel32.GetConsoleWindow() or 0)


def list_windows() -> list[WindowInfo]:
    """Visible top-level windows that have a title."""
    found: list[WindowInfo] = []

    def on_window(hwnd: int, _extra: None) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                found.append(WindowInfo(hwnd, title, win32gui.GetClassName(hwnd) or ""))
        return True

    win32gui.EnumWindows(on_window, None)
    return found


def validate_title_query(query: str) -> str:
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("Window title must not be empty.")
    if len(cleaned) > MAX_TITLE_QUERY_LEN:
        raise ValueError(f"Window title must be at most {MAX_TITLE_QUERY_LEN} characters.")
    if any(ord(ch) < 32 for ch in cleaned):
        raise ValueError("Window title must not contain control characters.")
    return cleaned


def choose_window(candidates: list[WindowInfo], query: str, exclude_hwnds: set[int]) -> WindowInfo:
    """Pick the one window whose title contains ``query`` (case-insensitive).

    Terminals and Explorer folders are skipped. If several windows still match,
    an exact title match wins; otherwise we refuse rather than guess.
    """
    needle = validate_title_query(query).casefold()
    matches = [
        w
        for w in candidates
        if needle in w.title.casefold()
        and w.class_name not in _EXCLUDED_CLASSES
        and w.hwnd not in exclude_hwnds
    ]
    if not matches:
        raise WindowNotFoundError(
            f'No window with "{query}" in its title. Is the game running and not minimised?'
        )
    if len(matches) == 1:
        return matches[0]
    exact = [w for w in matches if w.title.strip().casefold() == needle]
    if len(exact) == 1:
        return exact[0]
    titles = "; ".join(f'"{w.title}"' for w in matches)
    raise AmbiguousWindowError(
        f'Several windows match "{query}": {titles}. Use a more specific title.'
    )


def find_window(query: str) -> WindowInfo:
    return choose_window(list_windows(), query, exclude_hwnds={_own_console_hwnd()})


def client_rect_on_screen(hwnd: int) -> Rect:
    """The window's client area (no title bar or borders) in screen pixels."""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    screen_left, screen_top = win32gui.ClientToScreen(hwnd, (left, top))
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise CaptureError("The game window has no visible area. Is it minimised?")
    return Rect(screen_left, screen_top, width, height)


class WindowCapturer:
    """Captures one window's client area. Create and use it on a single thread."""

    def __init__(self, window: WindowInfo) -> None:
        self.window = window
        self._sct = mss.MSS()

    def __enter__(self) -> WindowCapturer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._sct.close()

    def _virtual_screen(self) -> Rect:
        mon = self._sct.monitors[0]  # index 0 = bounding box of all monitors
        return Rect(int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"]))

    def grab(self, region: Rect | None = None) -> CapturedFrame:
        """Capture the client area, or ``region`` given relative to the client area."""
        start = time.perf_counter()
        hwnd = self.window.hwnd
        if not win32gui.IsWindow(hwnd):
            raise CaptureError("The game window was closed.")
        if win32gui.IsIconic(hwnd):
            raise CaptureError("The game window is minimised.")

        client = client_rect_on_screen(hwnd)
        target = client
        if region is not None:
            target_rel = Rect(client.left + region.left, client.top + region.top,
                              region.width, region.height)
            clipped_to_client = client.intersect(target_rel)
            if clipped_to_client is None:
                raise CaptureError("The capture region is outside the game window.")
            target = clipped_to_client
        visible = target.intersect(self._virtual_screen())
        if visible is None:
            raise CaptureError("The game window is off screen.")

        shot = self._sct.grab(
            {"left": visible.left, "top": visible.top,
             "width": visible.width, "height": visible.height}
        )
        bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        image: Frame = np.ascontiguousarray(bgra[:, :, :3])
        foreground = win32gui.GetForegroundWindow() == hwnd
        elapsed = time.perf_counter() - start
        logger.debug(
            "captured %dx%d foreground=%s in %.1f ms",
            visible.width, visible.height, foreground, elapsed * 1000,
        )
        return CapturedFrame(image, visible, foreground, elapsed)

    def grab_settled(
        self, region: Rect | None = None, *, timeout_s: float = 0.3
    ) -> tuple[CapturedFrame, bool]:
        """Capture once the image stops changing. Returns (frame, settled)."""
        last: list[CapturedFrame] = []

        def grab_image() -> Frame:
            captured = self.grab(region)
            last.append(captured)
            return captured.image

        result = settle(grab_image, timeout_s=timeout_s)
        return last[-1], result.settled


def _save_png(image: Frame, directory: Path) -> Path:
    import cv2  # local import: only the debug path writes files

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"capture-{time.strftime('%Y%m%d-%H%M%S')}.png"
    if not cv2.imwrite(str(path), image):
        raise CaptureError(f"Could not write {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tanaw.capture", description="Capture a game window once (debug tool)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--window", help="Case-insensitive part of the game window's title")
    group.add_argument("--list", action="store_true", help="List visible window titles and exit")
    parser.add_argument("--save", action="store_true", help=f"Write the capture to ./{DEBUG_DIR}/")
    parser.add_argument("--settle", action="store_true", help="Wait for the image to stop changing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    netguard.install()
    print(f"DPI awareness: {enable_dpi_awareness()}")

    if args.list:
        for w in list_windows():
            print(f"{w.hwnd:>10}  {w.class_name:<32}  {w.title}")
        return 0

    try:
        window = find_window(args.window)
        with WindowCapturer(window) as capturer:
            if args.settle:
                captured, settled = capturer.grab_settled()
                print(f"settled: {settled}")
            else:
                captured = capturer.grab()
    except (CaptureError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    r = captured.screen_rect
    print(f'window: "{window.title}" (hwnd {window.hwnd}, class {window.class_name})')
    print(f"client area on screen: left={r.left} top={r.top} size={r.width}x{r.height}")
    print(f"frame shape: {captured.image.shape}  foreground: {captured.foreground}")
    print(f"capture time: {captured.elapsed_s * 1000:.1f} ms")
    if not captured.foreground:
        print("Warning: game window is not in front; another window may be covering it.")
    if args.save:
        print(f"saved: {_save_png(captured.image, DEBUG_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
