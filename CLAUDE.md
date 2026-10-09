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

- [ ] **No code yet** (as of 3:35 PM, Oct 9).
- [ ] **Demo game not yet chosen** — biggest open blocker. See §9.
- [x] Concept, scope, and defense finalized (`docs/proposal.md`).

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
| P1 | Benchmarks | Scripts measure keypress→speech latency, selection accuracy, OCR errors; results recorded honestly |
| P1 | Better voice (Piper) | Neural voice replaces SAPI (SAPI isn't neural — Piper strengthens the Local AI score). Do this first in P1 |
| P1 | Ask Mode | Player asks **by voice** (push-to-talk, local speech recognition) → answers a bounded question locally (OCR-derived logic first, VLM second) → spoken answer |
| P2 | Auto-calibration / broad game support | Post-hackathon |

**Do not start P1 until every P0 works end-to-end on the demo game.**

## 6. Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Fully typed, `mypy --strict` clean |
| Window lookup | `pywin32` (`win32gui`) | Call `SetProcessDpiAwareness(2)` (per-monitor) at startup, or window rects will be wrong on scaled displays |
| Capture | `mss` on the window's client rect | Window must be visible/foreground and borderless/windowed. Windows Graphics Capture is a possible later upgrade |
| Image ops | `numpy`, `opencv-python` | Diffing, HSV masks, template matching, upscaling, `cv2.selectROI` for calibration |
| OCR | `rapidocr-onnxruntime` (PaddleOCR models on ONNX Runtime) | **Verify current package name/API at install time and pin the version.** Upscale crops 2–3× before OCR for small/pixel fonts |
| TTS (P0) | Windows SAPI via `win32com.client` `SAPI.SpVoice` | Zero download, offline. `Speak(text, 1 \| 2)` = async + purge-before-speak (instant interrupt). Supports `Pause()`/`Resume()`/`Rate`. Call `pythoncom.CoInitialize()` in the speech thread |
| TTS (P1) | Piper | Better voice; needs audio playback + stop handling |
| Hotkeys / key events | `pynput` | Global listener for hotkeys + navigation keys. Never block the listener thread |
| Config/validation | `pydantic` v2 | Profiles and settings validated on load |
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
    __main__.py             # `python -m tanaw --profile profiles/<game>.json`
    settings.py             # pydantic settings + profile schema
    netguard.py             # loopback-only socket guard
    capture.py              # DPI awareness, window lookup, client-rect capture, settle()
    ocr.py                  # typed RapidOCR adapter, preprocessing, confidence filter
    layout.py               # reading-order grouping (pure)
    focus.py                # selection tracking strategies (pure + thin I/O)
    speech.py               # SAPI speaker: speak/stop/repeat/pause/resume
    hotkeys.py              # pynput bindings → event queue
    calibrate.py            # cv2.selectROI-based profile builder
    ask.py                  # P1: OCR-derived answers, then loopback VLM
    events.py               # append-only JSONL event log (metadata only)
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
Calibration (`python -m tanaw.calibrate`) uses `cv2.selectROI` to let the helper drag:
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

## 10. Demo game selection (open decision)

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
