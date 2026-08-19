"""
Text to speech with barge-in (spec B13/B14, C20).

edge-tts: free, no key, high quality, streams. Male professional voice per A2.

Barge-in is the part that makes it feel alive. We play audio in small chunks and
check an interrupt flag between chunks, so `stop()` cuts him off within ~50 ms
instead of after the sentence.

Two edge-tts realities worth knowing:
  - a sudden 403 usually means Microsoft rotated its client token: upgrade
    edge-tts, don't assume the service died.
  - it occasionally returns no audio for no reason; we retry.
"""
from __future__ import annotations

import asyncio
import io
import re
import threading

import numpy as np

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])$|\n{2,}")


def split_sentences(text: str, max_len: int = 240) -> list[str]:
    """Split so we can start speaking sentence 1 before sentence 3 exists."""
    parts = [p.strip() for p in _SENTENCE_END.split(text or "") if p and p.strip()]
    out: list[str] = []
    for part in parts:
        while len(part) > max_len:
            cut = part.rfind(",", 0, max_len)
            cut = cut if cut > 60 else max_len
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            out.append(part)
    return out


def clean_for_speech(text: str) -> str:
    """Strip anything that sounds absurd read aloud."""
    text = re.sub(r"```.*?```", " (code is on screen) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " a link ", text)
    text = re.sub(r"^[\s>#*\-–—•]+", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"[*_#|]", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


class Speaker:
    def __init__(self, cfg) -> None:
        self.voice = cfg.get_path("tts.voice", "en-US-AndrewNeural")
        self.rate = cfg.get_path("tts.rate", "+18%")
        self.volume = cfg.get_path("tts.volume", "+0%")
        self.pitch = cfg.get_path("tts.pitch", "+0Hz")
        self.retries = int(cfg.get_path("tts.retry_attempts", 3))
        self.max_spoken = int(cfg.get_path("tts.max_spoken_chars", 700))
        self.sentence_streaming = bool(cfg.get_path("tts.sentence_streaming", True))
        self._interrupt = threading.Event()
        self._speaking = threading.Event()
        self._say_lock = threading.Lock()
        self.on_state = lambda state: None  # set by the orchestrator to drive the orb

    # --------------------------------------------------------------- control
    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def stop(self) -> None:
        """Barge-in. Cuts playback within about one chunk."""
        self._interrupt.set()

    # ----------------------------------------------------------------- synth
    async def _synthesise(self, text: str) -> bytes:
        import edge_tts

        last: Exception | None = None
        for _ in range(max(1, self.retries)):
            try:
                comm = edge_tts.Communicate(
                    text,
                    voice=self.voice,
                    rate=self.rate,
                    volume=self.volume,
                    pitch=self.pitch,
                )
                buf = bytearray()
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        buf.extend(chunk["data"])
                if buf:
                    return bytes(buf)
                last = RuntimeError("edge-tts returned no audio")
            except Exception as exc:
                last = exc
                await asyncio.sleep(0.4)
        raise RuntimeError(f"TTS failed: {last}")

    @staticmethod
    def _decode_mp3(data: bytes) -> tuple[np.ndarray, int]:
        """edge-tts gives 24 kHz mono MP3; decode to float32 PCM."""
        try:
            import av  # bundled with faster-whisper, no system ffmpeg needed
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Need PyAV to decode audio: pip install av") from exc

        container = av.open(io.BytesIO(data))
        frames = []
        rate = 24000
        for frame in container.decode(audio=0):
            rate = frame.sample_rate
            arr = frame.to_ndarray()
            frames.append(arr.mean(axis=0) if arr.ndim > 1 else arr)
        container.close()
        if not frames:
            return np.zeros(0, dtype=np.float32), rate
        pcm = np.concatenate(frames).astype(np.float32)
        if np.issubdtype(pcm.dtype, np.integer) or np.max(np.abs(pcm)) > 1.5:
            pcm = pcm / 32768.0
        return pcm, rate

    # ------------------------------------------------------------------ play
    def _play(self, pcm: np.ndarray, rate: int) -> bool:
        """Play, checking for interrupt between chunks. False = interrupted."""
        import sounddevice as sd

        chunk = max(256, rate // 20)  # ~50 ms
        stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32")
        stream.start()
        try:
            for i in range(0, len(pcm), chunk):
                if self._interrupt.is_set():
                    return False
                stream.write(np.ascontiguousarray(pcm[i : i + chunk]))
            return True
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def warmup(self) -> None:
        """
        Pay edge-tts's first-connection cost up front (measured 4562ms cold
        vs ~2000ms warm), and pre-import PyAV so the first decode doesn't
        also pay an import. Synthesises one short throwaway word and never
        plays it.
        """
        try:
            import av  # noqa: F401  — import cost paid here, not mid-reply
        except ImportError:
            pass
        asyncio.run(self._synthesise("ready"))

    def say(self, text: str) -> bool:
        """
        Speak text. Blocks until finished or interrupted.
        Returns False if barge-in cut it short.

        Serialized on _say_lock: two turns finishing at once used to call
        this concurrently, and since _interrupt/_speaking are instance
        state, one call's cleanup would clear the other's flags mid-playback
        while both wrote to the output device — audible as overlapping,
        garbled speech.
        """
        text = clean_for_speech(text)
        if not text:
            return True

        with self._say_lock:
            self._interrupt.clear()
            self._speaking.set()
            self.on_state("speaking")
            try:
                sentences = split_sentences(text)
                if self.sentence_streaming and len(sentences) > 1:
                    return self._say_streaming(sentences)
                return self._say_sequential(sentences)
            finally:
                self._speaking.clear()
                self._interrupt.clear()
                self.on_state("idle")

    def _say_sequential(self, sentences: list[str]) -> bool:
        for sentence in sentences:
            if self._interrupt.is_set():
                return False
            try:
                pcm, rate = self._decode_mp3(asyncio.run(self._synthesise(sentence)))
            except Exception:
                continue  # one bad sentence shouldn't kill the whole reply
            if not self._play(pcm, rate):
                return False
        return True

    def _say_streaming(self, sentences: list[str]) -> bool:
        """
        Synthesise sentence N+1 while sentence N is still playing.

        This is what `tts.sentence_streaming: true` in jarvis.yaml always
        claimed to do and never did — synthesis and playback were strictly
        serial, so every sentence boundary cost a full network round-trip of
        silence (~2s each, measured warm). Overlapping them means only the
        FIRST sentence's synthesis is ever on the critical path; the rest is
        hidden behind audio that's already playing.
        """
        import concurrent.futures as cf

        with cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-synth") as pool:
            pending = pool.submit(self._render, sentences[0])
            for index in range(len(sentences)):
                if self._interrupt.is_set():
                    return False
                current, pending = pending, None
                # Kick off the NEXT synthesis before playing this one, so the
                # network round-trip overlaps playback instead of following it.
                if index + 1 < len(sentences):
                    pending = pool.submit(self._render, sentences[index + 1])
                try:
                    rendered = current.result()
                except Exception:
                    rendered = None
                if rendered is None:
                    continue
                pcm, rate = rendered
                if not self._play(pcm, rate):
                    return False
            return True

    def _render(self, sentence: str) -> tuple[np.ndarray, int] | None:
        """Synthesise + decode one sentence. Runs on the synth worker thread."""
        try:
            return self._decode_mp3(asyncio.run(self._synthesise(sentence)))
        except Exception:
            return None

    def summarise_if_long(self, text: str) -> tuple[str, str | None]:
        """
        Spec B15: long content is summarised aloud and shown in full on screen.
        Returns (what_to_speak, what_to_show_or_None).
        """
        if len(text) <= self.max_spoken:
            return text, None
        head = text[: self.max_spoken].rsplit(".", 1)[0]
        spoken = f"{head}. That's the short version — the full text is on screen."
        return spoken, text
