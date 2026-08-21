"""
Pytest wrapper around scripts/selftest_voice.py (handoff §5): "test the
voice pipeline WITHOUT a human — this is the important part." Every
assertion below runs against real hardware, real ONNX inference, real
edge-tts synthesis, and a real Groq API call — nothing here is a fixture
standing in for the thing being tested. That's the whole point of §5: prove
the pipeline works by running it, not by reading the code.

These are slower and more network/hardware-dependent than tests/test_safety.py
by nature — that's inherent to what's being verified, not a shortcut taken
here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import selftest_voice as sv  # noqa: E402


def test_mic_is_real():
    """Capture 2s from the configured device: RMS above the noise floor, not
    an all-zero buffer. Proves the device opens and Windows mic permission
    is granted."""
    result = sv.check_mic()
    assert "error" not in result, result.get("error")
    assert not result["all_zero"], "captured buffer was all zeros"
    assert result["rms"] > 1e-6, f"RMS too low to be real analog input: {result['rms']}"


def test_tts_is_real():
    """Synthesise, decode, confirm non-silent PCM at the expected rate, and
    that the output stream accepted the frames."""
    result = sv.check_tts()
    assert result["sample_rate"] == 24000
    assert result["peak_amplitude"] > 0.01, "PCM is effectively silent"
    assert result["played"], result.get("play_error")


def test_wake_word_fires_on_true_positive_not_on_true_negative():
    """
    Real true-positive/true-negative test: the CONFIGURED wake phrase fires,
    'what's the weather like' does not. Reads the phrase from config so a
    retrained wake model is tested rather than the phrase it replaced.
    """
    result = sv.check_wake_word()
    assert result["true_positive_fired"], (
        f"wake word never fired on positive audio (best score "
        f"{result['true_positive_best_score']:.3f} vs threshold {result['threshold']})"
    )
    assert not result["true_negative_fired"], "wake word false-fired on unrelated speech"
    assert result["true_negative_best_score"] < result["threshold"]


def test_vad_detects_speech_and_not_silence():
    """Speech crosses the threshold, true silence doesn't, and
    UtteranceCollector returns audio after the configured silence window."""
    result = sv.check_vad()
    assert result["max_speech_prob"] >= result["threshold"], (
        f"VAD never crossed threshold on real speech: {result['max_speech_prob']:.4f} "
        f"< {result['threshold']}"
    )
    assert result["max_silence_prob"] < result["threshold"]
    assert result["collector_returned_samples"] > 0, "UtteranceCollector never closed the utterance"


def test_stt_roundtrips_through_groq():
    """Synthesise a known sentence, send it to Groq, confirm the transcript
    matches within a normalised edit distance."""
    result = sv.check_stt_roundtrip()
    if result.get("skipped"):
        pytest.skip(result["reason"])
    assert result["engine_used"] == "groq"
    assert result["edit_distance_ratio"] < 0.3, (
        f"transcript too far from expected: {result['transcript']!r} vs "
        f"{result['expected']!r} (ratio {result['edit_distance_ratio']})"
    )


def test_stt_falls_back_to_moonshine_when_groq_is_unreachable():
    """With Groq forced to fail the way a real outage would, moonshine must
    still produce a transcript — offline, no network needed."""
    result = sv.check_stt_fallback()
    assert "error" not in result, result.get("error")
    assert result["engine_used"] == "moonshine"
    assert result["transcript"], "moonshine returned an empty transcript"


def test_barge_in_latency_is_measured():
    """Not a pass/fail threshold — the handoff explicitly says not to claim
    a target that wasn't measured. This asserts the interrupt eventually
    takes effect and that a real number came back."""
    result = sv.check_barge_in_latency()
    assert result["ok"], result.get("error")
    assert result["measured"] is True
    assert isinstance(result["injection_to_stop_ms"], float)


def test_failure_paths_keep_jarvis_alive():
    """Empty audio, pure silence, a 30s utterance, an invalid Groq key, a
    simulated timeout, no network — every case must end in either a clean
    result or the controlled RuntimeError transcribe() already raises when
    every engine fails. Never an unhandled crash."""
    result = sv.check_failure_paths()
    failures = {name: case for name, case in result["cases"].items() if not case.get("ok")}
    assert not failures, failures
