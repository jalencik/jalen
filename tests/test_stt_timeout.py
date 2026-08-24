"""
A stalled Groq must not become silence.

WHAT THE LOG SHOWED
-------------------
Across 214 timed turns in data/audit.jsonl, the gap between him finishing a
sentence and the transcript existing:

    p50  1.6s      p90  2.7s      p95  4.4s      max  36.0s

The 36 seconds is the interesting one, and it is not a slow network - it is
an unbounded one. `Transcriptions.create` was called without a timeout, and
the Groq SDK's own default is a 60 second read. So a Groq that stops
answering costs him a full minute of nothing, and the offline model that
exists for exactly this case never runs, because the call it is meant to
rescue has not come back yet.

THE FALLBACK WAS NEVER REAL. It could only trigger on a fast failure - a
403, a refused connection - and never on the slow one, which is the failure
mode that actually hurts. A bound is what turns it from a comment into a
mechanism.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from jarvis.audio.stt import Transcriber


class _Cfg(dict):
    """Minimal stand-in for the real Cfg's dotted lookup."""

    def __init__(self, **values):
        super().__init__()
        self._values = values

    def get_path(self, dotted, default=None):
        return self._values.get(dotted, default)


def _cfg(**overrides):
    values = {
        "stt.primary": "groq",
        "stt.fallback": "moonshine",
        "stt.language": "en",
        "stt.groq_model": "whisper-large-v3-turbo",
        "audio.sample_rate": 16000,
        "stt.groq_timeout_s": 8,
    }
    values.update(overrides)
    return _Cfg(**values)


class _Secrets:
    def require(self, _name):
        return "test-key"


def _audio(seconds: float = 1.0) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.float32)


class TestTheTimeoutExists:

    def test_default_is_bounded_and_short(self):
        stt = Transcriber(_cfg(), _Secrets())
        assert stt.groq_timeout_s == 8
        assert stt.groq_timeout_s < 60, (
            "anything at or above the SDK default is not a bound"
        )

    def test_it_is_configurable(self):
        stt = Transcriber(_cfg(**{"stt.groq_timeout_s": 3}), _Secrets())
        assert stt.groq_timeout_s == 3

    def test_the_timeout_is_actually_passed_to_groq(self, monkeypatch):
        """
        The value must reach the call. A setting that is read and then not
        used is the most convincing kind of broken.
        """
        seen = {}

        class _Transcriptions:
            @staticmethod
            def create(**kwargs):
                seen.update(kwargs)
                return "hello"

        class _Audio:
            transcriptions = _Transcriptions()

        class _Client:
            audio = _Audio()

        stt = Transcriber(_cfg(), _Secrets())
        monkeypatch.setattr(stt, "_groq_client", lambda: _Client())

        assert stt._via_groq(_audio()) == "hello"
        assert seen.get("timeout") == 8, (
            f"timeout never reached the API call; got {seen.get('timeout')!r}"
        )


class TestAStallFallsBackInsteadOfWaiting:

    def test_a_slow_groq_yields_to_the_local_model(self, monkeypatch):
        """
        The whole point. Groq hangs past its bound; the offline model
        answers; he gets a transcript instead of a minute of silence.
        """
        stt = Transcriber(_cfg(**{"stt.groq_timeout_s": 0.2}), _Secrets())

        def _hangs(_audio_in):
            time.sleep(0.5)                      # longer than the bound
            raise TimeoutError("Request timed out.")

        monkeypatch.setattr(stt, "_via_groq", _hangs)
        monkeypatch.setattr(stt, "_via_moonshine", lambda _a: "local words")

        started = time.monotonic()
        text = stt.transcribe(_audio())
        elapsed = time.monotonic() - started

        assert text == "local words"
        assert stt.last_engine == "moonshine"
        assert elapsed < 5, "it waited around instead of falling back"

    def test_the_reason_is_recorded_not_swallowed(self, monkeypatch):
        """
        A fallback that works silently is the dangerous kind: the transcript
        looks fine and nobody ever learns the primary is down.
        """
        stt = Transcriber(_cfg(), _Secrets())

        def _times_out(_audio_in):
            raise TimeoutError("Request timed out.")

        monkeypatch.setattr(stt, "_via_groq", _times_out)
        monkeypatch.setattr(stt, "_via_moonshine", lambda _a: "local words")

        stt.transcribe(_audio())
        assert "groq" in stt.last_fallback_reason.lower()
        assert "timeout" in stt.last_fallback_reason.lower()

    def test_both_failing_raises_rather_than_returning_nothing(self, monkeypatch):
        """Empty string would read as "he said nothing", which is a lie."""
        stt = Transcriber(_cfg(), _Secrets())
        monkeypatch.setattr(stt, "_via_groq",
                            lambda _a: (_ for _ in ()).throw(TimeoutError("x")))
        monkeypatch.setattr(stt, "_via_moonshine",
                            lambda _a: (_ for _ in ()).throw(RuntimeError("y")))
        with pytest.raises(RuntimeError, match="transcription failed"):
            stt.transcribe(_audio())

    def test_a_healthy_groq_is_untouched(self, monkeypatch):
        stt = Transcriber(_cfg(), _Secrets())
        monkeypatch.setattr(stt, "_via_groq", lambda _a: "groq words")
        monkeypatch.setattr(stt, "_via_moonshine",
                            lambda _a: pytest.fail("fell back unnecessarily"))
        assert stt.transcribe(_audio()) == "groq words"
        assert stt.last_engine == "groq"
        assert stt.last_fallback_reason == ""
