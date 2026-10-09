from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from tanaw.speech import (
    MAX_UTTERANCE_CHARS,
    NOTHING_TO_REPEAT,
    Speaker,
    SpeechError,
    VoiceBackend,
    validate_rate,
    validate_volume,
)


class FakeVoice:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.thread: threading.Thread | None = None

    def _record(self, name: str, arg: str = "") -> None:
        self.thread = threading.current_thread()
        self.calls.append((name, arg))

    def speak(self, text: str) -> None:
        self._record("speak", text)

    def stop(self) -> None:
        self._record("stop")

    def pause(self) -> None:
        self._record("pause")

    def resume(self) -> None:
        self._record("resume")

    def wait_until_done(self, timeout_ms: int) -> bool:
        return True

    def close(self) -> None:
        self._record("close")


@pytest.fixture
def voice() -> FakeVoice:
    return FakeVoice()


@pytest.fixture
def speaker(voice: FakeVoice) -> Iterator[Speaker]:
    def factory() -> VoiceBackend:
        return voice

    s = Speaker(factory)
    s.start()
    yield s
    s.close()


def test_speaks_on_its_own_thread(speaker: Speaker, voice: FakeVoice) -> None:
    speaker.speak("Fight")
    assert speaker.flush()
    assert voice.calls == [("speak", "Fight")]
    assert voice.thread is not None
    assert voice.thread is not threading.current_thread()
    assert voice.thread.name == "tanaw-speech"


def test_repeat_says_last_remembered_text(speaker: Speaker, voice: FakeVoice) -> None:
    speaker.speak("Item")
    speaker.speak("Selection unclear", remember=False)
    speaker.repeat_last()
    speaker.flush()
    assert voice.calls[-1] == ("speak", "Item")


def test_repeat_with_nothing_said(speaker: Speaker, voice: FakeVoice) -> None:
    speaker.repeat_last()
    speaker.flush()
    assert voice.calls == [("speak", NOTHING_TO_REPEAT)]


def test_pause_toggles_and_new_speech_resumes(speaker: Speaker, voice: FakeVoice) -> None:
    speaker.speak("Act")
    speaker.toggle_pause()
    speaker.speak("Spare")
    speaker.flush()
    assert voice.calls == [("speak", "Act"), ("pause", ""), ("resume", ""), ("speak", "Spare")]


def test_pause_then_resume(speaker: Speaker, voice: FakeVoice) -> None:
    speaker.toggle_pause()
    speaker.toggle_pause()
    speaker.flush()
    assert voice.calls == [("pause", ""), ("resume", "")]


def test_stop_while_paused_leaves_voice_unpaused(speaker: Speaker, voice: FakeVoice) -> None:
    speaker.toggle_pause()
    speaker.stop()
    speaker.flush()
    assert voice.calls == [("pause", ""), ("stop", ""), ("resume", "")]


def test_whitespace_is_normalised_and_empty_text_ignored(
    speaker: Speaker, voice: FakeVoice
) -> None:
    speaker.speak("   ")
    speaker.speak("HP\n 120 \t/ 160")
    speaker.flush()
    assert voice.calls == [("speak", "HP 120 / 160")]


def test_long_text_is_truncated(speaker: Speaker, voice: FakeVoice) -> None:
    speaker.speak("a" * (MAX_UTTERANCE_CHARS + 50))
    speaker.flush()
    assert len(voice.calls[0][1]) == MAX_UTTERANCE_CHARS


def test_close_stops_and_releases_backend(voice: FakeVoice) -> None:
    s = Speaker(lambda: voice)
    s.start()
    s.close()
    assert voice.calls[-2:] == [("stop", ""), ("close", "")]


def test_backend_failure_is_reported_by_start() -> None:
    def broken() -> VoiceBackend:
        raise OSError("no voices installed")

    with pytest.raises(SpeechError, match="no voices installed"):
        Speaker(broken).start()


def test_rate_and_volume_validation() -> None:
    assert validate_rate(5) == 5
    assert validate_volume(80) == 80
    with pytest.raises(ValueError):
        validate_rate(11)
    with pytest.raises(ValueError):
        validate_volume(-1)
