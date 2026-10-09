# Tanaw

**Tanaw** (Tagalog: "to look out at") is an offline AI accessibility companion. It helps blind and low-vision players navigate menus and read game information in supported Windows PC games, without modifying the game and without cloud services.

Built for the AppBuildersPH Hackathon 2026 ("Local AI").

> **Status:** work in progress. See [`docs/progress.md`](docs/progress.md) for what has actually been run and tested.

## What runs locally

Everything in the core path runs on your own computer:

- Window capture of the selected game window only
- OCR: PaddleOCR PP-OCRv6 text detection + recognition networks on ONNX Runtime (via RapidOCR)
- Speech: Windows SAPI text-to-speech
- A network guard blocks any non-loopback connection while Tanaw runs

**Internet is needed only once**, for `pip install` during setup. The OCR models are bundled inside the `rapidocr` package, so there is no separate model download.

## Requirements

- Windows 10 or 11
- Python 3.12 (3.11+ should work; 3.12 is what we tested)
- The game must run **windowed or borderless**. Exclusive fullscreen can capture as a black frame. In DELTARUNE, press **F4** to switch out of fullscreen.

## Setup

From the repo folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Run

Start the game in windowed mode, then:

```powershell
python -m tanaw --window "DELTARUNE"
```

`--window` is a case-insensitive substring of the game window's title.

### Hotkeys

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+R` | Read Mode: read the text in the game window |
| `Ctrl+Alt+S` | Stop speaking |
| `Ctrl+Alt+Space` | Repeat the last thing said |
| `Ctrl+Alt+P` | Pause / resume speech |
| `Ctrl+C` in the terminal | Quit |

### Options

| Flag | Meaning |
|---|---|
| `--debug-captures` | Save each captured frame to `./debug/` (deleted again on clean exit). Off by default: no frames are written to disk |
| `--verbose-text` | Include recognised text in logs. Off by default: logs contain only metadata (timings, sizes, confidences) |
| `--min-confidence 0.6` | OCR boxes below this confidence are dropped |
| `--upscale 2` | Upscale frames before OCR, 1 to 4 (may help pixel fonts; default 1) |
| `--nearest` | Use nearest-neighbour upscaling instead of cubic |
| `--rate 2` | Speech rate, -10 (slow) to 10 (fast) |

Read Mode only reads when the game window is in front. If another window is focused, Tanaw says "Switch to the game window first" instead of reading whatever is covering the game.

## Debug tools

```powershell
# List visible window titles (to find the right --window text)
python -m tanaw.capture --list

# Capture the game window once and save it to ./debug/capture-<time>.png
python -m tanaw.capture --window "DELTARUNE" --save

# OCR an image file and print each box, its text and confidence
python -m tanaw.ocr debug\capture-20261009-170000.png --upscale 2
python -m tanaw.ocr debug\capture-20261009-170000.png --upscale 2 --nearest

# Say a line with the speech engine
python -m tanaw.speech "Hello from Tanaw"
```

## Development checks

```powershell
python -m pytest
python -m mypy
python -m ruff check .
```

## Privacy

- Frames are kept in memory only, unless you pass `--debug-captures` or use the capture debug tool with `--save`.
- Only the selected game window is captured, never the whole desktop.
- Logs (`logs/session-*.jsonl`) contain metadata only unless `--verbose-text` is passed.
- The network guard refuses non-loopback connections from inside Tanaw's process.

## Known limitations

- Supports selected games in windowed/borderless mode, starting with turn-based titles. It does not work on every game.
- OCR reads text from pixels and can be wrong; Tanaw drops low-confidence text and says when it can't read something clearly.
- Not yet validated with blind and low-vision players. That's the next step.

## Disclosures

See [`DISCLOSURES.md`](DISCLOSURES.md) for every model, library, asset, and AI development tool used.
