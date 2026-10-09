"""Keypress -> speech-start latency from Tanaw's event logs.

    .venv\\Scripts\\python.exe scripts\\bench_latency.py            (all logs/session-*.jsonl)
    .venv\\Scripts\\python.exe scripts\\bench_latency.py --latest   (newest session only)

"Speech start" is when SAPI's StartStream event reached Tanaw's speech thread
(polled every 5 ms while waiting), so it may be up to ~5 ms after audio began.
Prints only what the logs contain, with sample counts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tanaw.bench import format_ms, load_events, speech_latencies, summarize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", type=Path, default=Path("logs"), help="Log folder")
    parser.add_argument("--latest", action="store_true", help="Only the newest session")
    args = parser.parse_args(argv)

    sessions = sorted(args.logs.glob("session-*.jsonl"))
    if args.latest:
        sessions = sessions[-1:]
    if not sessions:
        print(f"No session logs found in {args.logs}/")
        return 1
    by_mode = speech_latencies(load_events(sessions))
    print(f"Sessions read: {len(sessions)} ({sessions[0].name} .. {sessions[-1].name})")
    if not by_mode:
        print("No speech_start events found: 0 samples. Run Tanaw, press Read/Focus keys, "
              "then run this again.")
        return 0
    print(f"{'mode':<8} {'samples':>7} {'median':>11} {'p90':>11} {'worst':>11} {'best':>11}")
    for mode in sorted(by_mode):
        s = summarize(by_mode[mode])
        print(f"{mode:<8} {s.count:>7} {format_ms(s.median):>11} {format_ms(s.p90):>11} "
              f"{format_ms(s.worst):>11} {format_ms(s.best):>11}")
    print("p90 is shown only with 10+ samples.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
