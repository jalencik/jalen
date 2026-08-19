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
    """Accumulates frames until you stop talking, then hands back the audio."""

    def __init__(self, cfg, vad: VAD) -> None:
        self.vad = vad
        self.sample_rate = int(cfg.get_path("audio.sample_rate", 16000))
        self.frame_ms = int(cfg.get_path("audio.frame_ms", 32))
        self.max_s = float(cfg.get_path("vad.max_utterance_s", 30))
        self._reset()

    def _reset(self) -> None:
        self._buf: list[np.ndarray] = []
        self._silence_ms = 0
        self._speech_ms = 0
        self._started = False

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        """
        Returns None while you're still talking; returns the full utterance as a
        float32 array the moment you've been quiet for `silence_ms`.
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
            and self._silence_ms >= self.vad.silence_ms
            and self._speech_ms >= self.vad.min_speech_ms
        )
        too_long = total_ms >= self.max_s * 1000
        gave_up = not self._started and total_ms > 4000  # wake word but no speech

        if done_talking or too_long:
            audio = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
            self._reset()
            self.vad.reset()
            return audio
        if gave_up:
            self._reset()
            self.vad.reset()
            return np.zeros(0, dtype=np.float32)
        return None
