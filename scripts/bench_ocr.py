"""OCR error rate on labeled screenshots/crops.

    .venv\\Scripts\\python.exe scripts\\bench_ocr.py --labels fixtures\\labels.json

For each entry in the "ocr" list: OCR the image (or its "rect"), join lines in
reading order, compare with the expected text. Reports exact matches, character
error rate (CER = edits / expected characters), samples where boxes were dropped
for low confidence, and "confidently wrong" samples (wrong text, nothing flagged).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tanaw import netguard
from tanaw.bench import (
    format_ms,
    levenshtein,
    load_labels,
    normalize_text,
    read_image,
    run_ocr_case,
    summarize,
)
from tanaw.ocr import OcrEngine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", type=Path, default=Path("fixtures/labels.json"))
    parser.add_argument("--upscale", type=float, default=1.0)
    parser.add_argument("--min-confidence", type=float, default=0.6)
    parser.add_argument("--show-text", action="store_true")
    args = parser.parse_args(argv)
    netguard.install()

    if not args.labels.is_file():
        print(f"No labels file at {args.labels}: 0 samples. See tanaw/bench.py for the format.")
        return 1
    labels = load_labels(args.labels)
    if not labels.ocr:
        print('No "ocr" entries in the labels file: 0 samples.')
        return 1
    engine = OcrEngine()
    engine.warm_up()
    base = args.labels.parent
    cases = [run_ocr_case(engine, read_image(base / item.image), item,
                          upscale=args.upscale, min_confidence=args.min_confidence)
             for item in labels.ocr]

    print(f"Settings: upscale={args.upscale:g} min_confidence={args.min_confidence:g}   "
          f"samples: {len(cases)}")
    for c in cases:
        status = "exact" if c.exact else "DIFF "
        line = (f"  {status} CER={c.cer:5.3f} low-conf boxes={c.low_confidence_boxes:<2} "
                f"{c.ms:7.1f} ms  {c.image}")
        if args.show_text:
            line += f"\n        expected={c.expected!r}\n        got     ={c.got!r}"
        print(line)
    total = len(cases)
    exact = sum(c.exact for c in cases)
    chars = sum(len(normalize_text(c.expected)) for c in cases)
    edits = sum(levenshtein(normalize_text(c.expected), normalize_text(c.got)) for c in cases)
    flagged = sum(1 for c in cases if c.low_confidence_boxes > 0)
    confidently_wrong = sum(1 for c in cases if not c.exact and c.low_confidence_boxes == 0)
    print(f"\nExact matches: {exact}/{total}")
    rate = f" = {100 * edits / chars:.2f}%" if chars else ""
    print(f"Character error rate (all samples): {edits} edits / {chars} expected chars{rate}")
    print(f"Samples with low-confidence boxes dropped: {flagged}/{total}")
    print(f"Confidently wrong (wrong text, nothing flagged): {confidently_wrong}/{total}")
    s = summarize([c.ms for c in cases])
    print(f"OCR time: median {format_ms(s.median)}, worst {format_ms(s.worst)}, n={s.count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
