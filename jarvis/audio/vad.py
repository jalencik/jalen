"""
Voice activity detection — decides when you've finished a sentence, and powers
barge-in (spec C20).

We deliberately do NOT `pip install silero-vad`: that package hard-depends on
torch and torchaudio, which is multiple gigabytes. On a laptop with ~1 GB free
that is not a trade-off, it's a wall. We load the 2 MB ONNX file directly with
onnxruntime instead — same model, 0.3% of one core.

scripts/download_models.py fetches silero_vad.onnx for you.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "models"
MODEL_PATH = MODELS_DIR / "silero_vad.onnx"

WINDOW = 512   # samples @16k — new samples consumed per inference call
CONTEXT = 64   # samples @16k — this onnx export (Silero VAD v5+, opset 16) wants
               # each call's input to be the tail of the PREVIOUS chunk plus the
               # new one: 64 context samples + 512 new = 576 total. Feed it a bare
               # 512-sample window with no continuity and it doesn't error — it
               # just returns near-zero for everything, silently. Confirmed against
               # the reference OnnxWrapper in the silero-vad package's own
               # utils_vad.py (self._context = x[..., -context_size:], concatenated
               # before every session.run). Selftest §5 caught this: real speech
               # was scoring ~0.03 against a 0.5 threshold before this fix, ~0.9+
               # after.


class VAD:
    def __init__(self, cfg) -> None:
        self.threshold = float(cfg.get_path("vad.threshold", 0.5))
        self.sample_rate = int(cfg.get_path("audio.sample_rate", 16000))
        self.silence_ms = int(cfg.get_path("vad.silence_ms", 700))
        self.min_speech_ms = int(cfg.get_path("vad.min_speech_ms", 250))
        self._sess = None
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._sr = np.array(self.sample_rate, dtype=np.int64)
        self._tail = np.zeros(0, dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self.last_prob = 0.0

    def load(self) -> None:
        if self._sess is not None:
            return
        if not MODEL_PATH.exists():
            raise RuntimeError(
                f"{MODEL_PATH.name} not found. Run: python scripts/download_models.py"
            )
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1  # this model is tiny; threads only add overhead
        self._sess = ort.InferenceSession(
            str(MODEL_PATH), sess_options=opts, providers=["CPUExecutionProvider"]
        )

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self.last_prob = 0.0

    def probability(self, frame: np.ndarray) -> float:
        """Speech probability for a frame of any length (buffers internally)."""
        if self._sess is None:
            self.load()
        self._tail = np.concatenate([self._tail, frame.astype(np.float32)])
        prob = self.last_prob
        while len(self._tail) >= WINDOW:
            chunk = self._tail[:WINDOW]
            self._tail = self._tail[WINDOW:]
            windowed = np.concatenate([self._context, chunk]).reshape(1, CONTEXT + WINDOW)
            out, self._state = self._sess.run(
                None, {"input": windowed, "state": self._state, "sr": self._sr}
            )
            self._context = chunk[-CONTEXT:]
            prob = float(out[0][0])
        self.last_prob = prob
        return prob

    def is_speech(self, frame: np.ndarray) -> bool:
        return self.probability(frame) >= self.threshold


class UtteranceCollector:
    """
    Accumulates frames until you stop talking, then hands back the audio.

    ENDPOINTING (why this is not just one silence threshold)
    --------------------------------------------------------
    The delay between "you stop talking" and "Jalen starts working" is a
    fixed tax on EVERY turn, and it used to be the single largest item in
    the budget: 2000ms, more than the STT round-trip and the router put
    together. It was 700ms once, and got raised because 700ms guillotined
    people mid-thought -- "open chrome ... and go to youtube" with a normal
    1.2s pause was captured as just "open chrome".

    Both settings were right about their own failure and wrong about the
    other one, because a silence threshold alone cannot tell "he's finished"
    from "he's thinking". Nothing acoustic distinguishes them. Only the
    WORDS do.

    So there are two thresholds, and the transcript arbitrates:

      fast_silence_ms (default 550)   -> close the utterance, transcribe.
      silence_ms      (default 2000)  -> the patient limit, used only after
                                         the transcript says he isn't done.

    The orchestrator (app.py) transcribes at the fast endpoint and looks at
    how the sentence ENDS. "open chrome" is a complete thought, so it goes
    immediately. "open chrome and" ends on a conjunction -- nobody finishes
    a sentence there -- so it calls resume() and keeps listening on the
    patient threshold, then re-transcribes the whole thing.

    Net effect: the common command pays 550ms instead of 2000ms, and the
    trailing-thought case still gets its full 2s. The 1.45s saving is real
    on every single turn, including ones the router answers without ever
    reaching Claude.
    """

    def __init__(self, cfg, vad: VAD) -> None:
        self.vad = vad
        self.sample_rate = int(cfg.get_path("audio.sample_rate", 16000))
        self.frame_ms = int(cfg.get_path("audio.frame_ms", 32))
        self.max_s = float(cfg.get_path("vad.max_utterance_s", 30))
        self.min_speech_ms = int(cfg.get_path("vad.min_speech_ms", 250))
        # The patient threshold. Read off the VAD so the existing
        # vad.silence_ms key keeps meaning exactly what it always meant.
        self.patient_silence_ms = int(vad.silence_ms)
        self.fast_silence_ms = int(cfg.get_path("vad.fast_silence_ms", 550))
        # Never let a misconfiguration make the fast path the slow one.
        self.fast_silence_ms = min(self.fast_silence_ms, self.patient_silence_ms)
        # How long a trigger that produced no real speech is allowed to hold
        # the microphone before we give up on it.
        self.no_speech_timeout_ms = int(cfg.get_path("vad.no_speech_timeout_ms", 2500))
        self._reset()

    def _reset(self) -> None:
        self._buf: list[np.ndarray] = []
        self._silence_ms = 0
        self._speech_ms = 0
        self._started = False
        self._endpoint_ms = self.fast_silence_ms
        self.was_patient = False

    def resume(self, audio: np.ndarray) -> None:
        """
        Put a closed utterance back into listening, on the patient threshold.

        Called when the transcript of a fast endpoint turned out to be an
        unfinished sentence. The audio already captured is restored so the
        final transcription sees the WHOLE phrase, not just the tail -- one
        Whisper pass over "open chrome and go to youtube" is both cheaper
        and more accurate than stitching two partial transcripts together.
        """
        self._reset()
        self._endpoint_ms = self.patient_silence_ms
        self.was_patient = True
        if audio is not None and len(audio):
            self._buf.append(np.asarray(audio, dtype=np.float32))
            # The restored audio is known speech. Say so, or min_speech_ms
            # would have to be re-earned from scratch and a short trailing
            # clause ("...and youtube") could never close the utterance.
            self._speech_ms = max(self.min_speech_ms, self._speech_ms)
            self._started = True
        self.vad.reset()

    def prime(self, frames: list) -> None:
        """
        Start a new utterance with audio heard just before the window opened.

        The last of `frames` is the one the VAD scored as speech - the sound
        that opened the window - so it counts as speech here too; the ones
        before it are the quiet start of the word, kept as audio only. See
        PREROLL_S in app.py for why: a short answer used to lose its first
        frames and arrive too short to transcribe.
        """
        self._reset()
        kept = [np.asarray(f, dtype=np.float32) for f in (frames or []) if f is not None and len(f)]
        if not kept:
            return
        self._buf.extend(kept)
        self._speech_ms = self.frame_ms
        self._started = True

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        """
        Returns None while you're still talking; returns the full utterance as a
        float32 array the moment you've been quiet for the active threshold.
        """
        speech = self.vad.is_speech(frame)
        self._buf.append(frame)

        if speech:
            self._speech_ms += self.frame_ms
            self._silence_ms = 0
            self._started = True
        elif self._started:
            self._silence_ms += self.frame_ms

        total_ms = len(self._buf) * self.frame_ms
        done_talking = (
            self._started
            and self._silence_ms >= self._endpoint_ms
            and self._speech_ms >= self.min_speech_ms
        )
        too_long = total_ms >= self.max_s * 1000

        # A trigger that never became speech. Two shapes, and the second one
        # used to be a 30-SECOND HANG rather than a give-up:
        #
        #   never started    -- VAD stayed below threshold the whole time.
        #   started, but ... -- ONE 32ms blip (a keystroke, a door, the tail
        #                       of Jalen's own voice bleeding into the mic)
        #                       set _started, leaving _speech_ms at 32ms.
        #                       done_talking needs _speech_ms >= 250, which
        #                       can never now happen, and the old gave_up
        #                       tested `not self._started`, which is now
        #                       False. Neither could fire, so the collector
        #                       held the microphone until max_utterance_s.
        #                       Thirty seconds of a live assistant appearing
        #                       to be dead, from a single click of noise.
        quiet_for = self._silence_ms if self._started else total_ms
        gave_up = (
            self._speech_ms < self.min_speech_ms
            and quiet_for >= self.no_speech_timeout_ms
        )

        if done_talking or too_long:
            audio = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
            was_patient = self.was_patient
            self._reset()
            self.vad.reset()
            self.was_patient = was_patient   # survives the reset; app.py reads it
            return audio
        if gave_up:
            self._reset()
            self.vad.reset()
            return np.zeros(0, dtype=np.float32)
        return None
