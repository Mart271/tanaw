# Progress log

What was built, what was actually run, and what still needs a manual test on the game. Newest first.

Hardware/software for all runs below: Windows 11 Home (10.0.26300), Python 3.12 venv.

## Oct 9, 2026

### Step 1 — Project skeleton
- Added `pyproject.toml` with pinned dependencies, `.gitignore`, `README.md`, `DISCLOSURES.md`, this file.
- Verified at install time: the OCR package is now `rapidocr` 3.10.0 (the old `rapidocr-onnxruntime` is superseded). Its wheel bundles PP-OCRv6 small det/rec models.
- Ran a throwaway probe on a synthetic image (white background, OpenCV-drawn text "FIGHT ACT ITEM" / "HP 120 / 160"): returned `'FIGHT ACT ITEM'` (0.97) and `'HP 120 /160'` (0.95). First call took ~3.9 s because models load lazily; startup needs a warm-up call.
- Switched to `opencv-python-headless` because `rapidocr` requires it and the two OpenCV wheels overwrite each other. Calibration will need a `tkinter` ROI picker instead of `cv2.selectROI`.
