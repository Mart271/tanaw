# Disclosures

Everything Tanaw depends on, as required by the hackathon rules. Kept current as dependencies are added.

## AI models (all run locally)

| Model | Used for | Source | License |
|---|---|---|---|
| PP-OCRv6 small text **detection** (`PP-OCRv6_det_small.onnx`) | Finds where text is in a frame | PaddleOCR, converted to ONNX and bundled in the `rapidocr` 3.10.0 wheel | Apache-2.0 |
| PP-OCRv6 small text **recognition** (`PP-OCRv6_rec_small.onnx`) | Reads the text in each detected box | PaddleOCR via `rapidocr` 3.10.0 wheel | Apache-2.0 |

The wheel also contains a text-direction classifier (`ch_ppocr_mobile_v2.0_cls_mobile.onnx`); Tanaw turns it off because game text is never upside down.

Text-to-speech currently uses the built-in **Windows SAPI** voice (part of Windows, not a neural model).

## Libraries (runtime)

| Library | Version | Used for | License |
|---|---|---|---|
| rapidocr | 3.10.0 | OCR pipeline wrapper around the models above | Apache-2.0 |
| onnxruntime | 1.31.0 | Runs the OCR models on CPU | MIT |
| numpy | 2.5.3 | Image arrays | BSD-3-Clause (and others, see package) |
| opencv-python-headless | 5.0.0.93 | Image resizing, colour conversion, diffing | Apache-2.0 |
| mss | 10.2.0 | Screen capture of the game window's client area | MIT |
| pywin32 | 312 | Window lookup (`win32gui`), DPI awareness, SAPI speech via COM | PSF |
| pynput | 1.8.2 | Global hotkeys | LGPL-3.0 |
| pydantic | 2.14.0 | Settings and profile validation | MIT |

Transitive dependencies of `rapidocr` (omegaconf, pyclipper, shapely, Pillow, PyYAML, requests, tqdm, colorlog, six) are installed by pip. `requests` is only used by rapidocr's model-download path, which Tanaw never triggers (model paths are passed explicitly), and the network guard would block it anyway.

## Libraries (development only)

pytest 9.1.1, mypy 2.4.0, ruff 0.16.10, types-pywin32, types-pynput.

## APIs and cloud services

**None** in the core path. No API keys, no accounts, no telemetry.

## Existing code and assets

- All code in this repo was written during the hackathon (started Oct 9, 2026, 2:30 PM PHT). No code was copied from other personal projects.
- **Demo game:** DELTARUNE Chapter 1&2 (free, by Toby Fox). Used only for live demonstration. Game screenshots, crops, and sprites are copyrighted and are **not** included in this repository (`fixtures/` and `profiles/local/` are gitignored).

## AI development tools

- **Claude** (claude.ai) for planning and the proposal.
- **Claude Code** (Claude Opus 5.5) for writing code, tests, and docs in this repo. Commits made with it carry a `Co-Authored-By: Claude` line.
