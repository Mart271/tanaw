# Progress log

What was built, what was actually run, and what still needs a manual test on the game. Newest first.

Hardware/software for all runs below: Windows 11 Home (10.0.26300), Python 3.12 venv.

## Oct 9, 2026

Test laptop: Intel Core i5-12500H (16 threads), 15.6 GB RAM, two 1920x1080 displays.

### Step 6 — Read Mode (`layout.py`, `hotkeys.py`, `settings.py`, `events.py`, `app.py`, `__main__.py`)
- `layout.py` (pure): groups OCR boxes into lines by vertical overlap, orders top→bottom / left→right, reads wide gaps as a pause ("FIGHT, ACT"), drops dialogue bullets ("* ..."), ends lines with a full stop for natural pauses.
- `hotkeys.py`: own matcher on Windows virtual-key codes. **Found while probing pynput 1.8.2:** its `HotKey` helper compares letters by character, but with Ctrl+Alt held Windows can report the letter with no character, so `<ctrl>+<alt>+r` never matched in a simulated press. Hotkeys must include Ctrl or Alt, so they can't steal a game key; duplicates are rejected.
- Worker thread owns capture + OCR; speech controls (stop/repeat/pause) bypass it and go straight to the speaker, so Stop is instant even mid-OCR. Repeated Read presses while busy are merged into one read. Read refuses if the game isn't the foreground window.
- `logs/session-*.jsonl`: metadata only; the log API rejects free-text values. `--debug-captures` frames are deleted on clean exit.
- Ran `pytest`: **112 passed**. `mypy --strict`: clean (22 files). `ruff`: clean.
- Ran `python -m tanaw --window "DELTARUNE"` with the game closed: printed and spoke the "No window" error, exit code 1; event log had only `start` / `error kind=WindowNotFoundError` / `stop`.
- Ran `python -m tanaw --window x --upscale 9`: rejected by validation, exit code 2.
- Ran the full app against Task Manager for 15 s: reached `ready` (OCR load 1.47 s, warm-up 0.52 s, hotkey listener started), then I force-killed it (couldn't send Ctrl+C to a background process, so clean-exit cleanup was covered by unit tests, not this run).
- Ran real capture → real OCR → layout through the worker on the Task Manager window (foreground check bypassed, speech replaced by a counter): 3 runs produced ~2,700 characters each in **3.4–4.9 s**. That is a very text-dense window (100+ text boxes); a game screen has far less text, but **full-screen Read Mode latency on DELTARUNE is unmeasured**.
- **Not tested by me, must be tested manually:** pressing the real hotkeys (I did not inject keystrokes into your desktop), hearing the speech, and anything on DELTARUNE itself.

### Step 5 — Speech (`src/tanaw/speech.py`)
- `Speaker` runs SAPI on its own thread (`tanaw-speech`); COM is initialised and released inside that thread. `speak` / `stop` / `repeat_last` / `toggle_pause` only enqueue, so callers never block. Flags: async + purge-before-speak + **not-XML** (so OCR text can't inject SAPI markup). New speech while paused resumes; stop while paused un-pauses.
- Ran `pytest`: 65 passed (11 speech tests with a fake voice: ordering, thread, repeat, pause, stop, truncation, init failure).
- Ran the real SAPI voice: `python -m tanaw.speech "Tanaw speech test."` finished without errors. A probe on the real voice measured: first `Speak()` call returned in 182 ms (audio device start-up), an interrupting `Speak()` in 79 ms, and pause held speech until resume. These are single quick runs, not benchmarks. The ~80 ms purge cost is part of keypress→speech latency, worth re-measuring in P1.
- **Manual test needed:** I can't hear the laptop's audio, so confirm you actually hear the voice and that it gets cut off when interrupted.

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
