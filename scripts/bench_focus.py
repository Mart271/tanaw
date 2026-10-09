"""Focus Mode selection accuracy on labeled screenshots.

    .venv\\Scripts\\python.exe scripts\\bench_focus.py --labels fixtures\\labels.json

For each screenshot in the "focus" list, runs the same steps as Focus Mode
(locate cursor/highlight -> crop row -> OCR -> decide) and compares what it
would say with the label. Outcomes: correct, wrong (said the wrong item),
unclear (said "Selection unclear"), missed (silent although a menu item was
selected), false_alarm (spoke although no menu was open).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tanaw import netguard
from tanaw.bench import format_ms, load_labels, read_image, run_focus_case, summarize
from tanaw.ocr import OcrEngine
from tanaw.profile import load_profile

OUTCOMES = ("correct", "wrong", "unclear", "missed", "false_alarm")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", type=Path, default=Path("fixtures/labels.json"))
    parser.add_argument("--profile", type=Path, help="Override the profile in the labels file")
    parser.add_argument("--show-text", action="store_true",
                        help="Print expected/spoken text per screenshot")
    args = parser.parse_args(argv)
    netguard.install()

    if not args.labels.is_file():
        print(f"No labels file at {args.labels}: 0 samples. See tanaw/bench.py for the format.")
        return 1
    labels = load_labels(args.labels)
    base = args.labels.parent
    profile_path = args.profile or (base / labels.profile if labels.profile else None)
    if profile_path is None:
        print('No profile: pass --profile or set "profile" in the labels file.')
        return 1
    if not labels.focus:
        print('No "focus" entries in the labels file: 0 samples.')
        return 1
    loaded = load_profile(profile_path)
    engine = OcrEngine()
    engine.warm_up()

    cases = [run_focus_case(engine, loaded, read_image(base / item.image), item)
             for item in labels.focus]
    print(f"Profile: {loaded.profile.name}   screenshots: {len(cases)}")
    for c in cases:
        conf = "n/a" if c.confidence is None else f"{c.confidence:.2f}"
        line = (f"  {c.outcome:<12} {c.image:<36} locate={c.locate:<10} conf={conf:<5} "
                f"{c.ms:6.1f} ms")
        if args.show_text:
            line += f"  expected={c.expected!r} spoken={c.spoken!r}"
        print(line)
    total = len(cases)
    print("\nOutcome counts:")
    for outcome in OUTCOMES:
        n = sum(1 for c in cases if c.outcome == outcome)
        print(f"  {outcome:<12} {n:>4} / {total}")
    correct = sum(1 for c in cases if c.outcome == "correct")
    print(f"\nSelection accuracy: {correct}/{total} = {100 * correct / total:.1f}%")
    s = summarize([c.ms for c in cases])
    print(f"Processing time per screenshot (locate + OCR, no capture): median "
          f"{format_ms(s.median)}, worst {format_ms(s.worst)}, n={s.count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
