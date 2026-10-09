"""Create a Focus Mode profile for a game (run once by a sighted helper).

    python -m tanaw.calibrate --window "DELTARUNE" --out profiles/deltarune.json

Shows one captured frame and asks the helper to drag boxes with the mouse:

* cursor_template (default): 1) the menu area, 2) a tight box around the cursor
  sprite, 3) the whole selected row (cursor + its text).
* highlight_color: 1) the menu area, 2) the highlighted item.

Keys in the picker: Enter = confirm, Esc = cancel, A = whole window (step 1),
R = clear the box. The cursor image is saved to ``profiles/local/`` (gitignored:
game art is copyrighted); the JSON profile holds only numbers.

``cv2.selectROI`` is not available: rapidocr depends on the headless OpenCV
build, so the picker is drawn with tkinter (standard library).
"""

from __future__ import annotations

import argparse
import base64
import logging
import re
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from tanaw import netguard
from tanaw.capture import CaptureError, WindowCapturer, enable_dpi_awareness, find_window
from tanaw.frames import Frame, Rect
from tanaw.profile import (
    CursorTemplateStrategy,
    FocusProfile,
    HighlightColorStrategy,
    Hsv,
    ProfileError,
    RectModel,
    RowOffset,
    save_profile,
)

logger = logging.getLogger(__name__)

MIN_BOX = 3  # px; smaller drags are treated as accidental clicks
# Pixels darker than this are treated as background when sampling a highlight.
_DARK_V = 40


# --- pure helpers (unit-tested) ----------------------------------------------------------


def fit_scale(img_w: int, img_h: int, max_w: int, max_h: int) -> float:
    """Largest scale <= 1 that fits the image inside max_w x max_h."""
    if img_w <= 0 or img_h <= 0 or max_w <= 0 or max_h <= 0:
        raise ValueError("sizes must be positive")
    return min(1.0, max_w / img_w, max_h / img_h)


def drag_to_rect(
    x0: float, y0: float, x1: float, y1: float, scale: float, img_w: int, img_h: int
) -> Rect | None:
    """Convert a drag on the scaled preview into a Rect in image pixels (clamped)."""
    if scale <= 0:
        raise ValueError("scale must be positive")
    left = max(0, min(img_w, round(min(x0, x1) / scale)))
    top = max(0, min(img_h, round(min(y0, y1) / scale)))
    right = max(0, min(img_w, round(max(x0, x1) / scale)))
    bottom = max(0, min(img_h, round(max(y0, y1) / scale)))
    if right - left < MIN_BOX or bottom - top < MIN_BOX:
        return None
    return Rect(left, top, right - left, bottom - top)


def row_offset(cursor: Rect, row: Rect) -> RowOffset:
    return RowOffset(dx=row.left - cursor.left, dy=row.top - cursor.top,
                     width=row.width, height=row.height)


def crop(image: Frame, rect: Rect) -> Frame:
    out: Frame = np.ascontiguousarray(image[rect.top:rect.bottom, rect.left:rect.right])
    return out


def sample_hsv_range(box: Frame) -> tuple[Hsv, Hsv]:
    """HSV range of the dominant non-dark colour inside a highlighted item.

    Works for a coloured bar (it fills the box) and for coloured text on a dark
    background (dark pixels are ignored). Raises ValueError if the box is all dark.
    """
    hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.int32)
    lit = hsv[hsv[:, 2] >= _DARK_V]
    if len(lit) < 10:
        raise ValueError("The highlighted box is almost entirely dark; no highlight colour found.")
    s_med = int(np.median(lit[:, 1]))
    v_lo = int(np.percentile(lit[:, 2], 5))
    if s_med < 50:
        # Grey/white highlight: match on brightness only.
        s_hi = min(255, int(np.percentile(lit[:, 1], 95)) + 30)
        return (0, 0, max(0, v_lo - 30)), (179, s_hi, 255)
    # Coloured highlight: dominant hue +/- 10 (hue is circular, 0-179).
    hist = np.bincount(lit[:, 0], minlength=180)
    hue = int(np.argmax(hist))
    s_lo = max(0, int(np.percentile(lit[:, 1], 5)) - 40)
    low_h, high_h = (hue - 10) % 180, (hue + 10) % 180
    return (low_h, s_lo, max(0, v_lo - 40)), (high_h, 255, 255)


def profile_name_from_path(path: Path) -> str:
    name = re.sub(r"[^a-z0-9_-]", "-", path.stem.lower()).strip("-")[:40]
    return name or "game"


# --- tkinter picker ------------------------------------------------------------------------


class RectPicker:
    """Shows an image; the helper drags a rectangle and presses Enter."""

    def __init__(self, image: Frame, prompt: str, *, allow_whole: bool = False,
                 max_w: int = 1400, max_h: int = 850) -> None:
        self.image = image
        self.img_h, self.img_w = image.shape[:2]
        self.allow_whole = allow_whole
        self.result: Rect | None = None
        self._start: tuple[float, float] | None = None
        self._current: Rect | None = None

        self.root = tk.Tk()
        self.root.title("Tanaw calibration")
        self.root.configure(background="#111111")
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.scale = fit_scale(self.img_w, self.img_h,
                               min(max_w, screen_w - 80), min(max_h, screen_h - 200))
        keys = "Enter = confirm   Esc = cancel   R = redo" + ("   A = whole window"
                                                              if allow_whole else "")
        tk.Label(self.root, text=prompt, font=("Segoe UI", 14, "bold"), fg="#ffffff",
                 bg="#111111", wraplength=int(self.img_w * self.scale), justify="left"
                 ).pack(padx=12, pady=(12, 2), anchor="w")
        tk.Label(self.root, text=keys, font=("Segoe UI", 11), fg="#bbbbbb", bg="#111111"
                 ).pack(padx=12, pady=(0, 8), anchor="w")

        disp_w, disp_h = round(self.img_w * self.scale), round(self.img_h * self.scale)
        shown = cv2.resize(image, (disp_w, disp_h), interpolation=cv2.INTER_AREA)
        ok, png = cv2.imencode(".png", shown)
        if not ok:
            raise RuntimeError("could not encode preview image")
        self._photo = tk.PhotoImage(data=base64.b64encode(png.tobytes()).decode("ascii"))
        self.canvas = tk.Canvas(self.root, width=disp_w, height=disp_h, highlightthickness=0,
                                cursor="crosshair", background="#000000")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.pack(padx=12, pady=(0, 12))
        self._box = self.canvas.create_rectangle(0, 0, 0, 0, outline="#00ff66", width=2)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Return>", lambda _e: self._confirm())
        self.root.bind("<Escape>", lambda _e: self._cancel())
        self.root.bind("<KeyPress-r>", lambda _e: self._clear())
        if allow_whole:
            self.root.bind("<KeyPress-a>", lambda _e: self._whole())
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)
        self.root.attributes("-topmost", True)

    def _on_press(self, event: tk.Event[tk.Canvas]) -> None:
        self._start = (float(event.x), float(event.y))

    def _on_drag(self, event: tk.Event[tk.Canvas]) -> None:
        if self._start is not None:
            x0, y0 = self._start
            self.canvas.coords(self._box, x0, y0, event.x, event.y)

    def _on_release(self, event: tk.Event[tk.Canvas]) -> None:
        if self._start is None:
            return
        x0, y0 = self._start
        self._current = drag_to_rect(x0, y0, event.x, event.y, self.scale,
                                     self.img_w, self.img_h)
        self._start = None
        if self._current is None:
            self._clear()

    def _clear(self) -> None:
        self._current = None
        self.canvas.coords(self._box, 0, 0, 0, 0)

    def _whole(self) -> None:
        self._current = Rect(0, 0, self.img_w, self.img_h)
        self.canvas.coords(self._box, 1, 1, self.img_w * self.scale - 1,
                           self.img_h * self.scale - 1)

    def _confirm(self) -> None:
        if self._current is not None:
            self.result = self._current
            self.root.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.root.destroy()

    def run(self) -> Rect | None:
        self.root.focus_force()
        self.root.mainloop()
        return self.result


PickFn = Callable[[Frame, str, bool], Rect | None]


def tk_pick(image: Frame, prompt: str, allow_whole: bool) -> Rect | None:
    return RectPicker(image, prompt, allow_whole=allow_whole).run()


# --- calibration flow ----------------------------------------------------------------------


class CalibrationCancelledError(Exception):
    pass


def _require(rect: Rect | None) -> Rect:
    if rect is None:
        raise CalibrationCancelledError
    return rect


def build_profile(
    frame: Frame,
    *,
    name: str,
    window: str,
    strategy: str,
    out_path: Path,
    pick: PickFn = tk_pick,
) -> tuple[FocusProfile, Frame | None]:
    """Ask for the boxes and build a validated profile. Returns (profile, cursor template)."""
    img_h, img_w = frame.shape[:2]
    total = 3 if strategy == "cursor_template" else 2
    region = _require(pick(
        frame,
        f"Step 1 of {total}: drag a box around the MENU AREA where the selection moves. "
        "Make it generous: include every option the cursor can reach. "
        "Press A to use the whole window.",
        True,
    ))
    template: Frame | None = None
    if strategy == "cursor_template":
        cursor = _require(pick(
            frame,
            "Step 2 of 3: drag a TIGHT box around the CURSOR sprite only "
            "(in DELTARUNE: the red heart). Include as little background as possible.",
            False,
        ))
        if region.intersect(cursor) != cursor:
            raise ProfileError("The cursor box must be inside the menu area.")
        row = _require(pick(
            frame,
            "Step 3 of 3: drag a box around the WHOLE SELECTED ROW: the cursor and the full "
            "text of the selected option. Make it wide enough for the longest option.",
            False,
        ))
        template = crop(frame, cursor)
        template_rel = f"local/{name}-cursor.png"
        chosen: CursorTemplateStrategy | HighlightColorStrategy = CursorTemplateStrategy(
            template_path=template_rel, row=row_offset(cursor, row)
        )
    else:
        box = _require(pick(
            frame,
            "Step 2 of 2: drag a box around the HIGHLIGHTED item (the selected option).",
            False,
        ))
        low, high = sample_hsv_range(crop(frame, box))
        chosen = HighlightColorStrategy(hsv_low=low, hsv_high=high)

    profile = FocusProfile(
        name=name,
        window=window,
        client_width=img_w,
        client_height=img_h,
        menu_region=RectModel.from_rect(region),
        strategy=chosen,
    )
    return profile, template


def write_calibration(
    profile: FocusProfile, template: Frame | None, out_path: Path
) -> Path | None:
    """Save the JSON profile and, for cursor_template, the PNG next to it in local/."""
    template_file: Path | None = None
    if template is not None and isinstance(profile.strategy, CursorTemplateStrategy):
        template_file = out_path.parent / profile.strategy.template_path
        template_file.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(template_file), template):
            raise ProfileError(f"Could not write {template_file}")
    save_profile(profile, out_path)
    return template_file


def _capture(window_query: str) -> Frame:
    window = find_window(window_query)
    with WindowCapturer(window) as capturer:
        captured, _ = capturer.grab_settled()
    return captured.image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tanaw.calibrate",
                                     description="Create a Focus Mode profile for a game.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--window", help="Case-insensitive part of the game window's title")
    source.add_argument("--from-image", type=Path,
                        help="Calibrate from a saved capture instead of the live window")
    parser.add_argument("--out", type=Path, required=True, help="Profile JSON to write")
    parser.add_argument("--strategy", choices=("cursor_template", "highlight_color"),
                        default="cursor_template")
    parser.add_argument("--name", help="Profile name (default: from the --out file name)")
    parser.add_argument("--window-title", help="Window title to store when using --from-image")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    netguard.install()
    enable_dpi_awareness()
    if args.out.suffix.lower() != ".json":
        parser.error("--out must be a .json file")

    try:
        if args.from_image is not None:
            loaded = cv2.imread(str(args.from_image), cv2.IMREAD_COLOR)
            if loaded is None:
                parser.error(f"could not read image: {args.from_image}")
            frame: Frame = np.ascontiguousarray(loaded, dtype=np.uint8)
            window_title = args.window_title or args.from_image.stem
        else:
            frame = _capture(args.window)
            window_title = args.window
        name = args.name or profile_name_from_path(args.out)
        profile, template = build_profile(frame, name=name, window=window_title,
                                          strategy=args.strategy, out_path=args.out)
        template_file = write_calibration(profile, template, args.out)
    except CalibrationCancelledError:
        print("Calibration cancelled; nothing was saved.")
        return 1
    except (CaptureError, ProfileError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Saved profile: {args.out}")
    if template_file is not None:
        print(f"Saved cursor template (local only, gitignored): {template_file}")
    region = profile.menu_region
    print(f"Menu area: {region.left},{region.top} {region.width}x{region.height} "
          f"in a {profile.client_width}x{profile.client_height} window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
