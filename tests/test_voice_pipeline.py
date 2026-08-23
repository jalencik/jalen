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

    # SYNTHESIS FIRST, MODEL SECOND. edge-tts occasionally returns empty or
    # near-silent audio, and silence scores ~0.00 against the wake model —
    # which the old assertion reported as "the wake word never fired",
    # blaming the one component that was working. Checking the audio first
    # means a bad take fails with "edge-tts returned silence" instead.
    assert result["positive_audio_samples"] > 8000, (
        f"edge-tts returned only {result['positive_audio_samples']} samples — "
        "this is a synthesis failure, not a wake-word failure"
    )
    assert result["positive_audio_peak"] > 0.01, (
        f"edge-tts returned near-silent audio (peak "
        f"{result['positive_audio_peak']:.4f}) — this is a synthesis failure, "
        "not a wake-word failure. Re-run before treating it as a regression."
    )

    assert result["true_positive_fired"], (
        f"wake word never fired on positive audio (best score "
        f"{result['true_positive_best_score']:.3f} vs threshold "
        f"{result['threshold']}), and the audio was real "
        f"({result['positive_audio_samples']} samples, peak "
        f"{result['positive_audio_peak']:.2f}) — so this IS the model."
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


def test_the_wake_clip_is_padded_at_the_end_not_the_front():
    """
    THE "FLAKY NETWORK TEST" WAS A TEST BUG.

    test_wake_word_fires_on_true_positive_not_on_true_negative failed roughly
    one run in three for days, was re-run each time, and was excused as
    edge-tts flakiness. It was not the network and it was not the model.

    edge-tts returns "Hey Jalen" with about 1.4s of trailing silence most of
    the time, and with almost none occasionally. The SPEECH is identical —
    0.36s either way, measured. openWakeWord scores a ~1.96s window that has
    to CLOSE after the phrase, so without a tail the window never completes
    and the score sits at zero.

    Measured on one truncated clip:

        truncated to 0.57s          -> 0.000
        + 1.4s of LEADING silence   -> 0.000
        + 1.4s of TRAILING silence  -> 1.000

    A live microphone never hits this: it keeps delivering frames after he
    stops speaking. Leading padding was the obvious guess and it changed
    nothing, which is why this test pins the SIDE.
    """
    import numpy as np

    from jarvis.audio.wake import WakeWord
    from jarvis.config import CONFIG

    def best_score(audio):
        wake = WakeWord(CONFIG)
        wake.load()
        best = 0.0
        for frame in sv.to_frames(audio):
            wake.feed(frame)
            best = max(best, wake.last_score)
        return best

    full = sv.synth_16k(CONFIG.get_path("identity.wake_word", "hey jalen"))
    envelope = np.abs(full)
    loud = np.flatnonzero(envelope > envelope.max() * 0.05)
    # Reproduce the failing shape deterministically: the phrase with its
    # trailing silence cut off, which is what the short response is.
    truncated = full[: loud[-1] + int(0.05 * 16000)]

    assert best_score(truncated) < 0.5, (
        "the truncated clip now scores - this test can no longer demonstrate "
        "the failure it guards against"
    )
    assert best_score(sv.pad_for_wake(truncated)) > 0.5, (
        "padding no longer rescues a truncated clip"
    )

    silence = np.zeros(len(truncated), dtype=np.float32)
    front_padded = np.concatenate([silence, truncated])
    assert best_score(front_padded) < 0.5, (
        "front-padding appears to work, which contradicts the measurement "
        "this fix was built on - re-measure before trusting it"
    )


def test_the_padding_lands_at_the_end():
    """Structural, so the timing test above cannot pass for the wrong reason."""
    import numpy as np

    short = np.ones(1000, dtype=np.float32)
    padded = sv.pad_for_wake(short)
    assert len(padded) == int(sv.SAMPLE_RATE * sv.WAKE_WINDOW_S)
    assert padded[0] == 1.0, "the speech was pushed to the back"
    assert padded[-1] == 0.0, "the silence is not at the end"


def test_both_clips_get_the_same_treatment():
    """
    Padding the positive and not the negative would make the test pass for
    the wrong reason - a full window for the phrase and a starved one for
    the control.
    """
    import inspect

    source = inspect.getsource(sv.check_wake_word)
    assert source.count("pad_for_wake") == 2, (
        "the positive and negative clips are not padded identically"
    )
