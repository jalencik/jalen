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
import concurrent.futures as _cf
import io
import queue
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
        self._cache_lock = threading.Lock()
        self._audio_cache: dict[str, tuple[np.ndarray, int]] = {}
        self.on_state = lambda state: None  # set by the orchestrator to drive the orb
        # Fired the instant audio first reaches the output device. This is
        # the end of the silence the user experiences as "slowness" — see
        # jarvis/timing.py for why that moment, specifically, is the one
        # worth measuring.
        self.on_audio_start = lambda: None
        # The stream currently holding _say_lock, if any. say_now() needs to
        # know, because during a streamed reply that thread is the only one
        # that can speak. See say_now() for the deadlock this prevents.
        self._active_stream: "SpeechStream | None" = None

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
        # The single moment the waiting ends. Both paths into playback —
        # say() and SpeechStream — funnel through here, which is why the
        # hook lives at this level rather than in each of them. Fires once
        # per sentence; TurnTimer.mark() keeps only the first, because only
        # the first is when the silence actually stopped.
        try:
            self.on_audio_start()
        except Exception:
            # Instrumentation must never be able to break speech.
            pass
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

    # Short confirmations Jarvis says constantly. Synthesising "Opening
    # chrome." costs ~1.5s of network round-trip EVERY time — measured, and
    # it is the largest remaining delay on an otherwise sub-second command
    # (the app itself opens ~0.5s after you stop talking). The words never
    # change, so they are rendered once and replayed from memory afterwards.
    CACHEABLE_MAX_CHARS = 90   # confirmations, not paragraphs
    CACHE_MAX_ENTRIES = 40

    _COMMON_PHRASES = (
        "Done.", "Opening.", "Opened.", "Sure.", "Any time.", "Muted.", "Back.",
        "Skipped.", "Back one.", "Locked.", "Minimised.", "Maximised.",
        "Cancelled.", "Listening again.", "I didn't catch that.",
        "I'm still on the last one — give me a second.",
        # Spoken by speak_brain_reply() when a turn is calling tools and has
        # produced no text yet. It has to be instant to be worth saying at
        # all — a filler that itself takes 1.5s of network to synthesise
        # would just move the silence, so it is pre-rendered here.
        "Give me a second.",
        "That's the short version, the full text is on screen.",
    )

    def _cache_key(self, text: str) -> str:
        return f"{self.voice}|{self.rate}|{self.pitch}|{text}"

    def _render_cached(self, sentence: str) -> tuple[np.ndarray, int] | None:
        """Render, reusing a previous render of the identical sentence."""
        key = self._cache_key(sentence)
        with self._cache_lock:
            hit = self._audio_cache.get(key)
        if hit is not None:
            return hit
        rendered = self._render(sentence)
        if rendered is not None and len(sentence) <= self.CACHEABLE_MAX_CHARS:
            with self._cache_lock:
                # Bounded: this is a voice assistant on a machine with ~1GB
                # free, not a CDN. Oldest entry goes when full.
                if len(self._audio_cache) >= self.CACHE_MAX_ENTRIES:
                    self._audio_cache.pop(next(iter(self._audio_cache)))
                self._audio_cache[key] = rendered
        return rendered

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
        # Pre-render the phrases Jarvis says constantly, so the reply to
        # "open chrome" is instant instead of a 1.5s round-trip.
        # In PARALLEL: serially this took 55s (16 phrases x ~1.5s of network
        # round-trip each), which is most of a minute where common replies
        # are still slow. These are independent network calls, so they
        # overlap cleanly — measured ~5s for all 16.
        import concurrent.futures as _cf

        with _cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="tts-warm") as pool:
            list(pool.map(self._render_cached_quiet, self._COMMON_PHRASES))

    def _render_cached_quiet(self, sentence: str):
        """_render_cached that never raises — used by the warmup pool, where
        one failed phrase must not abort the rest."""
        try:
            return self._render_cached(sentence)
        except Exception:
            return None

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
            rendered = self._render_cached(sentence)
            if rendered is None:
                continue  # one bad sentence shouldn't kill the whole reply
            pcm, rate = rendered
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
            pending = pool.submit(self._render_cached, sentences[0])
            for index in range(len(sentences)):
                if self._interrupt.is_set():
                    return False
                current, pending = pending, None
                # Kick off the NEXT synthesis before playing this one, so the
                # network round-trip overlaps playback instead of following it.
                if index + 1 < len(sentences):
                    pending = pool.submit(self._render_cached, sentences[index + 1])
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

    def open_stream(self) -> "SpeechStream":
        """
        Start speaking text that does not fully exist yet.

        say() needs the whole reply up front. That is fine for the router,
        which answers in one canned line, but it is exactly wrong for the
        brain: Brain.ask() used to wait for the SDK's ResultMessage before
        returning a single character, so nothing was synthesised until
        Claude had completely finished -- every token AND every tool call.
        On a turn that reads the disk or searches the web that is five to
        fifteen seconds of total silence, and only THEN a TTS round-trip.

        A stream flips the order. Sentences are pushed in as the model emits
        them and spoken immediately, so the clock that matters -- how long
        until he hears something -- is set by the first sentence, not the
        last one.
        """
        stream = SpeechStream(self)
        self._active_stream = stream
        return stream

    def say_now(self, text: str) -> bool:
        """
        Say something that cannot wait — a RED confirmation, an AMBER
        announcement — even if a reply is currently streaming.

        THIS IS NOT A CONVENIENCE. Speaker.say() takes _say_lock, and
        SpeechStream holds that lock for an entire reply. The safety hook
        runs while the turn is still in flight, so a confirmation asked
        through say() blocked on a lock that could only be released by a
        turn that was itself blocked waiting for the confirmation. Both
        halves waited for the other, forever: no question spoken, no
        timeout, no action, no error. Every RED tool the brain reached for
        did nothing at all, and Jalen simply stopped responding.

        So: if a stream is running, ask IT to speak the line — it is the
        thread holding the lock, so it is the only one that can. Otherwise
        this is an ordinary blocking say().
        """
        stream = self._active_stream
        if stream is not None and not stream.finished:
            if stream.interject(text):
                return True
            # It finished, or could not get to it in time. The lock is free
            # (or about to be), so the ordinary path is safe now.
        return self.say(text)

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


# Sentinel meaning "the queue is empty right now, but the producer hasn't
# finished" -- distinct from None, which means "the producer IS finished".
# Conflating the two is how a stream either ends early or hangs forever.
_NOTHING_YET = object()

# Pushed into the sentence queue purely to wake a consumer that is blocked on
# get(), so it goes round the loop and notices an urgent item. Carries no text
# and is skipped.
_WAKE = object()


class _Urgent:
    """
    Something that must be said NOW, in the middle of a streaming reply.

    Exists because of a real deadlock. A RED confirmation ("send this
    message, confirm?") is spoken from the safety hook, which runs while the
    turn is still in flight — and therefore while SpeechStream is holding
    _say_lock for the whole reply. Speaker.say() blocked on that lock, the
    stream could not finish because close() is only reached after the turn
    returns, and the turn could not return because it was stuck in the hook.
    Jalen went silent and stayed silent, which is what "it is ignoring me"
    was.

    The alternative fix — stop the stream, then speak — is simpler and
    wrong: it destroys whatever the model says after the confirmation, and
    it does so silently.
    """

    __slots__ = ("text", "done", "spoken")

    def __init__(self, text: str) -> None:
        self.text = text
        self.done = threading.Event()
        self.spoken = False


class SpeechStream:
    """
    A speaking session fed incrementally. See Speaker.open_stream().

    Holds the speaker's lock and its `speaking` flag for the WHOLE session,
    not per sentence. That matters for more than tidiness: app.py's main
    loop treats `speaker.speaking` as "this is Jarvis's own voice, ignore
    it". Speaking sentence-by-sentence through say() would drop that flag
    in every gap between sentences, and the microphone would hear the tail
    of his own speech in those gaps and treat it as the user talking.
    """

    def __init__(self, speaker: "Speaker") -> None:
        self._speaker = speaker
        self._q: "queue.Queue[str | None]" = queue.Queue()
        # A separate lane, checked at the top of every consumer iteration, so
        # an urgent line jumps ahead of the model's remaining sentences
        # instead of queueing behind a reply that may still be arriving.
        self._urgent: "queue.Queue[_Urgent]" = queue.Queue()
        self._spoken: list[str] = []
        self._pending_text = ""
        self._first_audio = threading.Event()
        self._done = threading.Event()
        self.interrupted = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="jarvis-tts-stream"
        )
        self._thread.start()

    # ------------------------------------------------------------- producing
    def push(self, text: str) -> None:
        """
        Add model output. Safe to call with partial text — anything that is
        not yet a complete sentence is held back until it is.

        Half a sentence must never reach the synthesiser. edge-tts renders
        "Your C drive is at ninety nine" with the falling intonation of a
        finished statement, so a reply chopped at the buffer boundary sounds
        like a series of confident non-sequiturs rather than one thought.
        """
        if not text:
            return
        self._pending_text += text
        # Everything up to the last sentence terminator is safe to speak;
        # the remainder stays buffered until more text arrives or close()
        # flushes it.
        cut = max(
            self._pending_text.rfind("."),
            self._pending_text.rfind("!"),
            self._pending_text.rfind("?"),
            self._pending_text.rfind(chr(10)),
        )
        if cut < 0:
            return
        ready, self._pending_text = self._pending_text[: cut + 1], self._pending_text[cut + 1 :]
        self._enqueue(ready)

    def _enqueue(self, text: str) -> None:
        for sentence in split_sentences(clean_for_speech(text)):
            self._q.put(sentence)

    def close(self, timeout: float = 120.0) -> str:
        """Flush the tail, wait for playback, return everything spoken."""
        if self._pending_text.strip():
            self._enqueue(self._pending_text)
            self._pending_text = ""
        self._q.put(None)
        self._thread.join(timeout)
        return " ".join(self._spoken).strip()

    def abandon(self) -> None:
        """Stop without waiting — used when a turn errors out mid-stream."""
        self._pending_text = ""
        self._q.put(None)

    @property
    def finished(self) -> bool:
        return self._done.is_set()

    def interject(self, text: str, timeout: float = 30.0) -> bool:
        """
        Speak something urgent inside this session. True if it was spoken.

        The consumer already owns the speaker's lock, so it is the only
        thread that CAN speak right now — asking it to do so is what breaks
        the deadlock described on _Urgent.

        Returns False rather than blocking forever if the stream is already
        finished or does not get to it in time; the caller then falls back
        to the ordinary locking path, which is safe once the stream has let
        the lock go.
        """
        if self._done.is_set() or not text:
            return False
        item = _Urgent(text)
        self._urgent.put(item)
        # The consumer is very likely parked on self._q.get(), waiting for
        # the model's next sentence. Nudge it so it goes round the loop and
        # sees the urgent lane.
        self._q.put(_WAKE)
        if not item.done.wait(timeout):
            return False
        return item.spoken

    def _drain_urgent(self, sp: "Speaker") -> None:
        """Say anything waiting in the urgent lane, in order."""
        while True:
            try:
                item = self._urgent.get_nowait()
            except queue.Empty:
                return
            try:
                rendered = sp._render_cached(item.text)
                if rendered is not None:
                    pcm, rate = rendered
                    self._first_audio.set()
                    sp._play(pcm, rate)
                    item.spoken = True
            except Exception:
                # A confirmation that cannot be rendered must not take the
                # reply down with it — but it must also not report success,
                # or the caller will wait for an answer to a question that
                # was never asked out loud.
                item.spoken = False
            finally:
                item.done.set()

    # ------------------------------------------------------------- consuming
    @property
    def has_spoken(self) -> bool:
        """True once real audio has actually reached the speakers."""
        return self._first_audio.is_set()

    def _run(self) -> None:
        sp = self._speaker
        with sp._say_lock:
            sp._interrupt.clear()
            sp._speaking.set()
            sp.on_state("speaking")
            producer_finished = False
            try:
                with _cf.ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="tts-stream-synth"
                ) as pool:
                    ahead: tuple | None = None
                    while not sp._interrupt.is_set():
                        # Checked FIRST, every iteration. This thread holds
                        # _say_lock, so it is the only one that can speak —
                        # which is why an urgent line has to be spoken here
                        # rather than by whoever asked for it.
                        self._drain_urgent(sp)

                        if ahead is not None:
                            future, text = ahead
                            ahead = None
                        else:
                            item = self._q.get()
                            if item is None:
                                break
                            if item is _WAKE:
                                continue      # nudged; the urgent lane has it
                            future, text = pool.submit(sp._render_cached, item), item

                        # If the next sentence is ALREADY available, start
                        # synthesising it before playing this one, so its
                        # network round-trip hides behind audio that is
                        # already playing. If it isn't available yet, don't
                        # block waiting — the model is still writing it.
                        try:
                            nxt = self._q.get_nowait()
                        except queue.Empty:
                            nxt = _NOTHING_YET
                        if nxt is None:
                            producer_finished = True
                        elif nxt is _WAKE:
                            pass          # a nudge, not a sentence
                        elif nxt is not _NOTHING_YET:
                            ahead = (pool.submit(sp._render_cached, nxt), nxt)

                        try:
                            rendered = future.result()
                        except Exception:
                            rendered = None   # one bad sentence != a dead reply
                        if rendered is not None:
                            pcm, rate = rendered
                            self._first_audio.set()
                            if not sp._play(pcm, rate):
                                self.interrupted = True
                                break
                        self._spoken.append(text)

                        if producer_finished and ahead is None:
                            break
            finally:
                # Release anyone still waiting on an urgent line before the
                # lock goes. They are marked NOT spoken, so the caller falls
                # back to the ordinary path rather than waiting for an answer
                # to a question nobody heard.
                while True:
                    try:
                        self._urgent.get_nowait().done.set()
                    except queue.Empty:
                        break
                sp._speaking.clear()
                sp._interrupt.clear()
                sp.on_state("idle")
                self._done.set()
                if sp._active_stream is self:
                    sp._active_stream = None
