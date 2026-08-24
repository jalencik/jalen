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
from pathlib import Path
import time

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
        # When playback started, for speaking_for(). Set beside every
        # _speaking.set() so the two can never disagree.
        self._speaking_since = 0.0
        self._say_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._audio_cache: dict[str, tuple[np.ndarray, int]] = {}
        self.on_state = lambda state: None  # set by the orchestrator to drive the orb
        # Fired the instant audio first reaches the output device. This is
        # the end of the silence the user experiences as "slowness" — see
        # jarvis/timing.py for why that moment, specifically, is the one
        # worth measuring.
        self.on_audio_start = lambda: None
        # "cache" or "network" for the most recently rendered sentence. Read
        # by the turn timer; see _render_cached for why it is worth having.
        self.last_source = ""
        # The stream currently holding _say_lock, if any. say_now() needs to
        # know, because during a streamed reply that thread is the only one
        # that can speak. See say_now() for the deadlock this prevents.
        self._active_stream: "SpeechStream | None" = None

    # --------------------------------------------------------------- control
    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def speaking_for(self) -> float:
        """
        Seconds since playback started, or 0.0 when silent.

        Used by the barge-in check in app.py to ignore the opening of a
        reply. Without it, Jalen's own voice arriving back through the
        microphone stopped him mid-sentence: measured at 747-764ms into
        playback, over and over, in data/audit.jsonl.
        """
        started = self._speaking_since
        if not self._speaking.is_set() or not started:
            return 0.0
        return max(0.0, time.monotonic() - started)

    def stop(self) -> None:
        """
        Barge-in. Cuts playback within about one chunk.

        THE FLAG IS NOT ENOUGH ON ITS OWN, and the gap cost three minutes a
        time. A streamed reply spends most of its life blocked in
        `self._q.get()` waiting for the model to write the next sentence —
        a get() with no timeout. Setting `_interrupt` there changes nothing:
        the loop that would check it is asleep, so `_speaking` stays set,
        `speaking_for()` keeps climbing, and every consumer that waits for
        playback to end waits for the whole limit.

        Measured in data/audit.jsonl on 23 August: turns reporting
        `spoke=197970ms` and `spoke=191657ms` — almost exactly
        _await_playback's 180-second ceiling, not real speech. Those turns
        held a dispatch slot for three minutes each, which is why he heard
        "I'm still on the last one" three times while nothing was playing.

        So: set the flag AND wake the sleeper. The sentinel already exists
        for the urgent lane; this reuses it. The queue put is what makes the
        blocked get() return so the loop can see the flag it was set.
        """
        self._interrupt.set()
        stream = self._active_stream
        if stream is not None:
            try:
                stream._q.put_nowait(_WAKE)
            except Exception:
                # A full or closed queue means the stream is already
                # finishing, which is the outcome we wanted anyway.
                pass

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

    # Short confirmations Jalen says constantly. Synthesising "Opening
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
        # Counted in his own audit log. Each of these was costing a ~3.3s
        # network round trip EVERY time, and each is said verbatim over and
        # over: "No answer, so I've cancelled it." fifteen times, "Shutting
        # down" eight, the rating question seven.
        "No answer, so I've cancelled it.",
        "Shutting down. See you, Boss.",
        "Hey boss - how do you rate my work out of ten?",
        "Morning, Boss.",
        "Yes, Boss?",
        "On it.",
        "Sent.",
        "Nothing running.",
        "Thanks - that's noted.",
        "I'm working on it.",
    )

    # WHERE RENDERED SPEECH LIVES BETWEEN RUNS.
    #
    # Measured on his machine: edge-tts takes 3.3 SECONDS (median, 4.6s
    # worst) to render one short sentence, because it is a network round
    # trip to Microsoft. That single number explains most of what he has
    # been complaining about:
    #
    #   "it took me whole 20 seconds after orb appeared to actually speak"
    #       -> the warmup renders 18 phrases: 17.9s median in his log
    #   "it is taking too much time on that yellow phase"
    #       -> router turns think for 3.05s p50 while routing takes ~0ms.
    #          The orb is yellow because TTS is on the network, not because
    #          anything is being decided.
    #
    # An in-memory cache fixed the second occurrence and never the first,
    # because it died with the process. On disk it is paid once, ever.
    CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "tts_cache"
    DISK_CACHE_MAX = 400

    def _cache_key(self, text: str) -> str:
        return f"{self.voice}|{self.rate}|{self.pitch}|{text}"

    def _cache_path(self, key: str) -> Path:
        import hashlib

        return self.CACHE_DIR / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.npz"

    def _load_from_disk(self, key: str):
        """A previous run's render, or None. Never raises."""
        try:
            path = self._cache_path(key)
            if not path.exists():
                return None
            with np.load(path) as blob:
                return blob["audio"], int(blob["rate"])
        except Exception:
            return None

    def _save_to_disk(self, key: str, audio, rate: int) -> None:
        """Best effort. A full disk must cost speed, never a spoken reply."""
        try:
            self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            existing = list(self.CACHE_DIR.glob("*.npz"))
            if len(existing) >= self.DISK_CACHE_MAX:
                # Oldest first. This is a speed cache, not a record.
                for stale in sorted(existing, key=lambda f: f.stat().st_mtime)[:40]:
                    stale.unlink(missing_ok=True)
            np.savez_compressed(self._cache_path(key), audio=audio, rate=rate)
        except Exception:
            pass

    def _render_cached(self, sentence: str) -> tuple[np.ndarray, int] | None:
        """
        Render, reusing a previous render of the identical sentence.

        `last_source` records which of the three routes answered. It is the
        single most useful fact about a slow turn: measured on this machine
        a cache hit is 25ms and a network render is about 3000ms, so a
        timing line that does not say which one happened cannot be acted on
        at all. Written on every call, including the miss, so it never
        describes an earlier sentence.
        """
        key = self._cache_key(sentence)
        with self._cache_lock:
            hit = self._audio_cache.get(key)
        if hit is not None:
            self.last_source = "cache"
            return hit

        # Then a previous RUN's render. This is what makes startup free.
        on_disk = self._load_from_disk(key)
        if on_disk is not None:
            self._remember(key, on_disk)
            self.last_source = "cache"
            return on_disk

        self.last_source = "network"
        rendered = self._render(sentence)
        if rendered is not None and len(sentence) <= self.CACHEABLE_MAX_CHARS:
            self._remember(key, rendered)
            # Never at the cost of a reply. The cache is an optimisation, and
            # a full disk must cost speed rather than speech - so this is
            # guarded HERE as well as inside _save_to_disk. Belt and braces
            # on the one path where the failure would be silent and total.
            try:
                self._save_to_disk(key, rendered[0], rendered[1])
            except Exception:
                pass
        return rendered

    def _remember(self, key: str, rendered) -> None:
        """
        Put it in memory, bounded. ONE place, because the disk-hit path
        added a second insertion that skipped the bound entirely and let the
        cache grow past its limit - caught by test_audio_cache_is_bounded,
        which is exactly what that test is for.
        """
        with self._cache_lock:
            if len(self._audio_cache) >= self.CACHE_MAX_ENTRIES:
                self._audio_cache.pop(next(iter(self._audio_cache)))
            self._audio_cache[key] = rendered

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
        # The connection probe is SKIPPED when the disk cache is already
        # populated. It exists to pay edge-tts's cold-connection cost up
        # front, and there is nothing to pay when the first thing Jalen says
        # will come off the disk anyway. On his machine this was ~4s of every
        # single startup, spent to warm a connection that then went unused.
        already_warm = False
        try:
            already_warm = len(list(self.CACHE_DIR.glob("*.npz"))) >= max(
                8, len(self._COMMON_PHRASES) // 2)
        except Exception:
            already_warm = False
        if not already_warm:
            asyncio.run(self._synthesise("ready"))
        # Pre-render the phrases Jalen says constantly, so the reply to
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
            self._speaking_since = time.monotonic()
            self._speaking.set()
            self.on_state("speaking")
            try:
                sentences = split_sentences(text)
                if self.sentence_streaming and len(sentences) > 1:
                    return self._say_streaming(sentences)
                return self._say_sequential(sentences)
            finally:
                self._speaking.clear()
                self._speaking_since = 0.0
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
    loop treats `speaker.speaking` as "this is Jalen's own voice, ignore
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
            sp._speaking_since = time.monotonic()
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
                sp._speaking_since = 0.0
                sp._interrupt.clear()
                sp.on_state("idle")
                self._done.set()
                if sp._active_stream is self:
                    sp._active_stream = None
