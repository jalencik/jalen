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
from .safety import SafetyEngine, Tier
from . import tools as systools
from .ui.orb import Orb, TranscriptWindow


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

        self.brain = None  # lazily started — costs nothing until first real question

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
            self.say(intent.reply or "Sleeping.")
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

    async def handle_with_brain(self, text: str) -> str:
        if self.brain is None:
            from .brain.agent import Brain
            from .brain.tools import build_sdk_tools

            self.brain = Brain(
                self.cfg, self.safety, self.audit,
                confirm=self.confirm, announce=self.announce,
                tools=build_sdk_tools(),
            )
            await self.brain.start()
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

        def dispatch_turn(text: str) -> None:
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
            with systools.com_initialized():
                self.process(text)
            if self.cfg.get_path("conversation.follow_up", True):
                follow_up_until = time.monotonic() + follow_up_s
            self.orb.set_state("muted" if self.muted else "idle")

        with self.mic:
            for frame in self.mic.frames():
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
                        self.mic.drain()
                        self.collector._reset()
                    continue

                self.orb.set_level(float(np.abs(frame).mean() * 12))
                utterance = self.collector.feed(frame)
                if utterance is None:
                    continue

                listening = False
                if len(utterance) == 0:
                    self.orb.set_state("idle")
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

                threading.Thread(target=dispatch_turn, args=(text,), daemon=True).start()

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
