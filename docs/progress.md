# Progress log

What was built, what was actually run, and what still needs a manual test on the game. Newest first.

Hardware/software for all runs below: Windows 11 Home (10.0.26300), Python 3.12 venv.

## Oct 9, 2026

### Step 3 — Window capture (`src/tanaw/capture.py`, `src/tanaw/frames.py`)
- Per-monitor DPI awareness (`SetProcessDpiAwarenessContext(-4)`, with fallbacks), window lookup by title substring (skips terminals and Explorer folders, prefers an exact title, refuses ambiguous matches), client-area capture with `mss`, `settle()` that waits for the image to stop changing.
- Ran `pytest`: 44 passed (adds window-choice rules, title validation, `Rect`, `frame_difference`, `settle` with a fake clock).
- Ran `python -m tanaw.capture --window "Task Manager" --settle` (no `--save`): DPI mode reported `per-monitor`, captured 1210x1020 client area in 32 ms, settled. This laptop has two 1920x1080 displays, so **scaled-display (125%/150%) capture is not yet verified**.
- Ran `--window "DELTARUNE"` with the game closed: printed the "No window … Is the game running" error and exited 1, as intended.
- **Manual test needed:** run against DELTARUNE in windowed mode with `--save` and check the PNG shows only the game area, no title bar.

### Step 2 — Network guard (`src/tanaw/netguard.py`)
- Wraps `socket.socket.connect`, `connect_ex`, `sendto` (refuse non-loopback peers) and `socket.getaddrinfo` (refuse hostname lookups other than `localhost`, so no DNS leak).
- Ran `pytest`: 29 passed. Covers pure address rules, TCP/UDP/DNS/`urllib` blocked under the guard, and a real loopback TCP round-trip still working.
- Not yet run: Wi-Fi-off end-to-end test (needs the full app).

### Step 1 — Project skeleton
- Added `pyproject.toml` with pinned dependencies, `.gitignore`, `README.md`, `DISCLOSURES.md`, this file.
- Verified at install time: the OCR package is now `rapidocr` 3.10.0 (the old `rapidocr-onnxruntime` is superseded). Its wheel bundles PP-OCRv6 small det/rec models.
- Ran a throwaway probe on a synthetic image (white background, OpenCV-drawn text "FIGHT ACT ITEM" / "HP 120 / 160"): returned `'FIGHT ACT ITEM'` (0.97) and `'HP 120 /160'` (0.95). First call took ~3.9 s because models load lazily; startup needs a warm-up call.
- Switched to `opencv-python-headless` because `rapidocr` requires it and the two OpenCV wheels overwrite each other. Calibration will need a `tkinter` ROI picker instead of `cv2.selectROI`.
