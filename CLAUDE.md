# CLAUDE.md — Tanaw

> Read this file fully before writing any code. It is the source of truth for scope, rules, architecture, and priorities. The longer pitch/defense document lives at `docs/proposal.md`.

## 1. What we're building

**Tanaw** (Tagalog: "to look out at") is an **offline AI accessibility companion** that helps blind and low-vision players navigate menus and read game information in supported Windows PC games — without modifying the game and without cloud services.

**One-line pitch:** Tanaw helps blind and low-vision players navigate menus, read game information, and understand supported PC games — fully on their own computer, without developers modifying the game or players depending on cloud services.

**The core feature is Focus Mode:** when the player presses a navigation key, Tanaw speaks *only the newly selected item* — not the whole screen.

## 2. Hackathon context (hard constraints)

Event: **AppBuildersPH Hackathon 2026 — theme "Local AI"**. Building started **Oct 9, 2026, 2:30 PM PHT (UTC+8)**.

| Rule | What it means for us |
|---|---|
| **Deadline: Oct 10, 10:00 AM PHT. No extensions.** | Code freezes at 10:00 AM. Judges review the repo as of the deadline. |
| **One submission, no edits/resubmits** | Everything must be checked before submitting. |
| **Public GitHub repo by deadline** | Never commit secrets, personal files, or copyrighted assets we can't redistribute. |
| **Substantially built during the hackathon** | All code is written from scratch in this repo. Open-source libraries are fine. Do **not** pull code from other personal projects. Record any reused snippet in `DISCLOSURES.md`. |
| **Meaningful AI inference must run locally** | OCR, TTS, and Ask Mode all run on-device. No cloud AI APIs in the core path. |
| **Disclose models, frameworks, APIs, existing code/assets, AI dev tools** | Keep `DISCLOSURES.md` current as you add dependencies. AI dev tools used: Claude (claude.ai + Claude Code). |
| **Fake benchmarks = results can be disputed** | Never invent numbers. Only report what scripts actually measured, with the hardware listed. |
| **No external help from people outside the hackathon** | Only registered team members contribute. AI tools are allowed and disclosed. |
| **Repo must let judges recreate it** | README needs exact setup + run steps. Live deployment not required. |

**Judging weights:** Problem & Usefulness 25% · Local AI Implementation 25% · Technical Execution 20% · Innovation 15% · Product & Demo Quality 15%.
**Demo Day:** 5 min live pitch/demo + 3 min judge Q&A, in person. Prioritize a working product over slides.

## 3. Current status

- [x] Concept, scope, and defense finalized (`docs/proposal.md`).
- [x] **Demo game chosen (Oct 9):** primary **DELTARUNE Chapter 1&2** (free), backup a free RPG Maker MV/MZ game. See §10.
- [x] P0 built Oct 9, 4:00–4:40 PM: skeleton, network guard, capture, OCR, speech, Read Mode. Details and every test run in `docs/progress.md`.
- [x] Read pipeline run on DELTARUNE (real capture + OCR + layout, speech printed): reads the "start from Chapter 1?" screen in ~1.0–1.2 s, **but pixel-font "No" reads as "Ho" at 0.97+ confidence**. Fix goes in the DELTARUNE profile once we have more frames (see §10).
- [x] Mark tested Read Mode on DELTARUNE with the real hotkeys: works.
- [ ] Not yet done: Wi-Fi-off run.
- [ ] Next P0: calibration tool (tkinter ROI picker) + Focus Mode with `cursor_template` for DELTARUNE's heart, then the companion panel (§9b).

## 4. Scope

### In scope (MVP)
- Windows 10/11, Python.
- Selected games running **windowed or borderless** (exclusive fullscreen can capture as black — not supported).
- Turn-based, menu-heavy, dialogue-heavy games navigated with the keyboard.
- Per-game **profiles** (JSON) created once via a calibration tool.

### Non-goals (do not build)
- Fast-action/twitch games.
- Universal "works on any game" support.
- Playing the game for the user / sending inputs to the game.
- Reading game memory, injecting code, or hooking the game's renderer (anti-cheat risk, and unnecessary).
- Any cloud AI fallback.

## 5. Priorities (build in this order)

| P | Feature | Done when |
|---|---|---|
| P0 | Window selection + capture | Captures the chosen game window correctly (DPI-aware) with Wi-Fi off |
| P0 | Local OCR wrapper | Returns text boxes + confidence from a captured frame |
| P0 | Local TTS + hotkeys | Speak, interrupt (stop), repeat last, pause/resume |
| P0 | Read Mode | Hotkey reads window text in sensible reading order |
| P0 | Calibration tool + profiles | Sighted helper can create a profile for a game in < 2 min |
| P0 | **Focus Mode** | Speaks the newly selected menu item after a nav keypress, in a calibrated menu |
| P0 | Network guard | Core refuses any non-loopback connection (see §7) |
| P1 | Companion panel (§9b) | Live captions, mode, offline badge, latency visible to judges in the live demo and video. Build right after Focus Mode works on the demo game |
| P1 | Benchmarks | Scripts measure keypress→speech latency, selection accuracy, OCR errors; results recorded honestly |
| P1 | Better voice (Piper) | Neural voice replaces SAPI (SAPI isn't neural — Piper strengthens the Local AI score). Do this first in P1 |
| P1 | Ask Mode | Player asks **by voice** (push-to-talk, local speech recognition) → answers a bounded question locally (OCR-derived logic first, VLM second) → spoken answer |
| P2 | Auto-calibration / broad game support | Post-hackathon |

**Do not start P1 until every P0 works end-to-end on the demo game.**

## 6. Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ (developed and tested on **3.12**; the venv is `py -3.12 -m venv .venv`) | Fully typed, `mypy --strict` clean |
| Window lookup | `pywin32` (`win32gui`) | Per-monitor DPI awareness at startup (`SetProcessDpiAwarenessContext(-4)`, falling back to `SetProcessDpiAwareness(2)`), or window rects will be wrong on scaled displays |
| Capture | **`PrintWindow`** (`PW_CLIENTONLY \| PW_RENDERFULLCONTENT`, via ctypes in `printwindow.py`), with `mss` screen pixels as fallback | `PrintWindow` makes the game render its own client area, so it works **while covered** and can never include another app's pixels (verified on DELTARUNE: exact frame in 21–35 ms). Screen pixels (`mss`) are used **only while the game is the foreground window**; otherwise capture raises and Tanaw says "Bring it to the front." (`capture.decide_source`). Lesson learned: plain `mss` captured the Claude window that was covering the game. Window must be windowed/borderless |
| Image ops | `numpy`, `opencv-python-headless` | Diffing, HSV masks, template matching, upscaling. **Headless build** because `rapidocr` depends on it, and installing `opencv-python` alongside it overwrites the same `cv2` files. Headless has no `cv2.selectROI` window, so the calibration tool draws its ROI picker with `tkinter` (stdlib) instead |
| OCR | `rapidocr==3.10.0` on `onnxruntime==1.31.0` (PaddleOCR **PP-OCRv6 small** det + rec models) | Verified Oct 9: the old `rapidocr-onnxruntime` package is superseded by `rapidocr` 3.x. Models ship **inside the wheel**; we pass their paths explicitly so the library never reaches its download path. API: `RapidOCR(params={...})(img_bgr)` → `RapidOCROutput(boxes: ndarray[N,4,2], txts, scores)`, or all `None` when no text. Direction classifier disabled (game text is never upside down). **Detector resize set to "max side ≤ 960" and 4 ONNX threads** — the default ("min side ≥ 736") made a 160x60 crop take ~2 s. Recognition-only on a single row crop (no detection) was ~22 ms in a quick check: use it for Focus Mode. Warm up at startup |
| TTS (P0) | Windows SAPI via `win32com.client` `SAPI.SpVoice` | Zero download, offline. `Speak(text, 1 \| 2)` = async + purge-before-speak (instant interrupt). Supports `Pause()`/`Resume()`/`Rate`. Call `pythoncom.CoInitialize()` in the speech thread |
| TTS (P1) | Piper | Better voice; needs audio playback + stop handling |
| Hotkeys / key events | `pynput` (`keyboard.Listener` only) | Global listener for hotkeys + navigation keys. Never block the listener thread. **Don't use pynput's `HotKey`/`GlobalHotKeys`**: with Ctrl+Alt held Windows can report a letter with no character, so they miss Ctrl+Alt+R. `tanaw.hotkeys` matches Windows virtual-key codes instead |
| Config/validation | `pydantic` v2 | Profiles and settings validated on load |
| Companion panel | Tkinter | No extra deps. Tk owns its own thread; other threads send updates via a queue polled with `after()` |
| Ask Mode (P1) | Ollama on `127.0.0.1:11434` with a small VLM, e.g. `qwen2.5vl:3b` | Verify the exact model tag in the Ollama library. Loopback only |
| Speech recognition (P1, Ask Mode input) | `faster-whisper` (small or base model, CPU int8) | Push-to-talk. Blind players can't type questions, so Ask Mode needs voice input. Model downloaded once at setup |
| Quality | `pytest`, `mypy`, `ruff` | Tests run on fixture screenshots, no live game needed |

## 6b. Local AI components (25% of the score — keep these explicit)

| Model | Job | Priority |
|---|---|---|
| RapidOCR text **detection** network | Finds where text is in the frame | P0 |
| RapidOCR text **recognition** network | Reads the text in each box | P0 |
| Piper neural TTS | Speaks all output | P1 (SAPI is the non-neural fallback) |
| faster-whisper | Understands the player's spoken question | P1 |
| Small VLM via Ollama (loopback) | Answers visual questions OCR can't | P1 |

Highlight detection, diffing, and reading order are classic image processing/logic — fast and predictable. **Do not describe them as AI.**

Ask Mode flagship flow (all offline): hold `Ctrl+Alt+A` → speak → faster-whisper transcribes → OCR text (+ screenshot for VLM if needed) → local answer → Piper speaks.

## 7. Engineering & security rules

Code must be production-quality even under time pressure: modular, fully typed, validated, and secure by default.

1. **Type everything.** No `Any` unless unavoidable (wrap untyped libs in typed adapters). `mypy --strict` must pass.
2. **Small modules, single responsibility.** Pure logic (diffing, grouping, highlight scoring) is separated from I/O (capture, OCR, speech) so it's unit-testable on fixtures.
3. **Network guard (defense-in-depth):** at startup, install a guard that wraps `socket.socket.connect` and raises on any non-loopback address. Ask Mode's Ollama client must also validate that its host is `127.0.0.1`/`localhost`/`::1` and refuse otherwise. This backs the "nothing leaves the laptop" claim with code, not just words.
4. **No frame retention by default.** Never write captures to disk unless `--debug-captures` is explicitly passed. Debug output goes to `./debug/` (gitignored) and is deleted on clean exit.
5. **Capture only the selected game window,** never the full desktop.
6. **Logs contain metadata only** (timings, confidences, region sizes, mode). No OCR text or images in logs unless `--verbose-text` is passed.
7. **Validate all inputs:** profile JSON (rects inside window bounds, thresholds in range, colors valid), CLI args, hotkey strings. Fail closed with a clear spoken + printed error.
8. **Local event log:** append-only `logs/session-<timestamp>.jsonl` of events (mode changes, calibrations, errors) with metadata only. Gitignored.
9. **No secrets, tokens, or API keys anywhere.** There should be none to need.
10. **Never block the speech or key-listener threads.** Use a worker thread + queue for capture/OCR.
11. **Speak uncertainty, don't guess:** if OCR confidence is below threshold or selection is ambiguous, say "selection unclear" / "can't read that clearly" instead of speaking a guess.

## 8. Architecture

```text
pynput listener ──(nav key / hotkey)──▶ event queue ──▶ worker thread
                                                         │
                     ┌───────────────────────────────────┼─────────────────────────┐
                     ▼                                   ▼                         ▼
               Focus Mode                           Read Mode                Ask Mode (P1)
   settle → capture menu region          capture window → OCR →        OCR context (+ VLM via
   → find highlight (profile)            group into reading order      loopback Ollama) →
   → crop line → OCR → if changed                                      bounded answer
                     └───────────────────────────────────┬─────────────────────────┘
                                                         ▼
                                              Speaker (SAPI async, purge,
                                              repeat-last, pause/resume)
```

### Proposed layout

```text
tanaw/
  CLAUDE.md
  README.md                 # setup + run steps judges can follow
  DISCLOSURES.md            # models, libraries, AI tools, reused code (keep current)
  pyproject.toml
  .gitignore                # debug/, logs/, .venv/, local fixtures if not redistributable
  docs/proposal.md          # full pitch + 10-expert defense
  profiles/<game>.json      # per-game calibration
  src/tanaw/
    __main__.py             # `python -m tanaw --window "DELTARUNE"` (later also --profile profiles/<game>.json)
    app.py                  # wiring: hotkeys → worker thread (capture/OCR) → speaker
    settings.py             # pydantic settings + profile schema
    netguard.py             # loopback-only socket guard
    frames.py               # Frame/Rect types, frame_difference, settle() (pure)
    printwindow.py          # PrintWindow capture of the window's own pixels (ctypes/GDI)
    capture.py              # DPI awareness, window lookup, capture source decision, client-area capture
    ocr.py                  # typed RapidOCR adapter, preprocessing, confidence filter
    layout.py               # reading-order grouping (pure)
    focus.py                # selection tracking strategies (pure + thin I/O)
    speech.py               # SAPI speaker: speak/stop/repeat/pause/resume
    hotkeys.py              # pynput listener + virtual-key hotkey matcher (pure)
    calibrate.py            # tkinter ROI picker → profile builder
    ask.py                  # P1: OCR-derived answers, then loopback VLM
    events.py               # append-only JSONL event log (metadata only)
    ui/panel.py             # companion panel (Tkinter) — captions, mode, offline badge, metrics
  assets/fonts/             # DM Sans + Inter (OFL) with license files, registered privately at runtime
  scripts/
    bench_latency.py        # keypress → speech-start latency
    bench_focus.py          # selection accuracy on labeled transitions
    bench_ocr.py            # OCR error rate on labeled crops
  tests/
  fixtures/                 # screenshots + labels.json (only if license allows redistribution)
```

## 9. Focus Mode — the hard part

**Known trap:** a plain before/after diff marks **both** the old and new highlighted items as changed. We must identify the *new selection*, not just changed pixels.

### Approach (per-game profile, created once by a sighted helper)
Calibration (`python -m tanaw.calibrate`) shows a captured frame in a `tkinter` window (headless OpenCV has no `cv2.selectROI`) and lets the helper drag:
1. The **menu region** (where selectable items live).
2. A box over the **currently highlighted item** → sample its highlight signature.

Profile stores one of two strategies:
- **`highlight_color`** (default): HSV color range sampled from the highlight bar/background.
- **`cursor_template`**: a small image of a cursor/arrow sprite for games that mark selection with an icon.

### Runtime loop (on nav keypress: arrows, WASD if configured, Enter/Esc)
1. **Settle:** capture the menu region repeatedly (~every 16 ms) until two consecutive frames differ below a threshold, or a timeout (~300 ms) — menus often animate.
2. **Locate selection:**
   - `highlight_color`: HSV mask within the menu region → morphological cleanup → largest blob that looks like a text row → bounding box.
   - `cursor_template`: `cv2.matchTemplate` → best match above threshold → crop the text row to the right of the cursor.
   - Diff vs. the last stable frame may be used as a **prefilter** (only consider changed rows), never as the decision itself.
3. **OCR** the selected row crop (upscaled 2–3×).
4. **Speak only if** the text changed from the last spoken selection (avoid repeats), confidence ≥ threshold. Otherwise say "selection unclear".
5. **Cache** OCR results by crop hash to cut latency on revisits.

### Read Mode
OCR the whole client area (or a profile-defined region), group boxes into lines by y-overlap, order lines top→bottom and boxes left→right, optionally split into columns. Speak with interrupt/repeat/pause.

### Default hotkeys (configurable, must not collide with the demo game)
| Hotkey | Action |
|---|---|
| `Ctrl+Alt+F` | Toggle Focus Mode |
| `Ctrl+Alt+R` | Read Mode (read current screen) |
| `Ctrl+Alt+Space` | Repeat last utterance |
| `Ctrl+Alt+S` | Stop speech |
| `Ctrl+Alt+P` | Pause/resume speech |
| `Ctrl+Alt+A` (hold) | Ask Mode (P1): hold to speak a question, release to get the answer |

## 9b. Companion panel (UI)

**Why:** judges are sighted and watch the live demo and the video — they must *see* what Tanaw says. It also helps low-vision users. Speech + hotkeys remain the primary interface; everything must work with the panel closed.

**Contents (dark, high-contrast theme):**
- **Live caption** — last spoken text, DM Sans 36/40 (Display XL), wraps. The biggest thing on the panel.
- **Status row** — mode (Focus / Read / Ask) in DM Sans 18/28; offline badge `NETWORK BLOCKED · LOOPBACK ONLY` as an uppercase label 12/16; last keypress→speech latency (ms) and OCR confidence as metrics in DM Sans 24/32.
- **History** — last 5 utterances with timestamps, Inter 14/20.
- **Header** — selected game window + active profile name, Inter 16/24.
- Section labels: uppercase 12/16, 0.05em letter spacing if Tk supports it (otherwise skip spacing).

**Fonts:** bundle DM Sans and Inter (OFL, with licenses) in `assets/fonts/`; register privately via Windows `AddFontResourceExW` with `FR_PRIVATE`. No network font loading. Fall back to Segoe UI. List in DISCLOSURES.md.

**Capture safety (critical):** the default capture (`PrintWindow`) renders only the game window, so an overlapping panel isn't captured. But the screen-pixel fallback (used when `PrintWindow` gives nothing and the game is in front) copies the game's screen area, so the panel must still **never overlap the game** or Tanaw could OCR its own captions. Default position: beside the game window. Also apply `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`; if unsupported, rely on positioning and log a warning.

**Behavior:** `Ctrl+Alt+H` shows/hides the panel; always-on-top toggle; no mouse required. The panel shows only what was spoken (never extra OCR text) — same privacy rules as §7.

**Demo/video layout:** game window on the left, panel on the right, Wi-Fi icon visible in the taskbar. Optionally a Task Manager strip showing CPU/GPU load during OCR and Ask Mode.

## 10. Demo game selection

### Decision (Oct 9)
| | Game | Strategy | Notes |
|---|---|---|---|
| **Primary** | **DELTARUNE Chapter 1&2** (free, Toby Fox) | `cursor_template` — menus mark the selection with a **red heart (SOUL) sprite**; the selected option is also drawn **yellow** (others white), a possible `highlight_color` backup | Window title `DELTARUNE Chapter 1&2`, class `YYGameMakerYY`, client area 1280x960 (2x of native 640x480). **F4** toggles fullscreen (keep it windowed). Controls: arrows navigate, **Z/Enter** confirm, **X** cancel, **C** menu |
| **Backup** | A free RPG Maker MV/MZ game | `highlight_color` — selection is a highlighted bar | Switch if DELTARUNE's pixel font or heart tracking can't be made reliable in time |

**OCR on DELTARUNE (first frame, Oct 9):** the sentence and "Yes" read perfectly; pixel-font **"No" → "Ho" at 0.97–0.997 confidence**, so the confidence filter can't catch it. On that one frame, these read "No" correctly: black-and-white threshold + slight blur; downscale to native 640x480 + threshold (also fastest, 374 ms full frame); cropping just the row (36 ms). Collect more frames (battle menu, dialogue, status) before picking a per-profile preprocessing step. Don't hard-code a fix from one frame.

Copyright: game screenshots and sprites (including the heart template) are **never committed**. They live in `debug/`, `fixtures/` and `profiles/local/`, all gitignored. Only labels, scripts, and code go in the public repo.

### Original selection criteria
Pick one game that has:
- Turn-based play controlled with **arrow keys**
- **Windowed or borderless** mode
- A **clear highlight bar or cursor**
- **Readable fonts** (avoid tiny pixel fonts, or verify OCR after upscaling)
- **Free, offline, no built-in narration** (EA FC and Ren'Py games have built-in narration — avoid)

Process: shortlist 2–3 free keyboard-driven turn-based RPGs from itch.io, screenshot their battle/status menus, run OCR on each, pick the one that reads best. Check whether the game's license allows screenshots in a public repo; if not, keep fixtures local and publish only labels + scripts.

## 11. Benchmarks (P1) — honesty rules

Measure on the actual demo laptop and record hardware specs in the results file:
- Keypress → speech-start latency (median and worst), using timestamps at the key event and the speech-start callback.
- Selection accuracy: correct item announced / total labeled transitions.
- OCR error rate on labeled menu and status crops, including low-confidence cases.
- Startup time, peak memory.
- Offline run with Wi-Fi disabled (pass/fail).

**Never** state a number that a script didn't produce. **Never** describe planned tests as completed. If something fails, document it under "Known limitations".

## 12. Timeline (PHT)

| By | Milestone |
|---|---|
| Oct 9, 5:00 PM | Demo game chosen; capture + OCR working on its window |
| 8:00 PM | Read Mode with speech, stop, repeat, pause |
| Oct 10, 1:00 AM | Calibration tool + Focus Mode working on the demo game |
| 4:00 AM | Offline test passed; benchmark scripts producing real numbers |
| 7:00 AM | Ask Mode — **only if all P0 is solid** |
| 9:00 AM | README, DISCLOSURES, ~1-min demo video, X/LinkedIn post (tag Devin/Cognition, `#AppBuildersPH`) |
| 9:45 AM | Repo public, final check, submit once |
| **10:00 AM** | **Deadline / code freeze** |

## 13. Submission checklist

- [ ] Project name, short description, team members
- [ ] Public GitHub repo with working setup instructions
- [ ] Demo video (~1 min) + X/LinkedIn post URL
- [ ] **What runs locally:** window capture, selection tracking, OCR, TTS, speech recognition, Ask Mode
- [ ] **What requires internet:** only one-time dependency/model download during setup
- [ ] Models used, technologies/frameworks, APIs/cloud services (none in core), existing code/assets, AI development tools (Claude)
- [ ] Answer: *Why does this product benefit from running AI locally?* — instant response on every keypress, no screen content leaves the device, no per-use cost, and accessibility that doesn't disappear when an API or connection does.

## 14. Claims discipline (for README, pitch, and comments)

| Don't say | Say instead |
|---|---|
| "Works on any PC game" | "Supports selected Windows games in windowed/borderless mode, starting with turn-based titles" |
| "OCR can't be wrong" | "OCR reads text from pixels; we use confidence checks and repeat controls, and say when we're unsure" |
| "Nothing leaves the laptop" (unproven) | Show it: network guard + Wi-Fi-off demo |
| "Screen readers just dump the whole screen" | "Screen readers like NVDA have OCR; Tanaw adds selection tracking inside game menus" |
| Market-size stats without a primary source | Leave them out |
| "Tested with blind users" | "Not yet validated with blind and low-vision players — that's the next step" |

## 15. Working style

- The developer is **Mark**. Keep explanations direct and jargon-free, with practical examples.
- Prefer working code over discussion — but ask before big architecture changes.
- Commit small, frequently, with clear messages. Push regularly so nothing is lost.
- After each P0 feature, run it against the demo game and note results in `docs/progress.md`.
- If a dependency's API differs from what's written here, trust the installed version's docs and update this file.
