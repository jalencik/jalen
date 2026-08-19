"""
The orchestrator. This is the loop that makes Jarvis feel like a person.

    idle -> "hey jarvis" -> listening -> you stop talking -> thinking
         -> speaking (interruptible) -> follow-up window -> idle

Design notes worth knowing before you change anything here:

* One process. Wake word, VAD and STT share the numpy/onnxruntime import, which
  costs ~65 MB once instead of three times. On 8 GB that matters.
* The router runs BEFORE the brain. Most turns never reach Claude.
* Barge-in works because playback happens on a worker thread while this loop
  keeps reading the mic. When VAD says you're talking, we call speaker.stop().
* The kill switch is checked in the same loop, so "stop" always wins.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
import uuid
from typing import Optional

import numpy as np

from .audio.mic import Microphone
from .audio.stt import Transcriber
from .audio.tts import Speaker
from .audio.vad import VAD, UtteranceCollector
from .audio.wake import WakeWord
from .audit import AuditLog
from .brain.router import IntentRouter
from .config import CONFIG, SECRETS
from . import runtime
from .safety import SafetyEngine, Tier
from . import tools as systools
from .ui.orb import Orb, TranscriptWindow

# Queued mic audio older than this predates the wake word, so it can't be part
# of the command and is dropped. Anything under it is the tail of the phrase
# still being spoken and must be kept — see the wake handler in run().
STALE_AUDIO_S = 1.5

# How many turns may be working at once before new speech is deferred with a
# spoken "still on the last one" instead of silently joining a backlog.
MAX_IN_FLIGHT_TURNS = 2


class Jarvis:
    def __init__(self, cfg=CONFIG, secrets=SECRETS) -> None:
        self.cfg = cfg
        self.secrets = secrets
        self.session_id = uuid.uuid4().hex[:12]

        self.audit = AuditLog(cfg, self.session_id)
        self.safety = SafetyEngine(cfg)
        self.router = IntentRouter(cfg)

        self.mic = Microphone(cfg)
        self.wake = WakeWord(cfg)
        self.vad = VAD(cfg)
        self.collector = UtteranceCollector(cfg, self.vad)
        self.stt = Transcriber(cfg, secrets)
        self.speaker = Speaker(cfg)
        self.orb = Orb(cfg)
        self.transcript = TranscriptWindow(cfg)

        self.brain = None  # started by prewarm(), or lazily on first real question
        self._brain_lock = asyncio.Lock()

        # One persistent event loop, on its own thread, for the whole process
        # lifetime. Brain's ClaudeSDKClient (and anything else async) is
        # created and awaited on THIS loop only — never a fresh asyncio.run()
        # loop per turn, which used to orphan the client from turn 2 onward
        # (see HANDOFFPROMPT §3). process() and handle_local() are sync
        # methods called from the mic thread or the text-mode input loop;
        # _run_coro() is how they hand async work to this loop and block for
        # the result.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: Optional[threading.Thread] = None
        self._start_loop()

        self.muted = bool(cfg.get_path("startup.start_muted", True))
        self.running = threading.Event()
        self.kill = threading.Event()
        self._quit = threading.Event()   # "quit jarvis" / --stop: exit the loop
        self.paused = False              # "pause": stay alive, stop listening
        self._restart_requested = False  # run.py re-execs when this is set
        self._turn_lock = threading.Lock()
        self._turn_seq = 0               # monotonic id of the newest turn
        self._active_turns: set[int] = set()
        self._answer_q: queue.Queue[Optional[bool]] = queue.Queue()
        self._awaiting_confirmation = False
        self._awaiting_stop = False

        self.end_phrases = [p.lower() for p in cfg.get_path("conversation.end_phrases", [])]
        self.kill_phrases = [p.lower() for p in cfg.get_path("safety.kill_phrases", [])]
        self.address = cfg.get_path("identity.address_user_as", "")

        self.speaker.on_state = self.orb.set_state

    # -------------------------------------------------------------- event loop
    def _start_loop(self) -> None:
        ready = threading.Event()

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()

        self._loop_thread = threading.Thread(target=runner, name="jarvis-asyncio", daemon=True)
        self._loop_thread.start()
        ready.wait()

    def _run_coro(self, coro):
        """
        Submit a coroutine to the persistent loop from any other thread and
        block until it finishes. This is the only way async work (brain
        turns, confirmations, announces) should run — never asyncio.run().
        """
        if self._loop is None:
            raise RuntimeError("Jarvis's event loop isn't running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def _stop_loop(self) -> None:
        if self._loop is None:
            return
        loop, self._loop = self._loop, None
        loop.call_soon_threadsafe(loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5)
        loop.close()

    # ----------------------------------------------------------------- warmup
    def prewarm(self) -> None:
        """
        Pay every one-time connection cost in the background at startup,
        instead of on the user's first command.

        Measured on this machine, cold vs warm:
            Groq STT      3988ms -> ~330ms
            edge-tts      4562ms -> ~2000ms
            Claude client 17734ms -> ~2885ms

        That's the difference between "the first thing I say takes half a
        minute" and "it answers". None of this sends a billable request: the
        cost being paid here is DNS, TLS and client construction, plus (for
        Claude) starting the SDK subprocess and its MCP handshake.
        """
        def warm(label: str, fn) -> None:
            try:
                t0 = time.perf_counter()
                fn()
                self.audit.write(
                    "system", summary=f"prewarm {label}: {(time.perf_counter() - t0) * 1000:.0f}ms"
                )
            except Exception as exc:
                # Warmup is best-effort by definition — if the network is
                # down, the real call will surface the error properly later.
                self.audit.error(f"prewarm.{label}", exc)

        def warm_all() -> None:
            warm("stt", lambda: self.stt.warmup())
            warm("tts", lambda: self.speaker.warmup())
            if bool(self.cfg.get_path("brain.prewarm", True)):
                warm("brain", lambda: self._run_coro(self._start_brain()))

        threading.Thread(target=warm_all, name="jarvis-prewarm", daemon=True).start()

    # ------------------------------------------------------------------ speech
    def say(self, text: str, *, force: bool = False) -> None:
        if not text:
            return
        if self.muted and not force:
            self.orb.flash("muted", 0.6)
            return
        spoken, full = self.speaker.summarise_if_long(text)
        if full:
            self.transcript.show("Full answer", full)
        self.audit.utterance(text, who="jarvis")
        threading.Thread(target=self.speaker.say, args=(spoken,), daemon=True).start()

    def say_blocking(self, text: str) -> None:
        if not text:
            return
        self.audit.utterance(text, who="jarvis")
        self.speaker.say(text)

    # ------------------------------------------------------------ confirmations
    async def confirm(self, question: str) -> bool:
        """RED tier: ask out loud and wait for a real yes (spec F45/F46)."""
        timeout = float(self.cfg.get_path("safety.confirm_timeout_s", 20))
        self.orb.set_state("blocked")
        self.say_blocking(question)
        self._awaiting_confirmation = True
        self._drain_answers()
        try:
            answer = await asyncio.get_running_loop().run_in_executor(
                None, self._wait_for_answer, timeout
            )
        finally:
            self._awaiting_confirmation = False
            self.orb.set_state("thinking")
        if answer is None:
            self.say_blocking("No answer, so I've cancelled it.")
            return False
        return answer

    async def announce(self, text: str) -> None:
        """AMBER tier: say it, then give a short window to say stop."""
        window = float(self.cfg.get_path("safety.undo_window_s", 4))
        self.say_blocking(text)
        self._awaiting_stop = True
        self._drain_answers()
        try:
            stopped = await asyncio.get_running_loop().run_in_executor(
                None, self._wait_for_stop, window
            )
        finally:
            self._awaiting_stop = False
        if stopped:
            raise RuntimeError("cancelled by user")

    def _drain_answers(self) -> None:
        while not self._answer_q.empty():
            try:
                self._answer_q.get_nowait()
            except queue.Empty:
                break

    def _wait_for_answer(self, timeout: float) -> Optional[bool]:
        try:
            return self._answer_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _wait_for_stop(self, window: float) -> bool:
        try:
            return self._answer_q.get(timeout=window) is False
        except queue.Empty:
            return False

    @staticmethod
    def _parse_yes_no(text: str) -> Optional[bool]:
        low = text.strip().lower().rstrip(".!?")
        if low in ("yes", "yeah", "yep", "yes please", "do it", "go ahead", "confirm",
                   "sure", "ok", "okay", "correct", "affirmative", "send it"):
            return True
        if low in ("no", "nope", "don't", "dont", "stop", "cancel", "abort",
                   "no thanks", "never mind", "nevermind", "wait"):
            return False
        return None

    # ---------------------------------------------------------------- handling
    def handle_local(self, intent) -> Optional[str]:
        """Router hit — execute without ever touching an LLM."""
        tool = intent.tool

        if tool == "jarvis_mute":
            self.muted = True
            self.orb.set_state("muted")
            return None
        if tool == "jarvis_unmute":
            self.muted = False
            self.orb.set_state("idle")
            return intent.reply
        if tool == "jarvis_sleep":
            # "sleep" and "pause" are the same thing to a user, and this
            # branch used to only SAY "Sleeping. Say hey Jarvis to wake me."
            # while setting no state whatsoever — it kept right on listening
            # and acting. Now it does what it says.
            self.say_blocking(intent.reply or "Sleeping.")
            self.paused = True
            self.orb.set_state("muted")
            return None
        if tool == "jarvis_quit":
            # Speak first, then tear down — say() is async and shutdown()
            # stops the speaker, so a plain say() here would be cut off
            # mid-word and the user would never hear the acknowledgement.
            self.say_blocking(intent.reply or "Shutting down.")
            self._quit.set()
            return None
        if tool == "jarvis_pause":
            self.say_blocking(intent.reply or "Paused.")
            self.paused = True
            self.orb.set_state("muted")
            return None
        if tool == "jarvis_resume":
            self.paused = False
            self.orb.set_state("idle")
            return intent.reply
        if tool == "jarvis_restart":
            self.say_blocking(intent.reply or "Restarting.")
            self._restart_requested = True
            self._quit.set()
            return None
        if tool == "private_mode":
            on = bool(intent.args.get("on"))
            self.audit.set_private_mode(on)
            return intent.reply
        if tool == "set_posture":
            self.safety.posture = intent.args.get("posture", "irreversible_only")
            self.safety.paranoid = self.safety.posture == "paranoid"
            return intent.reply
        if tool == "audit_digest":
            digest = self.audit.daily_digest()
            self.transcript.show("Audit", digest)
            return "The log is on screen."
        if tool in ("cancel", "acknowledge"):
            return intent.reply
        if tool == "greet":
            hour = time.localtime().tm_hour
            part = "Morning" if hour < 12 else "Afternoon" if hour < 18 else "Evening"
            return f"{part}, {self.address}." if self.address else f"{part}."
        if tool == "reload_config":
            return intent.reply

        verdict = self.safety.classify(tool, intent.args, origin="user")
        if verdict.tier is Tier.BLACK:
            self.audit.action(verdict, "blocked")
            return f"I won't do that — {verdict.reason}."
        if verdict.tier is Tier.RED:
            approved = self._run_coro(self.confirm(f"{verdict.summary}. Confirm?"))
            self.audit.action(verdict, "executed" if approved else "cancelled")
            if not approved:
                return "Cancelled."
        elif verdict.tier is Tier.AMBER:
            # Router tools used to skip straight to execution here — AMBER's
            # announce+undo-window (spec: "say stop if you don't want that")
            # was only ever wired on the Brain/agent-hook path. Anything
            # reached through the router ran as if it were GREEN, with no
            # announcement and no chance to say stop. Caught live while
            # driving the app for the Phase B checkpoint.
            template = self.cfg.get_path(
                "safety_tiers.amber.announce_template",
                "{summary}. Say stop if you don't want that.",
            )
            try:
                self._run_coro(self.announce(template.format(summary=verdict.summary)))
            except RuntimeError:
                self.audit.action(verdict, "cancelled")
                return "Cancelled."

        try:
            result = systools.call(tool, intent.args)
            self.audit.action(verdict, "executed")
            return intent.reply or result
        except KeyError:
            return None  # not a local tool after all — let the brain try
        except Exception as exc:
            self.audit.action(verdict, "failed", str(exc))
            return f"That didn't work: {exc}"

    async def _start_brain(self) -> None:
        """Construct and connect the Claude client. Idempotent, and guarded
        so prewarm and a real first question can't race into building two."""
        async with self._brain_lock:
            if self.brain is not None:
                return
            from .brain.agent import Brain
            from .brain.tools import build_sdk_tools

            brain = Brain(
                self.cfg, self.safety, self.audit,
                confirm=self.confirm, announce=self.announce,
                tools=build_sdk_tools(),
            )
            await brain.start()
            self.brain = brain

    async def handle_with_brain(self, text: str) -> str:
        if self.brain is None:
            await self._start_brain()
        try:
            return await self.brain.ask(text)
        except Exception as exc:
            self.audit.error("brain", exc)
            return f"My brain hit an error: {exc}"

    def process(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.audit.utterance(text, who="user")

        low = text.lower().rstrip(".!?")

        # kill switch always wins
        if low in self.kill_phrases:
            self.speaker.stop()
            self.kill.set()
            self._answer_q.put(False)
            return

        # a pending yes/no takes priority over everything else
        if self._awaiting_confirmation:
            answer = self._parse_yes_no(text)
            if answer is not None:
                self._answer_q.put(answer)
                return

        # Exact match, not substring: "...just the number, nothing else." is a
        # real question, not a request to end the conversation, but "nothing
        # else" is a configured end phrase — `in` would swallow it silently
        # before it ever reached the router or the brain. Caught live while
        # driving the app for the Phase B checkpoint (handoff §1 rule).
        if low in self.end_phrases:
            self.say("Any time.")
            return

        intent = self.router.route(text)
        if intent is not None:
            reply = self.handle_local(intent)
            if reply is not None:
                self.say(reply)
            if intent.tool != "cancel":
                return

        self.orb.set_state("thinking")
        reply = self._run_coro(self.handle_with_brain(text))
        self.say(reply)

    # -------------------------------------------------------------------- loop
    def run(self) -> None:
        self.running.set()
        self.orb.start()
        self.orb.set_state("muted" if self.muted else "idle")

        self.wake.load()
        self.vad.load()

        listening = False
        follow_up_until = 0.0
        follow_up_s = float(self.cfg.get_path("conversation.follow_up_timeout_s", 12))
        barge_in = bool(self.cfg.get_path("conversation.barge_in", True))
        barge_threshold = float(self.cfg.get_path("conversation.barge_in_threshold", 0.6))

        self.audit.write("system", summary=f"Jarvis started (session {self.session_id})")

        def dispatch_turn(text: str, turn_id: int) -> None:
            """
            Run process() on its own thread so this loop keeps reading mic
            frames while a turn is in flight — including while it's stuck
            inside a RED confirmation or an AMBER stop-window. Calling
            process() inline here would freeze this exact loop until the
            turn returns, and since answering a confirmation means calling
            process() AGAIN (see _awaiting_confirmation below and in
            confirm()/announce()), that second call could never happen: the
            loop that would deliver it is the one blocked waiting for it.
            Every RED action would silently time out and "no" would be the
            only possible outcome, no matter what you said.
            """
            nonlocal follow_up_until
            try:
                with systools.com_initialized():
                    self.process(text)
            finally:
                with self._turn_lock:
                    self._active_turns.discard(turn_id)
            # Only the newest turn owns the UI state and the follow-up window.
            # Without this, a slow turn finishing late would reopen the
            # follow-up window and reset the orb long after the user moved on.
            if turn_id == self._turn_seq:
                if self.cfg.get_path("conversation.follow_up", True):
                    follow_up_until = time.monotonic() + follow_up_s
                self.orb.set_state("muted" if self.muted else "idle")

        self.prewarm()

        with self.mic:
            for frame in self.mic.frames():
                # `python run.py --stop` (and the spoken "quit") set this. We
                # check it here, in the loop that owns the microphone, so the
                # device and the audit DB get released properly — a hard kill
                # from outside leaves the mic held.
                if self._quit.is_set() or runtime.stop_requested():
                    break

                if self.paused:
                    # Paused means "stop reacting", not "go deaf" — the wake
                    # word stays live (it's ~3% of one core) so "Hey Jarvis"
                    # brings it back. Deafening it entirely would make the
                    # spoken "resume" impossible to hear, which is how you
                    # end up with an assistant you can't turn back on.
                    if self.wake.feed(frame):
                        self.paused = False
                        listening = True
                        self.orb.set_state("listening")
                        self.collector._reset()
                    continue

                if self.kill.is_set():
                    self.speaker.stop()
                    self.kill.clear()
                    listening = False
                    self.orb.set_state("idle")
                    continue

                # barge-in: you talking beats Jarvis talking
                if barge_in and self.speaker.speaking:
                    if self.vad.probability(frame) >= barge_threshold:
                        self.speaker.stop()
                        listening = True
                        self.orb.set_state("listening")
                        self.collector._reset()
                    continue

                if not listening:
                    in_follow_up = time.monotonic() < follow_up_until
                    # A pending RED confirmation or AMBER stop-window is
                    # itself a listening window — you shouldn't have to say
                    # "hey jarvis" again just to answer a question it just
                    # asked you.
                    awaiting_reply = self._awaiting_confirmation or self._awaiting_stop
                    if (in_follow_up or awaiting_reply) and self.vad.probability(frame) >= self.vad.threshold:
                        listening = True
                        self.orb.set_state("listening")
                    elif self.wake.feed(frame):
                        listening = True
                        follow_up_until = 0.0
                        self.orb.set_state("listening")
                        # Do NOT drain here. The frames still queued behind
                        # this one are the rest of what was just said — for
                        # "Hey Jarvis, open Chrome" spoken in one breath, the
                        # wake word fires ~0.77s in and the queued frames hold
                        # "open Chrome". Draining threw exactly that away, so
                        # the collector then heard only silence, waited out
                        # its 4s grace window, and dropped the command without
                        # a word. That is the "needs awkward pauses / say it
                        # twice" problem. Measured: 0.99s of command audio
                        # discarded on a 1.78s phrase.
                        #
                        # Only drop the backlog if it's genuinely stale (the
                        # machine stalled and frames piled up), since that
                        # audio predates the wake word and isn't the command.
                        if self.mic.queued_seconds() > STALE_AUDIO_S:
                            self.mic.drain()
                        self.collector._reset()
                    continue

                self.orb.set_level(float(np.abs(frame).mean() * 12))
                utterance = self.collector.feed(frame)
                if utterance is None:
                    continue

                listening = False
                if len(utterance) == 0:
                    # Wake fired but no speech followed. Say so: silently
                    # returning to idle is indistinguishable from "heard you
                    # and ignored you", which is how a dropped command felt
                    # like the assistant simply not working.
                    self.orb.flash("blocked", 0.8)
                    self.say("I didn't catch that.")
                    self.orb.set_state("muted" if self.muted else "idle")
                    continue

                self.orb.set_state("thinking")
                try:
                    text = self.stt.transcribe(utterance)
                except Exception as exc:
                    self.audit.error("stt", exc)
                    self.say("I didn't catch that — speech recognition failed.")
                    self.orb.set_state("idle")
                    continue

                if not text:
                    self.orb.set_state("idle")
                    continue

                with self._turn_lock:
                    self._turn_seq += 1
                    turn_id = self._turn_seq
                    in_flight = len(self._active_turns)
                    self._active_turns.add(turn_id)
                # Backlog guard. Turns used to be dispatched with no limit and
                # no cancellation, so a handful of commands issued while one
                # was still working would all complete later and speak in a
                # burst — the reported "old commands execute minutes later".
                if in_flight >= MAX_IN_FLIGHT_TURNS:
                    with self._turn_lock:
                        self._active_turns.discard(turn_id)
                    self.say("I'm still on the last one — give me a second.")
                    self.orb.set_state("thinking")
                    continue
                threading.Thread(
                    target=dispatch_turn, args=(text, turn_id), daemon=True,
                    name=f"jarvis-turn-{turn_id}",
                ).start()

    def shutdown(self) -> None:
        self.running.clear()
        self.speaker.stop()
        self.orb.stop()
        self.mic.stop()
        self.audit.write("system", summary="Jarvis stopped")
        self.audit.close()
        if self.brain is not None and self._loop is not None:
            try:
                self._run_coro(self.brain.stop())
            except Exception:
                pass
        self._stop_loop()
