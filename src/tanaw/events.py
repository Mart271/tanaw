"""Append-only session event log: ``logs/session-<timestamp>.jsonl``.

Metadata only (timings, counts, sizes, modes, error kinds). The API accepts only
numbers, booleans, and short identifier strings, so recognised text or images
cannot end up in the file by accident.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import IO

Scalar = int | float | bool | None | str

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")


def _check_field(name: str, value: Scalar) -> Scalar:
    if isinstance(value, str) and not _IDENTIFIER.match(value):
        raise ValueError(
            f"event field {name!r} must be a short identifier (no free text in event logs)"
        )
    if isinstance(value, float):
        return round(value, 3)
    return value


class EventLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._file: IO[str] | None = path.open("a", encoding="utf-8")

    @classmethod
    def for_new_session(cls, directory: Path = Path("logs")) -> EventLog:
        return cls(directory / f"session-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")

    def write(self, event: str, **fields: Scalar) -> None:
        if not _IDENTIFIER.match(event):
            raise ValueError(f"invalid event name {event!r}")
        record: dict[str, Scalar] = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event}
        for name, value in fields.items():
            record[name] = _check_field(name, value)
        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            if self._file is None:
                return
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def __enter__(self) -> EventLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
