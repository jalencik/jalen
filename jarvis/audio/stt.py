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
        self.groq_timeout_s = float(cfg.get_path("stt.groq_timeout_s", 8))
        self.groq_keepalive_s = float(cfg.get_path("stt.groq_keepalive_s", 300))
        self.groq_max_retries = int(cfg.get_path("stt.groq_max_retries", 1))
        self._groq = None
        self._groq_http = None
        self._moonshine = None
        self.last_engine = ""
        # Why the primary was skipped, when it was. Empty on a clean
        # Groq run. See the note in transcribe().
        self.last_fallback_reason = ""

    # ------------------------------------------------------------------ groq
    def _groq_client(self):
        """
        One client, and one CONNECTION, for the life of the session.

        The client was always reused. The connection underneath it was not,
        and nothing in this file said so. From the installed package:

            groq/_constants.py:11
            DEFAULT_CONNECTION_LIMITS = httpx.Limits(
                max_connections=100, max_keepalive_connections=20)

        keepalive_expiry is absent, so httpx's own default of 5.0 seconds
        applies and a pooled connection idle longer than that is closed.
        Voice turns are never five seconds apart - the wait is seconds,
        Jalen then talks for a median of 14.8, and he has to think of the
        next thing to say. So the pool was empty on essentially every turn.

        MEASURED against api.groq.com, six samples, no API call and no
        quota: TCP connect 142ms and TLS 159ms at the median, 301ms
        together, worst 951ms. DNS is not the cost - 231ms cold, 1ms warm.
        That is what every turn was paying to rebuild something it already
        had.

        AND THE RETRIES, which are the other half of the same story.
        groq.Groq defaults max_retries to 2, and stt.groq_timeout_s is a
        PER-ATTEMPT read timeout - so the eight seconds documented below as
        the bound that "MAKES the fallback real" was really three attempts
        plus backoff, about thirty seconds, against the 36-second stall it
        was written to fix. One retry, not two, and not zero: a connection
        held for minutes can be closed at the far end, and with no retry at
        all that first failed write is a silent drop to the offline model -
        a worse outcome than the 301ms being saved.
        """
        if self._groq is None:
            import httpx
            from groq import Groq

            self._groq_http = httpx.Client(
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                    keepalive_expiry=self.groq_keepalive_s,
                ),
            )
            self._groq = Groq(
                api_key=self.secrets.require("groq_api_key"),
                max_retries=self.groq_max_retries,
                http_client=self._groq_http,
            )
        return self._groq

    def close(self) -> None:
        """
        Release the connection pool. Idempotent.

        Called from Jalen.shutdown, where every teardown step runs in its
        own try/except - so a second call must not be the thing that
        raises. The client is dropped with it, because a Groq client whose
        transport is closed fails every later call and would send every
        remaining turn to the offline model in silence.
        """
        http, self._groq_http, self._groq = self._groq_http, None, None
        if http is not None:
            try:
                http.close()
            except Exception:  # noqa: BLE001
                pass

    def _via_groq(self, audio: np.ndarray) -> str:
        """
        Groq, with a BOUND on how long it may think about it.

        The SDK's default read timeout is 60 seconds. That is not a timeout,
        it is an outage: the local fallback below exists precisely for a
        Groq that is not answering, and it never gets a turn because the
        call it is supposed to rescue has not returned yet. Measured in his
        log: transcripts arriving 36 seconds after he stopped speaking, in
        total silence, with a perfectly good offline model sat idle.

        The timeout is what MAKES the fallback real. Without it the fallback
        is a comment.
        """
        client = self._groq_client()
        wav = _to_wav_bytes(audio, self.sample_rate)
        result = client.audio.transcriptions.create(
            file=("utterance.wav", wav, "audio/wav"),
            model=self.groq_model,
            language=self.language,
            response_format="text",
            temperature=0.0,
            timeout=self.groq_timeout_s,
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

    # -------------------------------------------------------------- warmup
    def warmup(self) -> None:
        """
        Pay the Groq client's construction + DNS + TLS cost up front. Measured
        cold-vs-warm on this machine: 3988ms -> ~330ms, and that ~3.7s was
        landing on the user's very first spoken command.

        Deliberately does not send a transcription request: building the
        client and opening the connection is what's slow, and a real request
        would spend quota to save nothing extra.
        """
        client = self._groq_client()
        try:
            # Cheapest authenticated round-trip available — completes the TLS
            # handshake and connection-pool setup that the first real call
            # would otherwise pay for.
            client.models.list()
        except Exception:
            # Offline, or the endpoint changed. The client object is built
            # either way, which is most of the win; the real call will report
            # any genuine problem properly.
            pass

    # -------------------------------------------------------------- dispatch
    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or len(audio) < self.sample_rate * 0.2:
            return ""

        order = [self.primary] + ([self.fallback] if self.fallback != self.primary else [])
        errors: list[str] = []
        self.last_fallback_reason = ""
        for engine in order:
            try:
                text = self._via_groq(audio) if engine == "groq" else self._via_moonshine(audio)
                if text:
                    self.last_engine = engine
                    # A fallback that WORKS is the dangerous kind of
                    # failure: the transcript is fine, so nothing looks
                    # wrong, and the fact that the primary is down never
                    # reaches anyone. Groq was observed returning
                    # intermittent 403s ("Access denied. Please check your
                    # network settings") on roughly two calls in five,
                    # silently demoting every one of those turns to the
                    # local tiny model — lower accuracy on real speech, and
                    # RAM on a machine that has none spare. It had been
                    # happening invisibly, and the audit log this project
                    # is diagnosed from had no record of it.
                    #
                    # Recording the reason does not fix the network. It
                    # makes the problem countable, which is the difference
                    # between "STT feels worse lately" and a line in the
                    # log saying how often and why.
                    if engine != self.primary and errors:
                        self.last_fallback_reason = errors[0]
                    return text
            except Exception as exc:  # network down, quota, model missing
                errors.append(f"{engine}: {type(exc).__name__}: {exc}")
                continue
        if errors:
            raise RuntimeError("transcription failed — " + " | ".join(errors))
        return ""
