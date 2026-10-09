# Progress log

What was built, what was actually run, and what still needs a manual test on the game. Newest first.

Hardware/software for all runs below: Windows 11 Home (10.0.26300), Python 3.12 venv.

## Oct 9, 2026

Test laptop: Intel Core i5-12500H (16 threads), 15.6 GB RAM, two 1920x1080 displays.

### Step 4 — OCR adapter (`src/tanaw/ocr.py`)
- Typed wrapper: `OcrEngine.read(frame, upscale=...)` returns `TextBox(text, confidence, rect)` in original-image coordinates, plus a count of boxes dropped for low confidence. Explicit model paths, direction classifier off, warm-up call.
- **Speed fix found by measuring:** rapidocr's default detector resize ("min side ≥ 736") made a 160x60 crop take ~1.7–2.2 s. Switched to "max side ≤ 960" and 4 ONNX threads. Same crop then took 95 ms through the CLI. These are quick checks on synthetic OpenCV-drawn text, **not** benchmarks; real numbers come from the P1 benchmark scripts on game frames.
- Quick-check observations (synthetic 1280x960 frame, same laptop): full-frame OCR ~0.6–1.0 s, mostly detection; a single menu-row crop ~50 ms, or ~22 ms with recognition only (no detection). That last one matters for Focus Mode.
- Ran `pytest`: 54 passed, including the real models reading synthetic text **with the network guard on**.
- Ran `python -m tanaw.ocr <tiny pixel-text PNG> --upscale 2 --nearest`: read "FIGHT ACT" (0.97) and "ITEM SPARE" (1.00). Adjacent words on one row can merge into one box.
- **Manual test needed:** OCR a real DELTARUNE capture at `--upscale 1`, `2`, `3`, with and without `--nearest`, and note which reads the pixel font best.

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
