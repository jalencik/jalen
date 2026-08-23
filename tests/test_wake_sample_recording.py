"""
Recording real wake-word samples — HANDOFF item 3.

The wake model has never heard his voice: 2,160 synthetic positives across 45
edge-tts voices, and an unmeasured miss rate on the one person who uses it.
scripts/record_wake_samples.py is how that gets fixed in fifteen minutes
instead of by hand.

WHAT IS ACTUALLY UNDER TEST HERE is the rejection logic, because a bad
positive is worse than a missing one. A silent or clipped clip labelled "hey
jalen" teaches the model that the wake word sounds like room tone or a
crackle — and then it fires on room tone at three in the morning. Every
check that stops such a clip reaching disk is pinned below.

The recording itself needs a microphone and is not tested. It contains no
decisions: sd.rec, then check_take.
"""
from __future__ import annotations

import wave

import numpy as np
import pytest

from scripts import record_wake_samples as rec


def speech_like(duration_s: float = 0.8, peak: float = 5000.0,
                lead_s: float = 0.4, total_s: float = 2.6) -> np.ndarray:
    """
    A synthetic "utterance": a burst of voiced-looking noise inside silence.

    Amplitude-modulated at 6 Hz because a flat tone is not what the energy
    gate is built to find — real speech has syllables, and the trimmer keys
    on exactly that.
    """
    total = int(rec.SAMPLE_RATE * total_s)
    out = np.zeros(total, dtype=np.float32)
    n = int(rec.SAMPLE_RATE * duration_s)
    t = np.arange(n) / rec.SAMPLE_RATE
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 6.0 * t)
    carrier = np.random.RandomState(0).randn(n)
    burst = (carrier * envelope).astype(np.float32)
    # Normalise so `peak` is the ACTUAL peak sample, not a scale factor.
    # Without this, randn's tail puts the real peak ~4x higher than asked
    # for, and a test written to sit just under a threshold quietly sits
    # above it — the test passes for the wrong reason, or fails for one.
    loudest = float(np.abs(burst).max()) or 1.0
    burst = burst / loudest * peak
    start = int(rec.SAMPLE_RATE * lead_s)
    out[start : start + n] = burst
    return out


# ---------------------------------------------------------------------------
# What must be rejected. Each of these, saved, poisons the training set.
# ---------------------------------------------------------------------------
def test_silence_is_rejected():
    """
    The commonest real failure: wrong input device selected, so every take is
    digital silence. Thirty of those labelled as the wake word would teach it
    that silence IS the wake word.
    """
    with pytest.raises(rec.Rejected) as why:
        rec.check_take(np.zeros(int(rec.SAMPLE_RATE * 2.6), dtype=np.float32))
    assert "too quiet" in str(why.value)
    # And it says how to fix it, rather than only that it failed.
    assert "--check" in str(why.value)


def test_an_off_mic_take_is_rejected():
    with pytest.raises(rec.Rejected) as why:
        rec.check_take(speech_like(peak=300.0))
    assert "too quiet" in str(why.value)


def test_a_clipped_take_is_rejected():
    """
    A squared-off waveform is not his voice any more — the harmonics are
    manufactured by the clipping, so the spectrogram teaches the model
    something that only happens when he is too close to the microphone.
    """
    with pytest.raises(rec.Rejected) as why:
        rec.check_take(speech_like(peak=32767.0))
    assert "clipping" in str(why.value)


def test_a_cut_off_phrase_is_rejected():
    with pytest.raises(rec.Rejected) as why:
        rec.check_take(speech_like(duration_s=0.15))
    assert "too short" in str(why.value)


def test_a_take_with_a_whole_sentence_in_it_is_rejected():
    """
    "Hey Jalen, open Chrome" is not a wake-word positive. Training on it
    widens the accept region to cover whatever followed the phrase.
    """
    with pytest.raises(rec.Rejected) as why:
        rec.check_take(speech_like(duration_s=2.2, lead_s=0.1, total_s=2.6))
    assert "too long" in str(why.value)


def test_the_vad_gets_the_final_say():
    """
    A clip loud enough and long enough, that still contains no speech — a
    door, a cough, a chair. Jalen's own VAD is asked, so a clip rejected here
    is one the running assistant would also have ignored.
    """
    class SilentVAD:
        def probability(self, frame):
            return 0.0

    with pytest.raises(rec.Rejected) as why:
        rec.check_take(speech_like(), SilentVAD())
    assert "couldn't hear speech" in str(why.value)


def test_a_broken_vad_does_not_reject_a_good_take():
    """
    Fail open, deliberately. A VAD that throws is a broken tool, not evidence
    about the audio, and losing good recordings to it would be silent damage.
    """
    class BrokenVAD:
        def probability(self, frame):
            raise RuntimeError("model not loaded")

    assert rec.check_take(speech_like(), BrokenVAD()) is not None


# ---------------------------------------------------------------------------
# What must be accepted, and in what shape.
# ---------------------------------------------------------------------------
def test_a_good_take_is_accepted():
    class LiveVAD:
        def probability(self, frame):
            return 0.9

    clip = rec.check_take(speech_like(), LiveVAD())
    assert len(clip) == rec.CLIP_SAMPLES


def test_the_saved_clip_is_exactly_the_training_clip_length():
    """
    train_wake_word.py's feature width depends on CLIP_SECONDS. A clip of the
    wrong length produces a feature block that will not fit openWakeWord's
    runtime, and the failure appears at export time with no hint of where it
    came from.
    """
    for duration in (0.4, 0.8, 1.5):
        clip = rec.check_take(speech_like(duration_s=duration))
        assert len(clip) == rec.CLIP_SAMPLES == int(rec.SAMPLE_RATE * 2.0)


def test_the_speech_is_trimmed_and_repositioned():
    """
    A take recorded with a long silent lead must not be saved with the phrase
    at the very end, where the training window would clip it.
    """
    clip = rec.check_take(speech_like(duration_s=0.8, lead_s=1.5, total_s=2.6))
    loud = np.flatnonzero(np.abs(clip) > 500)
    assert len(loud) > 0
    # The phrase now starts in the first half of the clip, not at 1.5s.
    assert loud[0] < rec.CLIP_SAMPLES // 2


def test_the_written_file_matches_the_synthetic_corpus_format(tmp_path):
    """
    16 kHz, mono, 16-bit — the same as every file train_wake_word.py already
    reads. read_wav() would resample a mismatch silently, which is a quiet
    quality loss rather than an error, so the format is pinned here instead.
    """
    path = tmp_path / "real_hey_000.wav"
    rec.write_wav(path, speech_like()[: rec.CLIP_SAMPLES])
    with wave.open(str(path)) as fh:
        assert fh.getnchannels() == 1
        assert fh.getsampwidth() == 2
        assert fh.getframerate() == 16000
        assert fh.getnframes() == rec.CLIP_SAMPLES


def test_clipping_on_write_cannot_overflow_int16():
    """
    numpy wraps on int16 overflow rather than saturating, so a value of 40000
    would be written as a large NEGATIVE sample — an audible click, in the
    training data, labelled as the wake word.
    """
    loud = np.full(rec.CLIP_SAMPLES, 40000.0, dtype=np.float32)
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(16000)
        fh.writeframes(np.clip(loud, -32768, 32767).astype(np.int16).tobytes())
    data = np.frombuffer(buf.getvalue()[44:], dtype=np.int16)
    assert data.max() == 32767 and data.min() >= 0


# ---------------------------------------------------------------------------
# Fitting into the existing pipeline.
# ---------------------------------------------------------------------------
def test_real_takes_are_named_apart_from_the_synthetic_ones():
    """
    The synthetic corpus is named after the edge-tts voice that made it. Real
    takes carry a distinct prefix so "delete the synthetic ones and retrain
    on just my voice" is one glob, and so nobody has to guess which files
    came from a person.
    """
    assert rec._prefix() == "real_"
    from scripts import train_wake_word as train
    for existing in list(train.POS_DIR.glob("*.wav"))[:20]:
        assert not existing.name.startswith("real_"), (
            "a synthetic positive is using the real-recording prefix"
        )


def test_the_recorder_and_the_trainer_agree_on_the_clip_shape():
    """
    Two files with their own copies of these constants. If they ever drift,
    the recordings become subtly wrong training data rather than an error.
    """
    from scripts import train_wake_word as train

    assert rec.SAMPLE_RATE == train.SAMPLE_RATE
    assert rec.CLIP_SAMPLES == train.CLIP_SAMPLES
    assert rec.CLIP_SECONDS == train.CLIP_SECONDS


def test_every_recorded_phrase_is_one_the_trainer_treats_as_positive():
    """
    Recording "Yo Jalen" would produce clips labelled positive that the
    trainer's own phrase list does not contain — teaching a wake phrase that
    the rest of the system does not agree is one.
    """
    from scripts import train_wake_word as train

    assert set(rec.PHRASES) == set(train.WAKE_PHRASES)


def test_the_trainer_picks_up_new_positives_without_a_stale_cache():
    """
    build_dataset caches embeddings under a filename containing the clip
    count, so adding recordings invalidates it automatically. If that key
    ever loses the count, a retrain would silently reuse the old features and
    report an improvement that never happened.
    """
    from scripts import train_wake_word as train
    import inspect

    source = inspect.getsource(train.build_dataset)
    assert 'features_{len(clips)}' in source, (
        "the embedding cache key no longer includes the clip count — new "
        "recordings would be silently ignored on retrain"
    )
