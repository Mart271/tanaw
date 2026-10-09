"""Command line entry point: ``python -m tanaw --window "DELTARUNE"``."""

from __future__ import annotations

import argparse
import logging

from pydantic import ValidationError

from tanaw.app import run
from tanaw.settings import AppSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tanaw",
        description="Tanaw: reads game text aloud, fully offline. Read Mode: Ctrl+Alt+R.",
    )
    parser.add_argument("--window", required=True,
                        help="Case-insensitive part of the game window's title, e.g. DELTARUNE")
    parser.add_argument("--debug-captures", action="store_true",
                        help="Save captured frames to ./debug/ (deleted on clean exit)")
    parser.add_argument("--verbose-text", action="store_true",
                        help="Include recognised text in logs (off by default)")
    parser.add_argument("--min-confidence", type=float, default=0.6,
                        help="Drop OCR boxes below this confidence (0 to 1, default 0.6)")
    parser.add_argument("--upscale", type=float, default=1.0,
                        help="Upscale frames before OCR, 1 to 4 (try 2 for pixel fonts)")
    parser.add_argument("--nearest", action="store_true",
                        help="Use nearest-neighbour upscaling instead of cubic")
    parser.add_argument("--rate", type=int, default=0, help="Speech rate, -10 to 10")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        settings = AppSettings(
            window=args.window,
            debug_captures=args.debug_captures,
            verbose_text=args.verbose_text,
            min_confidence=args.min_confidence,
            upscale=args.upscale,
            interpolation="nearest" if args.nearest else "cubic",
            speech_rate=args.rate,
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        parser.error(problems)
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
