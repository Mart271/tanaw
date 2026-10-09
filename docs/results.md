# Tanaw — Measured results

Every number on this page was printed by a script in this repo, with the command shown next to it. Nothing here is estimated or rounded up. Anything not measured yet says so.

## Test machine

| | |
|---|---|
| CPU | 12th Gen Intel Core i5-12500H (12 cores / 16 threads) |
| RAM | 15.6 GB |
| GPU | NVIDIA GeForce RTX 3050 Laptop GPU + Intel Iris Xe Graphics. **Not used:** OCR runs on the CPU (ONNX Runtime CPU execution provider, 4 threads) |
| OS | Windows 11 Home Single Language, 10.0.26300 (build 26300) |
| Python | 3.12.0 (project venv) |
| Displays | Two 1920x1080 displays at 100% scaling |
| Game | DELTARUNE Chapter 1&2, windowed, client area 1280x960 |
| Speech | Windows SAPI (built-in voice) |

## 1. Offline check

**Command:** `.venv\Scripts\python.exe scripts\check_offline.py` (Oct 9, 2026, Wi-Fi **on**)

| Check | Result |
|---|---|
| Network guard blocks a deliberate connection to a public address (1.1.1.1:443) | **PASS** — refused inside Python before any packet was sent |
| Computer actually offline (unguarded connection to 1.1.1.1:443 fails) | **Not run yet.** Needs Wi-Fi off: `scripts\check_offline.py --prove-offline` |
| Full app run with Wi-Fi off | **Not run yet** |

At that time Windows reported these adapters as up: Wi-Fi (Intel Wi-Fi 6 AX201), Radmin VPN, Tailscale. A full offline test must turn off Wi-Fi **and** disconnect both VPN adapters.

Tanaw also runs this guard self-test on **every start-up** and refuses to start if the connection isn't blocked. Observed on `python -m tanaw` (Oct 9, 17:03): printed `Network guard self-test (connect to 1.1.1.1): PASS - blocked`, and the session log recorded `{"event":"netguard_selftest","passed":true,"target":"1.1.1.1:443"}`.

## 2. Keypress → speech-start latency

**Command:** `.venv\Scripts\python.exe scripts\bench_latency.py`

**Not measured yet: 0 samples.** The script read 4 session logs (Oct 9, 16:20–16:59) and found no `speech_start` events, because keypress/speech-start logging was added after those sessions. The numbers come from real hotkey and arrow-key presses in the game, which haven't been recorded yet.

How it's measured: `keypress_t` is taken in the global key hook the moment the key goes down. `speech_start_t` is when SAPI's StartStream event (audio started) reaches Tanaw's speech thread, which polls every 5 ms while waiting. So reported latency can be up to ~5 ms longer than the real one.

## 3. Focus Mode selection accuracy

**Command:** `.venv\Scripts\python.exe scripts\bench_focus.py --labels fixtures\labels.json`

**Not measured yet: 0 samples.** There are no labeled screenshots in `fixtures/` yet. The format is described in `src/tanaw/bench.py`; screenshots stay local because game art is copyrighted.

## 4. OCR error rate

**Command:** `.venv\Scripts\python.exe scripts\bench_ocr.py --labels fixtures\labels.json`

**Not measured yet: 0 samples** (same reason as above).

## 5. Automated tests

**Command:** `.venv\Scripts\python.exe -m pytest` → **204 passed** (Oct 9, 2026). These use synthetic images drawn by the tests, not game screenshots. They include the real OCR models running with the network guard on.

## Known limitations

- **Pixel-font misreads with high confidence.** On DELTARUNE, full-screen Read Mode read the menu option "No" as "Ho" at 0.97+ confidence, so a confidence threshold can't catch it. Focus Mode's row preprocessing (shrink to native size, pad, threshold) read it correctly in our development checks (see `docs/progress.md`). Read Mode doesn't use that preprocessing yet.
- **Only one game calibrated**, DELTARUNE, and only one menu screen examined in detail so far. Other menus (battle, items, dialogue) are not verified yet.
- **Windows SAPI, not a neural voice.** An interrupting `Speak` call sometimes blocked for hundreds of milliseconds in our probes (57–707 ms in one run), which adds directly to latency.
- **Capture:** `PrintWindow` worked for DELTARUNE (GameMaker). Some games may return a black image; then Tanaw falls back to screen pixels only while the game is the active window.
- **Exclusive fullscreen is not supported**; the game must run windowed or borderless.
- **Profiles are per game and per window shape.** Resizing to a different aspect ratio requires recalibration. The cursor template image stays on the computer that created it (copyright), so each machine calibrates once.
- **Not yet validated with blind and low-vision players.** That's the next step.
- **Scaled displays** (125%/150%) are handled in code (per-monitor DPI awareness) but untested; the test machine runs at 100%.
