"""Text-to-speech on a dedicated thread (Windows SAPI for P0).

All public methods only put a command on a queue and return immediately, so the
key listener and OCR worker are never blocked by speech. The SAPI COM object is
created, used, and released only on the speech thread (COM apartment rules).

Debug CLI::

    python -m tanaw.speech "Hello from Tanaw"
"""

from __future__ import annotations

import argparse
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

logger = logging.getLogger(__name__)

MAX_UTTERANCE_CHARS = 4000
NOTHING_TO_REPEAT = "Nothing to repeat yet."

# SAPI SpeechVoiceSpeakFlags
_SVSF_ASYNC = 1
_SVSF_PURGE_BEFORE_SPEAK = 2
_SVSF_IS_NOT_XML = 16  # OCR text is never interpreted as SAPI XML markup


class SpeechError(RuntimeError):
    pass


class VoiceBackend(Protocol):
    """What the speaker needs from a TTS engine. Used only on the speech thread."""

    def speak(self, text: str) -> None:
        """Start speaking ``text`` asynchronously, cutting off anything already playing."""

    def stop(self) -> None:
        """Stop speaking immediately and discard anything queued."""

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def wait_until_done(self, timeout_ms: int) -> bool: ...

    def poll_start_times(self) -> list[float]:
        """``time.perf_counter()`` values at which audio started since the last poll."""
        ...

    def close(self) -> None: ...


#: Called on the speech thread with the perf_counter time speech audio started.
SpeechStartCallback = Callable[[float], None]
# Give up waiting for a start event after this long (e.g. utterance was purged).
_START_WAIT_LIMIT_S = 5.0


class _SpVoice(Protocol):
    """The subset of the SAPI ``SpVoice`` COM interface we call."""

    Rate: int
    Volume: int

    def Speak(self, text: str, flags: int) -> int: ...  # noqa: N802 (COM name)

    def Pause(self) -> None: ...  # noqa: N802

    def Resume(self) -> None: ...  # noqa: N802

    def WaitUntilDone(self, ms_timeout: int) -> bool: ...  # noqa: N802


def validate_rate(rate: int) -> int:
    if not -10 <= rate <= 10:
        raise ValueError(f"speech rate must be between -10 and 10, got {rate}")
    return rate


def validate_volume(volume: int) -> int:
    if not 0 <= volume <= 100:
        raise ValueError(f"speech volume must be between 0 and 100, got {volume}")
    return volume


class SapiVoice:
    """Windows SAPI voice. Construct on the thread that will use it."""

    def __init__(self, *, rate: int = 0, volume: int = 100) -> None:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self._com_ready = True
        starts: list[float] = []

        class _Events:
            # SAPI's StartStream event: audio for an utterance began. Delivered as a
            # window message, so poll_start_times() pumps messages on this thread.
            def OnStartStream(  # noqa: N802 (COM event name)
                self, stream_number: object, stream_position: object
            ) -> None:
                starts.append(time.perf_counter())

        self._starts = starts
        try:
            dispatch = win32com.client.Dispatch("SAPI.SpVoice")
            self._voice = cast(_SpVoice, dispatch)
            self._voice.Rate = validate_rate(rate)
            self._voice.Volume = validate_volume(volume)
            self._event_sink = win32com.client.WithEvents(dispatch, _Events)  # type: ignore[no-untyped-call]
        except Exception:
            pythoncom.CoUninitialize()
            self._com_ready = False
            raise

    def speak(self, text: str) -> None:
        self._voice.Speak(text, _SVSF_ASYNC | _SVSF_PURGE_BEFORE_SPEAK | _SVSF_IS_NOT_XML)

    def stop(self) -> None:
        self._voice.Speak("", _SVSF_ASYNC | _SVSF_PURGE_BEFORE_SPEAK | _SVSF_IS_NOT_XML)

    def pause(self) -> None:
        self._voice.Pause()

    def resume(self) -> None:
        self._voice.Resume()

    def wait_until_done(self, timeout_ms: int) -> bool:
        return bool(self._voice.WaitUntilDone(timeout_ms))

    def poll_start_times(self) -> list[float]:
        import pythoncom

        pythoncom.PumpWaitingMessages()
        times = list(self._starts)
        self._starts.clear()
        return times

    def close(self) -> None:
        if self._com_ready:
            import pythoncom

            del self._event_sink
            del self._voice
            pythoncom.CoUninitialize()
            self._com_ready = False


# --- commands sent to the speech thread -------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Speak:
    text: str
    remember: bool
    on_start: SpeechStartCallback | None = None


class _Stop:
    pass


class _Repeat:
    pass


class _TogglePause:
    pass


@dataclass(frozen=True, slots=True)
class _Flush:
    done: threading.Event
    wait_for_audio_ms: int


class _Shutdown:
    pass


_Command = _Speak | _Stop | _Repeat | _TogglePause | _Flush | _Shutdown


class Speaker:
    """Owns the speech thread. Safe to call from any thread; never blocks."""

    def __init__(
        self,
        backend_factory: Callable[[], VoiceBackend] = SapiVoice,
        *,
        log_text: bool = False,
    ) -> None:
        self._backend_factory = backend_factory
        self._log_text = log_text
        self._commands: queue.Queue[_Command] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="tanaw-speech", daemon=True)
        self._ready = threading.Event()
        self._init_error: BaseException | None = None
        # Owned by the speech thread only:
        self._last_text: str | None = None
        self._paused = False
        # (callback, perf_counter when Speak was called) for the utterance in flight.
        self._awaiting_start: tuple[SpeechStartCallback, float] | None = None

    # -- lifecycle -----------------------------------------------------------------------

    def start(self, timeout_s: float = 10.0) -> None:
        """Start the thread and wait until the voice is ready. Raises SpeechError on failure."""
        self._thread.start()
        if not self._ready.wait(timeout_s):
            raise SpeechError("The speech engine did not start in time.")
        if self._init_error is not None:
            raise SpeechError(f"Could not start the speech engine: {self._init_error}")

    def close(self, timeout_s: float = 2.0) -> None:
        if self._thread.is_alive():
            self._commands.put(_Shutdown())
            self._thread.join(timeout_s)

    # -- public commands (non-blocking) -------------------------------------------------

    def speak(
        self, text: str, *, remember: bool = True, on_start: SpeechStartCallback | None = None
    ) -> None:
        """Interrupt current speech and say ``text``.

        ``remember`` makes it the repeat target. ``on_start`` is called (on the speech
        thread, must be quick) with the perf_counter time the audio started.
        """
        self._commands.put(_Speak(text, remember, on_start))

    def stop(self) -> None:
        self._commands.put(_Stop())

    def repeat_last(self) -> None:
        self._commands.put(_Repeat())

    def toggle_pause(self) -> None:
        self._commands.put(_TogglePause())

    def flush(self, timeout_s: float = 5.0, *, wait_for_audio_ms: int = 0) -> bool:
        """Block until all earlier commands ran (and optionally until audio finished).

        For tests and the debug CLI only; never call this from the key listener.
        """
        done = threading.Event()
        self._commands.put(_Flush(done, wait_for_audio_ms))
        return done.wait(timeout_s)

    # -- speech thread -------------------------------------------------------------------

    def _run(self) -> None:
        try:
            backend = self._backend_factory()
        except Exception as exc:  # reported to the caller by start()
            self._init_error = exc
            self._ready.set()
            return
        self._ready.set()
        logger.info("speech thread started")
        try:
            while True:
                # Poll quickly only while waiting for an utterance's start event.
                timeout = 0.005 if self._awaiting_start is not None else 0.1
                command: _Command | None
                try:
                    command = self._commands.get(timeout=timeout)
                except queue.Empty:
                    command = None
                self._check_started(backend)
                if command is None:
                    continue
                if isinstance(command, _Shutdown):
                    backend.stop()
                    break
                try:
                    self._handle(backend, command)
                except Exception:
                    logger.exception("speech command failed: %s", type(command).__name__)
        finally:
            backend.close()
            logger.info("speech thread stopped")

    def _check_started(self, backend: VoiceBackend) -> None:
        try:
            starts = backend.poll_start_times()
        except Exception:
            logger.exception("polling speech start events failed")
            return
        if self._awaiting_start is None:
            return
        callback, called_at = self._awaiting_start
        started = next((t for t in starts if t >= called_at), None)
        if started is not None:
            self._awaiting_start = None
            try:
                callback(started)
            except Exception:
                logger.exception("speech start callback failed")
        elif time.perf_counter() - called_at > _START_WAIT_LIMIT_S:
            self._awaiting_start = None

    def _handle(self, backend: VoiceBackend, command: _Command) -> None:
        if isinstance(command, _Speak):
            self._say(backend, command.text, remember=command.remember,
                      on_start=command.on_start)
        elif isinstance(command, _Stop):
            self._awaiting_start = None
            backend.stop()
            self._ensure_resumed(backend)
            logger.debug("speech stopped")
        elif isinstance(command, _Repeat):
            if self._last_text is None:
                self._say(backend, NOTHING_TO_REPEAT, remember=False)
            else:
                self._say(backend, self._last_text, remember=False)
        elif isinstance(command, _TogglePause):
            if self._paused:
                backend.resume()
                self._paused = False
            else:
                backend.pause()
                self._paused = True
            logger.debug("speech paused=%s", self._paused)
        elif isinstance(command, _Flush):
            if command.wait_for_audio_ms > 0:
                backend.wait_until_done(command.wait_for_audio_ms)
            command.done.set()

    def _say(
        self,
        backend: VoiceBackend,
        text: str,
        *,
        remember: bool,
        on_start: SpeechStartCallback | None = None,
    ) -> None:
        cleaned = " ".join(text.split())
        if not cleaned:
            return
        if len(cleaned) > MAX_UTTERANCE_CHARS:
            cleaned = cleaned[:MAX_UTTERANCE_CHARS]
        if remember:
            self._last_text = cleaned
        # A new utterance while paused would sit silently in the queue; the player
        # pressed a key to hear something, so resume.
        self._ensure_resumed(backend)
        backend.poll_start_times()  # drop stale start events from earlier utterances
        # Purge-before-speak: an earlier utterance still waiting will never start.
        self._awaiting_start = None if on_start is None else (on_start, time.perf_counter())
        backend.speak(cleaned)
        if self._log_text:
            logger.info("speak %r", cleaned)
        else:
            logger.debug("speak chars=%d remember=%s", len(cleaned), remember)

    def _ensure_resumed(self, backend: VoiceBackend) -> None:
        if self._paused:
            backend.resume()
            self._paused = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tanaw.speech", description="Speak a line.")
    parser.add_argument("text", help="What to say")
    parser.add_argument("--rate", type=int, default=0, help="-10 (slow) to 10 (fast)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        validate_rate(args.rate)
    except ValueError as exc:
        parser.error(str(exc))

    speaker = Speaker(lambda: SapiVoice(rate=args.rate))
    try:
        speaker.start()
    except SpeechError as exc:
        print(f"Error: {exc}")
        return 1
    speaker.speak(args.text)
    finished = speaker.flush(timeout_s=30.0, wait_for_audio_ms=25_000)
    speaker.close()
    print("done" if finished else "timed out")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
