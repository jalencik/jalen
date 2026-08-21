"""
Wake word: "Hey Jalen".

openWakeWord ships a PRETRAINED hey_jarvis model, which is the entire reason
the wake word in the spec is worth keeping as-is — no training, no cost, ~3% of
one core.

Two hard-won details, both of which will waste your evening if you miss them:

1. `Model()` with no arguments defaults to TFLite, and tflite-runtime has never
   had a Windows wheel. You MUST pass inference_framework="onnx".
2. `pip install openwakeword` on Python 3.12+ silently resolves to 0.4.0, which
   has a completely different API. Pin ==0.6.0 (requirements.txt does).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "models"


class WakeWord:
    def __init__(self, cfg) -> None:
        self.enabled = bool(cfg.get_path("wake.enabled", True))
        self.model_name = cfg.get_path("wake.model", "hey_jarvis_v0.1")
        self.threshold = float(cfg.get_path("wake.threshold", 0.55))
        self.cooldown_s = float(cfg.get_path("wake.cooldown_s", 1.5))
        self._last_fire = 0.0
        self._model = None
        self.last_score = 0.0

    def load(self) -> None:
        if not self.enabled or self._model is not None:
            return
        try:
            from openwakeword.model import Model
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "openwakeword missing. Run: pip install openwakeword==0.6.0 onnxruntime"
            ) from exc

        local = MODELS_DIR / f"{self.model_name}.onnx"
        kwargs = {"inference_framework": "onnx"}  # never omit this on Windows
        if local.exists():
            kwargs["wakeword_models"] = [str(local)]
        else:
            kwargs["wakeword_models"] = [self.model_name]
        self._model = Model(**kwargs)

    def reset(self) -> None:
        if self._model is not None:
            try:
                self._model.reset()
            except Exception:
                pass

    def feed(self, frame: np.ndarray) -> bool:
        """Feed one frame. Returns True the moment the wake word fires."""
        if not self.enabled:
            return False
        if self._model is None:
            self.load()

        pcm = (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16)
        scores = self._model.predict(pcm)
        self.last_score = max(scores.values()) if scores else 0.0

        if self.last_score < self.threshold:
            return False
        now = time.monotonic()
        if now - self._last_fire < self.cooldown_s:
            return False
        self._last_fire = now
        self.reset()
        return True
