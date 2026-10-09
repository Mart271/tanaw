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

    def close(self) -> None: ...


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
        try:
            self._voice = cast(_SpVoice, win32com.client.Dispatch("SAPI.SpVoice"))
            self._voice.Rate = validate_rate(rate)
            self._voice.Volume = validate_volume(volume)
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

    def close(self) -> None:
        if self._com_ready:
            import pythoncom

            del self._voice
            pythoncom.CoUninitialize()
            self._com_ready = False


# --- commands sent to the speech thread -------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Speak:
    text: str
    remember: bool


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

    def speak(self, text: str, *, remember: bool = True) -> None:
        """Interrupt current speech and say ``text``. ``remember`` makes it the repeat target."""
        self._commands.put(_Speak(text, remember))

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
                command = self._commands.get()
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

    def _handle(self, backend: VoiceBackend, command: _Command) -> None:
        if isinstance(command, _Speak):
            self._say(backend, command.text, remember=command.remember)
        elif isinstance(command, _Stop):
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

    def _say(self, backend: VoiceBackend, text: str, *, remember: bool) -> None:
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
