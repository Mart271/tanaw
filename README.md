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
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

All commands below call `.venv\Scripts\python.exe` directly, so they work without activating the venv. (If you do run `.venv\Scripts\Activate.ps1`, plain `python` works too. Without either, `python` is your system Python and you'll see `No module named 'tanaw'`.)

## Run

Start the game in windowed mode, then:

```powershell
.venv\Scripts\python.exe -m tanaw --window "DELTARUNE"
```

`--window` is a case-insensitive substring of the game window's title.

### Focus Mode (speaks only the newly selected menu item)

Focus Mode needs a one-time calibration per game, done by a sighted helper (under 2 minutes). With the game window showing a menu with the cursor on an option:

```powershell
.venv\Scripts\python.exe -m tanaw.calibrate --window "DELTARUNE" --out profiles\deltarune.json --pixel-scale 2 --threshold --nav-keys "up,down,left,right,z,x,c,enter,esc"
```

Drag three boxes when asked: the menu area (or press **A** for the whole window), a tight box around the cursor (DELTARUNE's red heart), and the whole selected row. The tool then shows what Tanaw reads in that row. The cursor image is saved to `profiles\local\` (gitignored, since it's game art), so each computer calibrates once.

Then run with the profile and press **Ctrl+Alt+F**:

```powershell
.venv\Scripts\python.exe -m tanaw --profile profiles\deltarune.json
```

After each navigation key, Tanaw waits for the menu to redraw, finds the cursor, reads only that row, and speaks it if it changed. If OCR isn't confident, or it sees two cursors, it says "Selection unclear" instead of guessing. With no cursor on screen (e.g. walking around), it stays silent.

### Hotkeys

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+R` | Read Mode: read the text in the game window |
| `Ctrl+Alt+F` | Focus Mode on/off (needs `--profile`) |
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
| `--capture auto` | `auto` (default), `window` or `screen`. See below |

### How capture works

By default Tanaw asks Windows to render the game window's own pixels (`PrintWindow`), so it works even while another window covers the game, and it can never pick up another app's content. If that gives nothing usable, Tanaw falls back to copying screen pixels, **but only while the game is the active window**. Otherwise it says "Can't see the game window. Bring it to the front." instead of reading whatever is on top.

## Debug tools

```powershell
# List visible window titles (to find the right --window text)
.venv\Scripts\python.exe -m tanaw.capture --list

# Capture the game window once and save it to ./debug/capture-<time>.png
.venv\Scripts\python.exe -m tanaw.capture --window "DELTARUNE" --save

# OCR an image file and print each box, its text and confidence
.venv\Scripts\python.exe -m tanaw.ocr debug\capture-20261009-170000.png --upscale 2
.venv\Scripts\python.exe -m tanaw.ocr debug\capture-20261009-170000.png --upscale 2 --nearest

# Say a line with the speech engine
.venv\Scripts\python.exe -m tanaw.speech "Hello from Tanaw"
```

## Measurements

Results with hardware specs are in [`docs/results.md`](docs/results.md). To reproduce:

```powershell
.venv\Scripts\python.exe scripts\check_offline.py --prove-offline   # with Wi-Fi off
.venv\Scripts\python.exe scripts\bench_latency.py                   # from logs\session-*.jsonl
.venv\Scripts\python.exe scripts\bench_focus.py --labels fixtures\labels.json
.venv\Scripts\python.exe scripts\bench_ocr.py --labels fixtures\labels.json
```

The labels format is described at the top of `src/tanaw/bench.py`. Game screenshots stay in the gitignored `fixtures/` folder.

## Development checks

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m ruff check .
```

## Privacy

- Frames are kept in memory only, unless you pass `--debug-captures` or use the capture debug tool with `--save`.
- Only the selected game window is captured, never the whole desktop, and never another window that happens to cover the game.
- Logs (`logs/session-*.jsonl`) contain metadata only unless `--verbose-text` is passed.
- The network guard refuses non-loopback connections from inside Tanaw's process.

## Known limitations

- Supports selected games in windowed/borderless mode, starting with turn-based titles. It does not work on every game.
- OCR reads text from pixels and can be wrong; Tanaw drops low-confidence text and says when it can't read something clearly.
- Not yet validated with blind and low-vision players. That's the next step.

## Disclosures

See [`DISCLOSURES.md`](DISCLOSURES.md) for every model, library, asset, and AI development tool used.
