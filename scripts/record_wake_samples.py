"""
Teach the wake word YOUR voice.  (HANDOFF item 3)

    .venv\\Scripts\\python.exe scripts\\record_wake_samples.py

THE PROBLEM THIS SOLVES
-----------------------
models/hey_jalen.onnx was trained on 2,160 synthetic positives across 45
edge-tts voices. On held-out synthetic audio it measures 0.00% false accepts
on ordinary speech and 2.9% missed wake words at threshold 0.7 — good
numbers, and numbers about a voice that is not yours.

Accent is exactly what these models are sensitive to, and the miss rate on
one specific person has never been measured. Thirty real recordings fix that
properly, and this script is the thirty recordings.

Everything downstream already accepts them. train_wake_word.py globs
data/wake_training/positive/*.wav, and its embedding cache is keyed on the
clip count, so new files invalidate it automatically and only the new clips
cost time. Nothing else needs changing:

    .venv\\Scripts\\python.exe scripts\\train_wake_word.py train --augment 2

WHAT IT REFUSES TO SAVE, AND WHY THAT IS THE POINT
--------------------------------------------------
A bad positive is worse than a missing one. A clip that is silent, clipped,
or contains no speech teaches the model that "hey jalen" sounds like a room
tone or a crackle, and it will then fire on room tone or crackle at three in
the morning. Every recording is checked before it is written:

    too quiet      you were off-mic, or the wrong input device is selected
    clipping       too close to the mic; the waveform is squared off and the
                   spectrogram is no longer your voice
    no speech      the Silero VAD Jalen already uses heard nothing
    too short      the phrase was cut off by the fixed window
    too long       you said something else as well

A rejected take is re-recorded, not saved and apologised for later. This is
the same failure this project keeps producing in other places — reporting
success for something that did not work — and the cheapest place to stop it
is before the file exists.

PRIVACY
-------
Audio is written to data/wake_training/positive/ and nowhere else. It is
never uploaded, never sent to Groq or anyone else, and never enters the
audit log. Delete the files when you are done training if you would rather
they did not exist; the model keeps what it learned.
"""
from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

POS_DIR = ROOT / "data" / "wake_training" / "positive"

SAMPLE_RATE = 16000
CLIP_SECONDS = 2.0
CLIP_SAMPLES = int(SAMPLE_RATE * CLIP_SECONDS)

# The three phrasings train_wake_word.py treats as positives. Recorded in
# rotation rather than thirty of the first one: the model already knows what
# "Hey Jalen" sounds like from 45 synthetic voices, and what it is missing is
# how YOUR mouth moves through all three.
PHRASES = ["Hey Jalen", "Hi Jalen", "OK Jalen"]

# STRESS. The thing he reported: "many people give udareniya not to a in
# jalen, but they give it to e".
#
# The ROUTER handles this at the text layer — jarvis/brain/router.py matches
# the name by shape, and every stress spelling is covered there. The WAKE
# WORD cannot be fixed that way: it is an acoustic model, it never sees text,
# and "JA-len" and "ja-LEN" are genuinely different sounds. A model trained
# only on the first will miss the second, and no threshold recovers it.
#
# The synthetic corpus was 45 edge-tts voices, all of which say it the
# English way. So the recorder deliberately asks for BOTH stress patterns,
# alternating, and says which one out loud each time. That is the only way
# the model learns his.
STRESS_CUES = [
    "stress the FIRST syllable  -  JA-len",
    "stress the SECOND syllable -  ja-LEN",
]

# Below this peak (int16) the microphone did not really hear you. Measured
# against real speech at a normal desk distance, which peaks around 3000-8000.
MIN_PEAK = 900
# Above this, the input is clipping and the waveform is squared off.
CLIP_PEAK = 32000
# A wake word is roughly 0.6-1.2s. Outside this the take is not the phrase.
MIN_SPEECH_S = 0.35
MAX_SPEECH_S = 1.60

# Recorded a little longer than the training clip so a late start is not
# truncated; the speech is then cut out and re-centred before saving.
RECORD_SECONDS = 2.6


def _prefix() -> str:
    """
    Real recordings are named apart from the synthetic ones on purpose.

    The synthetic corpus is named after the edge-tts voice that produced it
    (en-AU-NatashaNeural_00.wav). Real takes get 'real_' so that a later
    "delete the synthetic positives and retrain on just my voice" is one
    glob, and so nobody has to guess which files came from a person.
    """
    return "real_"


def existing_count() -> int:
    return len(list(POS_DIR.glob(f"{_prefix()}*.wav")))


# --------------------------------------------------------------- validation
class Rejected(Exception):
    """A take that must not be written. Carries the reason, for the user."""


def _speech_bounds(pcm: np.ndarray) -> tuple[int, int]:
    """
    First and last sample of actual speech, by short-time energy.

    Deliberately not the VAD: this runs on every take and the VAD costs a
    model load. Energy gating is enough to TRIM, and check_take() below still
    asks the real VAD whether there was speech at all.
    """
    window = int(SAMPLE_RATE * 0.02)
    if len(pcm) < window:
        return 0, len(pcm)
    frames = len(pcm) // window
    energy = np.abs(pcm[: frames * window].reshape(frames, window)).mean(axis=1)
    floor = np.percentile(energy, 20)
    threshold = max(floor * 3.0, MIN_PEAK * 0.08)
    loud = np.flatnonzero(energy > threshold)
    if len(loud) == 0:
        return 0, len(pcm)
    return int(loud[0] * window), int(min(len(pcm), (loud[-1] + 1) * window))


def check_take(pcm: np.ndarray, vad=None) -> np.ndarray:
    """
    Validate and trim one take. Returns the clip to save, or raises Rejected.

    Order matters: the cheap objective checks come first so that the common
    failures (wrong input device, sitting too far away) are named precisely
    rather than being reported as the vaguer "I heard no speech".
    """
    peak = float(np.abs(pcm).max()) if len(pcm) else 0.0
    if peak < MIN_PEAK:
        raise Rejected(
            f"too quiet (peak {peak:.0f}). Move closer, or check the input "
            "device with: python run.py --check"
        )
    if peak >= CLIP_PEAK:
        raise Rejected(
            f"clipping (peak {peak:.0f}). Back off from the mic a little — a "
            "squared-off waveform is not your voice any more."
        )

    start, end = _speech_bounds(pcm)
    duration = (end - start) / SAMPLE_RATE
    if duration < MIN_SPEECH_S:
        raise Rejected(f"too short ({duration:.2f}s). Say the whole phrase.")
    if duration > MAX_SPEECH_S:
        raise Rejected(
            f"too long ({duration:.2f}s). Just the wake phrase, nothing after it."
        )

    if vad is not None and not _has_speech(pcm, vad):
        raise Rejected("I couldn't hear speech in that one. Try again.")

    # Centre the trimmed speech in the fixed clip, with a little room before
    # it. train_wake_word.py re-places it randomly during augmentation, so
    # this only has to be consistent, not clever.
    speech = pcm[start:end][:CLIP_SAMPLES]
    out = np.zeros(CLIP_SAMPLES, dtype=np.float32)
    room = CLIP_SAMPLES - len(speech)
    out[room // 3 : room // 3 + len(speech)] = speech
    return out


def _has_speech(pcm: np.ndarray, vad) -> bool:
    """
    Ask the same Silero VAD the live microphone loop uses.

    Using Jalen's own VAD rather than a second opinion is deliberate: a clip
    this rejects is one the running assistant would also have ignored, so the
    training set stays consistent with what the runtime actually hears.
    """
    frame = 512                                   # what Silero wants at 16 kHz
    audio = (pcm / 32768.0).astype(np.float32)
    hits = 0
    for i in range(0, len(audio) - frame, frame):
        try:
            if vad.probability(audio[i : i + frame]) >= 0.5:
                hits += 1
        except Exception:
            return True        # a broken VAD must not reject a good take
    return hits >= 3


# ------------------------------------------------------------------ capture
def record(seconds: float, device=None) -> np.ndarray:
    import sounddevice as sd

    frames = int(SAMPLE_RATE * seconds)
    buffer = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                    dtype="int16", device=device)
    sd.wait()
    return buffer[:, 0].astype(np.float32)


def write_wav(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(pcm, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        fh.writeframes(data.tobytes())


# --------------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record real samples of you saying the wake word."
    )
    parser.add_argument("-n", "--count", type=int, default=30,
                        help="how many takes to record this session (default 30)")
    parser.add_argument("--device", default=None,
                        help="input device index or name; default is the system default")
    parser.add_argument("--list-devices", action="store_true",
                        help="show input devices and exit")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        import sounddevice as sd
    except ImportError:
        print("sounddevice isn't installed. Run: pip install -r requirements.txt")
        return 1

    if args.list_devices:
        for index, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                print(f"  {index}: {dev['name']}")
        return 0

    device = args.device
    if device is not None and str(device).isdigit():
        device = int(device)

    already = existing_count()
    print("Recording real samples of your voice for the wake word.")
    print(f"  saving to   {POS_DIR}")
    print(f"  on disk     {already} real takes already")
    print(f"  this run    {args.count} more")
    print()
    print("Say the phrase ONCE, normally, the way you actually say it.")
    print("Vary it as you go: closer, further, quieter, faster, turned away.")
    print("That variety is the whole point — thirty identical takes teach")
    print("the model one distance and one volume.")
    print()
    print("Ctrl+C stops any time; everything recorded so far is kept.")
    print()

    vad = None
    try:
        from jarvis.audio.vad import VAD
        from jarvis.config import CONFIG

        vad = VAD(CONFIG)
        vad.load()
    except Exception as exc:
        print(f"(speech check unavailable: {exc}; recording anyway)\n")

    saved = 0
    rejected = 0
    index = already
    try:
        while saved < args.count:
            phrase = PHRASES[index % len(PHRASES)]
            # Alternate the stress, and SAY WHICH. Left to himself he would
            # say it his own way thirty times, and the model would learn one
            # stress pattern again — just his instead of edge-tts's.
            cue = STRESS_CUES[index % len(STRESS_CUES)]
            print(f"[{saved + 1}/{args.count}]  Say:  \"{phrase}\"   ({cue})")
            input("            press Enter, then say it… ")
            print("            recording…", end="", flush=True)
            pcm = record(RECORD_SECONDS, device)
            print("\r            ", end="")

            try:
                clip = check_take(pcm, vad)
            except Rejected as why:
                rejected += 1
                print(f"\r  rejected: {why}\n")
                continue

            name = f"{_prefix()}{phrase.split()[0].lower()}_{index:03d}.wav"
            write_wav(POS_DIR / name, clip)
            saved += 1
            index += 1
            print(f"\r  saved {name}  (peak {np.abs(clip).max():.0f})\n")
            time.sleep(0.15)
    except KeyboardInterrupt:
        print("\n\nStopped.")

    total = existing_count()
    print()
    print(f"{saved} saved this run, {rejected} rejected, {total} real takes in total.")
    if total < 20:
        print(
            f"\nThat is fewer than 20. It will still help, but 30-50 is where "
            f"the miss rate on your own voice actually moves. Run this again "
            f"to add {30 - total} more."
        )
        return 0

    print("\nNow retrain. Embeddings are cached, so only the new clips cost time:")
    print("    .venv\\Scripts\\python.exe scripts\\train_wake_word.py train --augment 2")
    print("\nThe evaluation at the end prints false accepts and misses per")
    print("threshold. If misses on your voice are still high, lower")
    print("wake.threshold in config/jarvis.yaml toward 0.5 before recording more.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
