"""
Self-test the voice pipeline WITHOUT a human (handoff §5). Run directly for
a printed report:

    python scripts/selftest_voice.py

Or import the check_*() functions from tests/test_voice_pipeline.py for
pytest assertions — both callers share the exact same measured numbers,
nothing here is a canned fixture. Real edge-tts synthesis, real ONNX
inference through the real wake-word and VAD models, a real Groq API call,
a real microphone capture, real playback.

Only three things genuinely need O'ktam, per the handoff: how Jarvis
actually sounds to him, whether the wake word fires on HIS voice in HIS
room, and the Google OAuth consent click. Everything below is provable by
machine, on purpose, before asking him for anything.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from typing import Iterator

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.audio.mic import Microphone  # noqa: E402
from jarvis.audio.stt import Transcriber  # noqa: E402
from jarvis.audio.tts import Speaker  # noqa: E402
from jarvis.audio.vad import VAD, UtteranceCollector  # noqa: E402
from jarvis.audio.wake import WakeWord  # noqa: E402
from jarvis.config import CONFIG, SECRETS, Secrets  # noqa: E402

SAMPLE_RATE = 16000
FRAME = 512  # samples @16kHz = 32ms, matches config audio.frame_ms


# --------------------------------------------------------------------- helpers
def synth_24k(text: str) -> tuple[np.ndarray, int]:
    """Real edge-tts synthesis + decode, at whatever rate edge-tts actually
    streams (24 kHz)."""
    speaker = Speaker(CONFIG)
    mp3 = asyncio.run(speaker._synthesise(text))
    return speaker._decode_mp3(mp3)


def resample(pcm: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """PyAV resample. av is already a hard dependency (it decodes edge-tts's
    mp3), so this adds nothing new — just reuses it for the other direction."""
    if from_rate == to_rate:
        return pcm.astype(np.float32)
    import av

    frame = av.AudioFrame.from_ndarray(
        pcm.reshape(1, -1).astype(np.float32), format="flt", layout="mono"
    )
    frame.sample_rate = from_rate
    resampler = av.AudioResampler(format="flt", layout="mono", rate=to_rate)
    frames = list(resampler.resample(frame) or [])
    frames += list(resampler.resample(None) or [])  # flush any buffered tail
    arrays = [f.to_ndarray().reshape(-1) for f in frames if f is not None]
    return np.concatenate(arrays) if arrays else np.zeros(0, dtype=np.float32)


def synth_16k(text: str) -> np.ndarray:
    """Synthesise and resample straight to the 16 kHz Jarvis actually runs on."""
    pcm, rate = synth_24k(text)
    return resample(pcm, rate, SAMPLE_RATE)


def to_frames(pcm: np.ndarray, frame_size: int = FRAME) -> Iterator[np.ndarray]:
    """Chunk audio into VAD/wake-word-sized frames, zero-padding the tail."""
    for start in range(0, len(pcm), frame_size):
        chunk = pcm[start : start + frame_size]
        if len(chunk) < frame_size:
            chunk = np.pad(chunk, (0, frame_size - len(chunk)))
        yield chunk.astype(np.float32)


def edit_distance_ratio(a: str, b: str) -> float:
    """Normalised Levenshtein distance in [0, 1]. 0 = identical."""
    a, b = a.strip().lower(), b.strip().lower()
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1] / max(len(a), len(b))


# ----------------------------------------------------------------------- checks
def check_mic(seconds: float = 2.0) -> dict:
    """Capture real audio from the configured device. Proves the device opens
    and Windows mic permission is granted — the one failure mode here that
    genuinely needs O'ktam (handoff §10: OS-level permission denial is a
    stop-and-ask, not a thing to script around)."""
    mic = Microphone(CONFIG)
    frames: list[np.ndarray] = []
    try:
        with mic:
            deadline = time.monotonic() + seconds
            for frame in mic.frames(timeout=1.0):
                frames.append(frame)
                if time.monotonic() >= deadline:
                    break
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if not frames:
        return {"ok": False, "error": "no frames captured — no input device?"}
    audio = np.concatenate(frames)
    rms = float(np.sqrt(np.mean(np.square(audio))))
    all_zero = bool(np.all(audio == 0.0))
    return {
        "ok": (not all_zero) and rms > 1e-6,
        "rms": rms,
        "all_zero": all_zero,
        "frames_captured": len(frames),
        "samples": len(audio),
    }


def check_tts(text: str = "This is a Jarvis self test.") -> dict:
    """Real synthesis + decode + playback. Non-silent PCM at the expected
    rate, and the output stream accepts the frames."""
    try:
        pcm, rate = synth_24k(text)
    except Exception as exc:
        return {"ok": False, "error": f"synthesis failed: {type(exc).__name__}: {exc}"}

    non_silent = len(pcm) > 0 and float(np.max(np.abs(pcm))) > 0.01
    played, play_error = False, None
    try:
        played = Speaker(CONFIG)._play(pcm, rate)
    except Exception as exc:
        play_error = f"{type(exc).__name__}: {exc}"

    return {
        "ok": non_silent and rate > 0 and played,
        "sample_rate": rate,
        "samples": len(pcm),
        "peak_amplitude": float(np.max(np.abs(pcm))) if len(pcm) else 0.0,
        "played": played,
        "play_error": play_error,
    }


def check_wake_word() -> dict:
    """True-positive / true-negative test with no human: 'hey jarvis' must
    fire, an unrelated sentence must not. Fresh WakeWord instance per case so
    the cooldown from one case can't mask the other's real score."""
    positive_audio = synth_16k("hey jarvis")
    negative_audio = synth_16k("what's the weather like")

    wake_pos = WakeWord(CONFIG)
    wake_pos.load()
    fired = False
    best_positive_score = 0.0
    for frame in to_frames(positive_audio):
        if wake_pos.feed(frame):
            fired = True
        best_positive_score = max(best_positive_score, wake_pos.last_score)

    wake_neg = WakeWord(CONFIG)
    wake_neg.load()
    false_fired = False
    best_negative_score = 0.0
    for frame in to_frames(negative_audio):
        if wake_neg.feed(frame):
            false_fired = True
        best_negative_score = max(best_negative_score, wake_neg.last_score)

    return {
        "ok": fired and not false_fired and best_negative_score < wake_pos.threshold,
        "true_positive_fired": fired,
        "true_positive_best_score": best_positive_score,
        "true_negative_fired": false_fired,
        "true_negative_best_score": best_negative_score,
        "threshold": wake_pos.threshold,
    }


def check_vad() -> dict:
    """Speech crosses the threshold, silence doesn't, and UtteranceCollector
    returns audio after the configured silence window."""
    vad = VAD(CONFIG)
    vad.load()

    speech_audio = synth_16k("Testing voice activity detection.")
    speech_probs = [vad.probability(f) for f in to_frames(speech_audio)]
    max_speech_prob = max(speech_probs) if speech_probs else 0.0

    vad.reset()
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)  # 1s of true silence
    silence_probs = [vad.probability(f) for f in to_frames(silence)]
    max_silence_prob = max(silence_probs) if silence_probs else 0.0

    vad.reset()
    collector = UtteranceCollector(CONFIG, vad)
    collected = None
    for f in to_frames(speech_audio):
        collected = collector.feed(f)
        if collected is not None:
            break
    if collected is None:  # still talking at end of clip — feed silence to close it
        n_silence_frames = int(vad.silence_ms / collector.frame_ms) + 3
        for _ in range(n_silence_frames):
            collected = collector.feed(np.zeros(FRAME, dtype=np.float32))
            if collected is not None:
                break

    return {
        "ok": (
            max_speech_prob >= vad.threshold
            and max_silence_prob < vad.threshold
            and collected is not None
            and len(collected) > 0
        ),
        "max_speech_prob": max_speech_prob,
        "max_silence_prob": max_silence_prob,
        "threshold": vad.threshold,
        "collector_returned_samples": 0 if collected is None else len(collected),
    }


def check_stt_roundtrip() -> dict:
    """Synthesise a known sentence, send it to the real Groq API, confirm the
    transcript matches within a normalised edit distance."""
    if not SECRETS.has("groq_api_key"):
        return {"ok": False, "skipped": True, "reason": "GROQ_API_KEY not set"}

    expected = "The quick brown fox jumps over the lazy dog."
    audio = synth_16k(expected)
    transcriber = Transcriber(CONFIG, SECRETS)
    try:
        transcript = transcriber.transcribe(audio)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    ratio = edit_distance_ratio(expected, transcript)
    return {
        "ok": ratio < 0.3 and transcriber.last_engine == "groq",
        "expected": expected,
        "transcript": transcript,
        "edit_distance_ratio": round(ratio, 3),
        "engine_used": transcriber.last_engine,
    }


def check_stt_fallback() -> dict:
    """Force the primary (Groq) path to fail the way a real network outage
    would, and confirm the moonshine offline fallback still produces a
    transcript. The failure is simulated rather than actually cutting network
    access — the thing under test is the fallback chain in
    Transcriber.transcribe(), not O'ktam's Wi-Fi, and simulated failure
    injection is the same approach the handoff asks for below for "a
    simulated timeout"."""
    expected = "Testing the offline fallback."
    audio = synth_16k(expected)
    transcriber = Transcriber(CONFIG, SECRETS)
    transcriber._via_groq = lambda _a: (_ for _ in ()).throw(
        ConnectionError("simulated network outage")
    )
    try:
        transcript = transcriber.transcribe(audio)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": bool(transcript) and transcriber.last_engine == "moonshine",
        "transcript": transcript,
        "engine_used": transcriber.last_engine,
    }


def check_barge_in_latency() -> dict:
    """Measured, not claimed: start a long utterance speaking, inject
    synthesised speech frames the way real barge-in detection sees them
    (same VAD threshold check jarvis/app.py's run() loop uses), and time
    from the start of injection to playback actually stopping."""
    speaker = Speaker(CONFIG)
    long_text = " ".join(
        ["This is a long passage meant to keep Jarvis talking for a while."] * 6
    )
    barge_in_audio = synth_16k("stop talking right now")
    vad = VAD(CONFIG)
    vad.load()
    threshold = float(CONFIG.get_path("conversation.barge_in_threshold", 0.6))

    def speak() -> None:
        speaker.say(long_text)

    thread = threading.Thread(target=speak, daemon=True)
    thread.start()

    start_deadline = time.monotonic() + 15.0
    while not speaker.speaking and time.monotonic() < start_deadline:
        time.sleep(0.01)
    if not speaker.speaking:
        speaker.stop()
        thread.join(timeout=5)
        return {"ok": False, "error": "playback never started within 15s"}

    t0 = time.perf_counter()
    detected_at = None
    for frame in to_frames(barge_in_audio):
        if vad.probability(frame) >= threshold:
            detected_at = time.perf_counter()
            speaker.stop()
            break

    if detected_at is None:
        speaker.stop()  # don't hang the suite even though this case is a failure
        thread.join(timeout=5)
        return {"ok": False, "error": "injected audio never crossed the VAD barge-in threshold"}

    stop_deadline = time.monotonic() + 5.0
    while speaker.speaking and time.monotonic() < stop_deadline:
        time.sleep(0.005)
    t1 = time.perf_counter()
    thread.join(timeout=5)

    return {
        "ok": not speaker.speaking,
        "injection_to_stop_ms": round((t1 - t0) * 1000, 1),
        "injection_to_vad_detection_ms": round((detected_at - t0) * 1000, 1),
        "measured": True,
    }


def check_failure_paths() -> dict:
    """Empty audio, pure silence, a 30s utterance, an invalid Groq key, a
    simulated timeout, no network — Jarvis must stay alive through all of
    them: either a clean result, or the controlled RuntimeError
    Transcriber.transcribe() already raises when every engine fails. Never
    an unhandled crash."""
    cases: dict[str, dict] = {}

    try:
        out = Transcriber(CONFIG, SECRETS).transcribe(np.zeros(0, dtype=np.float32))
        cases["empty_audio"] = {"ok": out == "", "returned": out}
    except Exception as exc:
        cases["empty_audio"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        out = Transcriber(CONFIG, SECRETS).transcribe(
            np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
        )
        cases["pure_silence"] = {"ok": isinstance(out, str), "returned": out}
    except Exception as exc:
        cases["pure_silence"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        vad = VAD(CONFIG)
        vad.load()
        collector = UtteranceCollector(CONFIG, vad)
        speech_frames = list(to_frames(synth_16k("This keeps going and going and going.")))
        max_frames = int((collector.max_s + 5) * 1000 / collector.frame_ms)
        collected, frames_fed = None, 0
        while frames_fed < max_frames:
            collected = collector.feed(speech_frames[frames_fed % len(speech_frames)])
            frames_fed += 1
            if collected is not None:
                break
        cases["thirty_second_utterance"] = {
            "ok": collected is not None,
            "closed_after_s": round(frames_fed * collector.frame_ms / 1000, 1),
            "hit_hard_cap": frames_fed * collector.frame_ms / 1000 >= collector.max_s,
        }
    except Exception as exc:
        cases["thirty_second_utterance"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        bad_secrets = Secrets(
            groq_api_key="gsk_invalid00000000000000000000000000",
            gemini_api_key=SECRETS.gemini_api_key,
            anthropic_api_key=SECRETS.anthropic_api_key,
            telegram_bot_token=SECRETS.telegram_bot_token,
            telegram_api_id=SECRETS.telegram_api_id,
            telegram_api_hash=SECRETS.telegram_api_hash,
            github_token=SECRETS.github_token,
            notion_token=SECRETS.notion_token,
        )
        t = Transcriber(CONFIG, bad_secrets)
        out = t.transcribe(synth_16k("invalid key test"))
        cases["invalid_groq_key"] = {"ok": bool(out), "engine_used": t.last_engine}
    except Exception as exc:
        cases["invalid_groq_key"] = {
            "ok": isinstance(exc, RuntimeError),  # controlled failure = stayed alive
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        t = Transcriber(CONFIG, SECRETS)
        t._via_groq = lambda _a: (_ for _ in ()).throw(TimeoutError("simulated timeout"))
        out = t.transcribe(synth_16k("timeout test"))
        cases["simulated_timeout"] = {"ok": bool(out), "engine_used": t.last_engine}
    except Exception as exc:
        cases["simulated_timeout"] = {
            "ok": isinstance(exc, RuntimeError),
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        t = Transcriber(CONFIG, SECRETS)
        t._via_groq = lambda _a: (_ for _ in ()).throw(ConnectionError("no network"))
        t._via_moonshine = lambda _a: (_ for _ in ()).throw(ConnectionError("no network"))
        out = t.transcribe(synth_16k("no network test"))
        cases["no_network"] = {"ok": False, "unexpected_return": out}
    except RuntimeError as exc:
        cases["no_network"] = {"ok": True, "controlled_error": str(exc)}
    except Exception as exc:
        cases["no_network"] = {"ok": False, "error": f"{type(exc).__name__}: {exc} (uncontrolled)"}

    return {"ok": all(c.get("ok") for c in cases.values()), "cases": cases}


CHECKS = [
    ("mic", check_mic),
    ("tts", check_tts),
    ("wake_word", check_wake_word),
    ("vad", check_vad),
    ("stt_roundtrip", check_stt_roundtrip),
    ("stt_fallback", check_stt_fallback),
    ("barge_in_latency", check_barge_in_latency),
    ("failure_paths", check_failure_paths),
]


def main() -> int:
    print("\n=== Jarvis voice pipeline self-test (handoff §5) ===\n")
    problems = 0
    for name, fn in CHECKS:
        try:
            result = fn()
        except Exception as exc:  # a check itself must not kill the report
            result = {"ok": False, "error": f"check crashed: {type(exc).__name__}: {exc}"}
        mark = "[ok]" if result.get("ok") else ("[--]" if result.get("skipped") else "[XX]")
        if not result.get("ok") and not result.get("skipped"):
            problems += 1
        print(f"{mark} {name}")
        for key, value in result.items():
            if key in ("ok", "skipped"):
                continue
            print(f"      {key}: {value}")
        print()

    print(
        "All checks passed."
        if problems == 0
        else f"{problems} check(s) failed — see above."
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
