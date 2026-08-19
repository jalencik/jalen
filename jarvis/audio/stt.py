"""
Speech to text.

Primary: Groq whisper-large-v3-turbo. Free tier, and fast enough that the
round-trip is invisible — typically faster than running tiny locally, and it
costs you zero RAM, which on 8 GB is the number that actually matters.

Fallback: moonshine-voice, ~34 MB resident, runs offline. Used when the network
is down or Groq errors (spec B16 "nice to have as a fallback").
"""
from __future__ import annotations

import io
import wave

import numpy as np


def _to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


class Transcriber:
    def __init__(self, cfg, secrets) -> None:
        self.cfg = cfg
        self.secrets = secrets
        self.primary = cfg.get_path("stt.primary", "groq")
        self.fallback = cfg.get_path("stt.fallback", "moonshine")
        self.language = cfg.get_path("stt.language", "en")
        self.groq_model = cfg.get_path("stt.groq_model", "whisper-large-v3-turbo")
        self.sample_rate = int(cfg.get_path("audio.sample_rate", 16000))
        self._groq = None
        self._moonshine = None
        self.last_engine = ""

    # ------------------------------------------------------------------ groq
    def _groq_client(self):
        if self._groq is None:
            from groq import Groq

            self._groq = Groq(api_key=self.secrets.require("groq_api_key"))
        return self._groq

    def _via_groq(self, audio: np.ndarray) -> str:
        client = self._groq_client()
        wav = _to_wav_bytes(audio, self.sample_rate)
        result = client.audio.transcriptions.create(
            file=("utterance.wav", wav, "audio/wav"),
            model=self.groq_model,
            language=self.language,
            response_format="text",
            temperature=0.0,
        )
        return (result if isinstance(result, str) else getattr(result, "text", "")).strip()

    # ------------------------------------------------------------- moonshine
    def _moonshine_model(self):
        if self._moonshine is None:
            from moonshine_voice import (
                ModelArch,
                Transcriber as MoonTranscriber,
                get_model_for_language,
            )

            # Transcriber(model_path, model_arch) — model_path is a required
            # directory of .ort weights, not something you can skip. The
            # original code passed the ModelArch enum as the first (path)
            # argument instead, which the C API stringified into the literal
            # path "2" (ModelArch.TINY_STREAMING.value) and then failed to
            # find. get_model_for_language() downloads (once, then caches
            # via moonshine_voice's own cache dir) and resolves the real path.
            name = self.cfg.get_path("stt.moonshine_model", "TINY_STREAMING")
            wanted_arch = getattr(ModelArch, name)
            model_path, resolved_arch = get_model_for_language(self.language, wanted_arch)
            self._moonshine = MoonTranscriber(model_path, resolved_arch)
        return self._moonshine

    def _via_moonshine(self, audio: np.ndarray) -> str:
        model = self._moonshine_model()
        # Transcriber has no .transcribe() — the one-shot (non-streaming) API
        # is transcribe_without_streaming(list[float], sample_rate), returning
        # a Transcript whose .lines is a list of TranscriptLine, each with
        # its own .text.
        result = model.transcribe_without_streaming(
            audio.astype(np.float32).tolist(), sample_rate=self.sample_rate
        )
        return " ".join(line.text for line in result.lines).strip()

    # -------------------------------------------------------------- dispatch
    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or len(audio) < self.sample_rate * 0.2:
            return ""

        order = [self.primary] + ([self.fallback] if self.fallback != self.primary else [])
        errors: list[str] = []
        for engine in order:
            try:
                text = self._via_groq(audio) if engine == "groq" else self._via_moonshine(audio)
                if text:
                    self.last_engine = engine
                    return text
            except Exception as exc:  # network down, quota, model missing
                errors.append(f"{engine}: {type(exc).__name__}: {exc}")
                continue
        if errors:
            raise RuntimeError("transcription failed — " + " | ".join(errors))
        return ""
