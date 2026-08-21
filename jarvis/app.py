"""
The orchestrator. This is the loop that makes Jalen feel like a person.

    idle -> "hey jalen" -> listening -> you stop talking -> thinking
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
import re
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
from .timing import TimingLog, TurnTimer
from . import tools as systools
from .ui.orb import Orb, TranscriptWindow

# Queued mic audio older than this predates the wake word, so it can't be part
# of the command and is dropped. Anything under it is the tail of the phrase
# still being spoken and must be kept — see the wake handler in run().
STALE_AUDIO_S = 1.5

# --------------------------------------------------------------------------
# Endpointing support: deciding, from the WORDS, whether he has finished.
#
# The microphone cannot tell "he's done" from "he's thinking" — both are
# silence. That ambiguity is why vad.silence_ms had to be 2000ms, and that
# 2000ms was the largest fixed cost on every single turn, paid even by the
# commands the router answers without ever contacting Claude.
#
# The transcript resolves what the silence cannot. Measured over the 409
# real user utterances in data/audit.jsonl: 55% are six words or fewer
# (commands that should be instant) and only 1.0% end on a word implying
# more is coming. The fast path is the overwhelmingly common case and the
# patient path only has to cover a thin tail — which is exactly the shape
# that makes "endpoint early, then check the words" beat one pessimistic
# timeout applied to everybody.
# --------------------------------------------------------------------------

# Words nobody ends a sentence on. If the transcript at the fast endpoint
# ends here, he paused mid-thought: keep listening on the patient window.
_UNFINISHED_TAIL = re.compile(
    r"(?:^|\s)(?:and|or|but|so|then|also|plus|because|if|when|while|"
    r"which|to|for|with|from|into|onto|at|in|on|of|about|by|as|"
    r"the|a|an|my|your|his|her|their|its|this|these|"
    r"is|are|was|were|be|been|am|can|could|would|should|will|"
    r"want|need|like|going|gonna|try|trying|"
    r"um|uh|erm|hmm)$"
)
# Deliberately NOT on that list, though they look like they belong:
#
#   do / does / did   "what do you do", "what things can you do" — six of
#                     them end a sentence in the log, all complete questions.
#   that              "why is that", "what's that".
#   those             "delete those".
#   you / know / it   the three commonest final words he actually uses.
#
# The asymmetry is deliberate. A false "unfinished" only costs him the
# extra 1.4s he used to pay anyway, and the turn still runs. A false
# "finished" truncates the command, which is the failure that reads as
# being ignored. So the list errs toward waiting — but not on words this
# particular person demonstrably ends sentences with.

# Openers meaning "this is the REST of what I just said", not a new command.
# "open chrome" [pause] "and go to youtube" is one instruction with a gap in
# it; treated as two, the second half arrives with no subject and means
# nothing on its own.
_CONTINUATION_OPENER = re.compile(
    r"^(?:and then|after that|as well as|followed by|and|then|also|plus|or|next)\b"
)


def looks_unfinished(text: str) -> bool:
    """True when the transcript reads like the middle of a sentence."""
    cleaned = (text or "").strip().lower().rstrip(".,!?;:")
    if not cleaned:
        return False
    return bool(_UNFINISHED_TAIL.search(cleaned))


def is_continuation(text: str) -> bool:
    """True when the transcript picks up where the previous one left off."""
    return bool(_CONTINUATION_OPENER.match((text or "").strip().lower()))



# How many turns may be working at once before new speech is deferred with a
# spoken "still on the last one" instead of silently joining a backlog.
MAX_IN_FLIGHT_TURNS = 2

# Mic frames between checks for a hotkey signal. At audio.frame_ms = 32 this
# is roughly a third of a second — below the threshold where a key press
# feels unacknowledged, and 10x cheaper than checking on every frame.
SIGNAL_POLL_FRAMES = 10


class Jalen:
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
        self._quit = threading.Event()   # "quit jalen" / --stop: exit the loop
        self.paused = False              # "pause": stay alive, stop listening
        self._restart_requested = False  # run.py re-execs when this is set
        self._turn_lock = threading.Lock()
        self._turn_seq = 0               # monotonic id of the newest turn
        self._active_turns: set[int] = set()
        self._answer_q: queue.Queue[Optional[bool]] = queue.Queue()
        self._awaiting_confirmation = False
        self._awaiting_stop = False
        # Last thing he said, for re-attaching a continuation fragment —
        # see the stitching block in process().
        self._last_user_text = ""
        self._last_user_at = 0.0

        self.end_phrases = [p.lower() for p in cfg.get_path("conversation.end_phrases", [])]
        self.kill_phrases = [p.lower() for p in cfg.get_path("safety.kill_phrases", [])]
        self.address = cfg.get_path("identity.address_user_as", "")

        self.speaker.on_state = self.orb.set_state

        # Per-turn stopwatch. "Why is it so slow" had no answer before this,
        # because the only evidence was the wall-clock gap between his
        # utterance and Jalen's — a number that conflates waiting with
        # Jalen talking. See jarvis/timing.py.
        self.timings = TimingLog()
        self._turn_timer: TurnTimer | None = None
        self.speaker.on_audio_start = self._mark_first_audio

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
            raise RuntimeError("Jalen's event loop isn't running")
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

        # CONCURRENTLY, not one after another. These four warmups ran in
        # sequence and the wait was the SUM of them — measured on this
        # machine at startup: stt 2.3s + tts 9.4s + sysinfo 0s + brain 7.5s,
        # so roughly nineteen seconds before he could be answered properly.
        # He described pressing the hotkey and it "taking too much time to
        # load", and that is the number he was feeling.
        #
        # Nothing here depends on anything else here: three are independent
        # network handshakes and one is a disk scan. Run together the wait
        # becomes the SLOWEST of them, about nine seconds, for no extra work
        # and no extra risk — a failure in one was already isolated by
        # warm()'s own try/except.
        jobs = [
            ("stt", lambda: self.stt.warmup()),
            ("tts", lambda: self.speaker.warmup()),
            # Disk/cleanup scans take 13s and 41s cold. Doing them here means
            # "what's eating my disk" answers instantly the first time it's
            # asked, instead of after a 41-second silence.
            ("sysinfo", lambda: __import__(
                "jarvis.tools.sysinfo", fromlist=["prewarm_system_scan"]
            ).prewarm_system_scan()),
        ]
        if bool(self.cfg.get_path("brain.prewarm", True)):
            jobs.append(("brain", lambda: self._run_coro(self._start_brain())))

        for label, fn in jobs:
            threading.Thread(
                target=warm, args=(label, fn),
                name=f"jalen-prewarm-{label}", daemon=True,
            ).start()

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
        """
        Say it and wait until it has actually been said.

        say_now(), not say(). Both confirmations and AMBER announcements come
        through here, and both are spoken from the safety hook WHILE a turn
        is still streaming its reply — which is exactly the case say()
        deadlocks on. See Speaker.say_now().

        Note the audit line is written before the audio, so a line in the log
        is evidence the question was ASKED FOR, not that anyone heard it.
        That distinction is what made the original deadlock so hard to see:
        the log looked completely normal.
        """
        if not text:
            return
        self.audit.utterance(text, who="jarvis")
        self.speaker.say_now(text)

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
    # ------------------------------------------------------------- timing
    def _mark_first_audio(self) -> None:
        timer = self._turn_timer
        if timer is not None:
            timer.mark("first_audio")

    def _await_playback(self, grace_s: float = 0.5, limit_s: float = 180.0) -> None:
        """
        Block until the reply has actually finished playing.

        Needed because say() is asynchronous: it hands the audio to a worker
        thread and returns immediately, so a router turn's dispatch thread
        reaches its `finally` while Jalen is still mid-sentence — sometimes
        before the sound has started at all. Marking "done" there recorded
        turns that spoke for zero seconds and frequently had no first_audio
        mark whatsoever, which is worse than not measuring: it pulls the
        median silently toward zero and makes a regression look like an
        improvement.

        The brain path does not need this (SpeechStream.close() already
        blocks), but calling it there is harmless — `speaking` is false by
        then and both loops fall straight through.

        Two waits, not one. The first is a short grace period for playback
        to START, because the worker thread may not have set the flag yet;
        without it, a fast turn would sail past a flag that is about to be
        set. The second is the real wait, bounded so a wedged audio device
        can never hang a dispatch thread forever.
        """
        deadline = time.monotonic() + grace_s
        while not self.speaker.speaking and time.monotonic() < deadline:
            time.sleep(0.01)

        deadline = time.monotonic() + limit_s
        while self.speaker.speaking and time.monotonic() < deadline:
            time.sleep(0.02)

    def _finish_timing(self, timer: TurnTimer | None) -> None:
        """
        Close a turn's stopwatch and write one audit line.

        A turn that never produced audio — muted, or a tool-only action with
        nothing to say — is recorded anyway with whatever marks it does
        have. Dropping those would bias the median toward the turns that
        went well, which is the opposite of what this is for.
        """
        if timer is None:
            return
        timer.mark("done")
        self.timings.record(timer)
        self.audit.write("system", summary=timer.summary())

    # ------------------------------------------------------------- hotkey
    def _apply_signal(self, signal: str) -> bool:
        """
        Act on a press of the global hotkey. Returns True when the press
        means "start listening now", which the mic loop turns into the same
        state transition the wake word causes.

        "toggle" is what Ctrl+Alt+J actually sends, and it is deliberately
        NOT a plain mute toggle. What he wants from one key is "pay attention
        to me", and the obstacle to that differs by state: muted, the
        obstacle is the mute; paused, it's the pause; idle, it's that nothing
        is listening yet. One key resolves whichever one is in the way.
        """
        if signal == "mute":
            self.muted = True
            self.orb.set_state("muted")
            return False
        if signal == "unmute":
            self.muted = False
            self.paused = False
            self.orb.set_state("idle")
            return False
        if signal == "wake":
            self.muted = False
            self.paused = False
            return True
        if signal == "toggle":
            # Speaking? The press means "stop talking" — the same thing
            # barge-in does, for someone who would rather hit a key than
            # talk over it.
            if self.speaker.speaking:
                self.speaker.stop()
                return False
            if self.muted or self.paused:
                self.muted = False
                self.paused = False
                return True
            # Awake and idle: the press is a wake word.
            return True
        return False

    def handle_local(self, intent) -> Optional[str]:
        """Router hit — execute without ever touching an LLM."""
        tool = intent.tool

        if tool == "jalen_mute":
            self.muted = True
            self.orb.set_state("muted")
            return None
        if tool == "jalen_unmute":
            self.muted = False
            self.orb.set_state("idle")
            return intent.reply
        if tool == "jalen_sleep":
            # "sleep" and "pause" are the same thing to a user, and this
            # branch used to only SAY "Sleeping. Say hey Jalen to wake me."
            # while setting no state whatsoever — it kept right on listening
            # and acting. Now it does what it says.
            self.say_blocking(intent.reply or "Sleeping.")
            self.paused = True
            self.orb.set_state("muted")
            return None
        if tool == "jalen_quit":
            # Speak first, then tear down — say() is async and shutdown()
            # stops the speaker, so a plain say() here would be cut off
            # mid-word and the user would never hear the acknowledgement.
            self.say_blocking(intent.reply or "Shutting down.")
            self._quit.set()
            return None
        if tool == "jalen_pause":
            self.say_blocking(intent.reply or "Paused.")
            self.paused = True
            self.orb.set_state("muted")
            return None
        if tool == "jalen_resume":
            self.paused = False
            self.orb.set_state("idle")
            return intent.reply
        if tool == "jalen_orb_size":
            delta = int(intent.args.get("delta", 0))
            self.orb.resize_by(delta)
            return "Bigger." if delta > 0 else "Smaller."
        if tool == "jalen_timing":
            # Deliberately reports the PREVIOUS turn, not this one: this
            # turn has not finished, and its own first_audio mark is the
            # sound of it answering the question.
            return self.timings.report()
        if tool == "jalen_ack":
            # He said the name and nothing else. Answer and stay open — the
            # follow-up listen in run() is what makes "Jalen ... open chrome"
            # work as two breaths instead of one failed turn.
            return intent.reply
        if tool == "jalen_restart":
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
        if tool == "morning_brief":
            # This rule existed with reply=None and no implementation, so
            # "brief me" produced total silence — indistinguishable from
            # Jalen being broken. Report what's actually wired, and say
            # plainly what isn't, rather than pretending or staying quiet.
            #
            # It then had a subtler version of the same fault. The original
            # only CHECKED integrations.gmail.enabled and appended "I can't
            # include email yet" when it was off — so the moment Google was
            # actually connected and the flag flipped to true, the warning
            # correctly disappeared and nothing replaced it. A brief that
            # silently drops email reads as "no mail today", which is a
            # worse lie than admitting it isn't wired. Fetch it for real.
            from .tools import system as _sys

            parts = [_sys.get_date(), _sys.get_time(), _sys.get_battery()]
            missing: list[str] = []

            for label, enabled_key, fetch in (
                ("email", "integrations.gmail.enabled", self._brief_email),
                ("calendar", "integrations.calendar.enabled", self._brief_calendar),
            ):
                if not self.cfg.get_path(enabled_key, False):
                    missing.append(label)
                    continue
                line = fetch()
                if line:
                    parts.append(line)

            if missing:
                parts.append(
                    f"I can't include {' or '.join(missing)} yet — "
                    "that needs Google connected first."
                )
            return " ".join(parts)
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

    async def handle_with_brain(self, text: str, on_text=None) -> str:
        if self.brain is None:
            await self._start_brain()
        try:
            return await self.brain.ask(text, on_text=on_text)
        except Exception as exc:
            self.audit.error("brain", exc)

            # A CLI subprocess that died mid-stream leaves the client
            # unusable. Brain.ask() reconnects when the failure happens on
            # the way IN; this covers the way out. Dropping the client here
            # means the next turn starts a fresh one instead of repeating
            # this error forever, which is what the audit log shows
            # happening on 21 Aug — the same "terminated process" three
            # minutes apart, every turn in between answered with it.
            from .brain.agent import _looks_like_a_dead_client

            if _looks_like_a_dead_client(exc):
                try:
                    await self.brain.stop()
                except Exception:
                    pass
                self.brain = None
                return (
                    "My connection to Claude dropped, so I've reset it — "
                    "say that again and it should work."
                )

            # Everything else: say what broke in words, not as a traceback.
            # "My brain hit an error: Cannot write to terminated process
            # (exit code: 129)" is read out loud by a TTS engine, and it
            # tells him nothing he can act on.
            return f"Something went wrong in my head — {type(exc).__name__}. Try again?"

    def speak_brain_reply(self, user_text: str) -> None:
        """
        Run a brain turn and speak the answer WHILE it is being written.

        The old shape was three serial waits — finish the reply, synthesise
        it, play it — and he heard nothing through any of them. Measured on
        a real session that silence ran to a median of 3.0s and a p90 of 23s.

        Now the model's tokens flow straight into a SpeechStream, so the
        wait he actually experiences ends at the FIRST sentence. The work
        still takes as long as it takes; it just stops being
        indistinguishable from a crash while it happens.
        """
        if self.muted:
            # Nothing will be audible, so streaming buys nothing here and
            # the plain path keeps the summarise/transcript logic simple.
            self.say(self._run_coro(self.handle_with_brain(user_text)))
            return

        stream = self.speaker.open_stream()
        max_spoken = self.speaker.max_spoken
        state = {"chars": 0, "overflowed": False}

        def on_text(chunk: str) -> None:
            # Spec B15 still applies: long answers are summarised aloud and
            # shown in full on screen. Streaming means enforcing that as the
            # text arrives rather than measuring a finished string — once the
            # reply runs long, stop feeding the speaker and let the
            # transcript window carry the remainder.
            if state["overflowed"]:
                return
            state["chars"] += len(chunk)
            if state["chars"] > max_spoken:
                state["overflowed"] = True
                stream.push(" That's the short version, the full text is on screen.")
                return
            stream.push(chunk)

        # A turn that calls tools first produces no text for seconds. Silence
        # reads as "it's broken" — he said exactly that, more than once, in
        # the log. One short line costs nothing (it is pre-rendered into the
        # phrase cache at startup) and turns dead air into visible work.
        ack_after = float(self.cfg.get_path("brain.ack_after_ms", 1400)) / 1000.0

        def acknowledge() -> None:
            if state["chars"] == 0:
                stream.push("Give me a second.")

        ack_timer = threading.Timer(ack_after, acknowledge)
        ack_timer.daemon = True
        ack_timer.start()

        try:
            reply = self._run_coro(self.handle_with_brain(user_text, on_text=on_text))
        finally:
            ack_timer.cancel()

        if state["chars"] == 0:
            # Nothing streamed: a tool-only turn, or an SDK build that did
            # not emit deltas. Speak the finished string so a reply is never
            # silently dropped.
            stream.abandon()
            self.say(reply)
            return

        if state["overflowed"]:
            self.transcript.show("Full answer", reply)
        stream.close()
        self.audit.utterance(reply, who="jarvis")

    def process(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return

        # Stitching. The fast endpoint can close an utterance during a pause
        # that turns out to be mid-sentence, so "open chrome and go to
        # youtube" can arrive as two. The second half opens on a connector
        # and is meaningless alone — "and go to youtube" has no subject — so
        # re-attach it to what it continues instead of routing a fragment.
        # Bounded by a short window: a sentence starting with "and" a minute
        # later is a new thought, not the rest of an old one.
        window = float(self.cfg.get_path("conversation.stitch_window_s", 8))
        if (
            is_continuation(text)
            and self._last_user_text
            and time.monotonic() - self._last_user_at <= window
        ):
            text = f"{self._last_user_text} {text}"
        self._last_user_text = text
        self._last_user_at = time.monotonic()

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
        # Which path answered is recorded, because the two have completely
        # different budgets: a router hit should be under half a second and
        # a brain turn cannot be. One blended median would hide whichever of
        # them had regressed.
        if self._turn_timer is not None:
            self._turn_timer.route = "router" if intent is not None else "brain"
        if intent is not None:
            reply = self.handle_local(intent)
            if reply is not None:
                self.say(reply)
            if intent.tool != "cancel":
                return

        self.orb.set_state("thinking")
        self.speak_brain_reply(text)

    # ------------------------------------------------------------ brief parts
    # Both of these are deliberately failure-tolerant and deliberately SHORT.
    # A morning brief is spoken out loud in one breath, so it wants a count
    # and the two or three things that matter, not an inbox dump — and if
    # Google is unreachable it must degrade to one honest clause rather than
    # taking the whole brief down with it.

    def _brief_email(self) -> str:
        # Same sentence the "any new emails?" router turn speaks — see
        # gmail.unread_email_headline. Deliberately one implementation: two
        # near-identical summarisers drift, and the one nobody is looking at
        # is the one that ends up wrong.
        try:
            from .tools import gmail

            return gmail.unread_email_headline(max_results=5)
        except Exception as exc:
            return f"I couldn't reach your email ({type(exc).__name__})."

    def _brief_calendar(self) -> str:
        try:
            from .tools import gcalendar

            today = gcalendar.read_calendar(0)
        except Exception as exc:
            return f"I couldn't reach your calendar ({type(exc).__name__})."
        if today.startswith("Nothing on the calendar"):
            return "Nothing on your calendar today."
        events = [line.strip("- ") for line in today.splitlines() if line.startswith("- ")]
        if not events:
            return "Nothing on your calendar today."
        head = "; ".join(events[:3])
        more = f", and {len(events) - 3} more" if len(events) > 3 else ""
        return f"{len(events)} thing{'s' if len(events) != 1 else ''} on today: {head}{more}."

    # -------------------------------------------------------------------- loop
    def run(self) -> None:
        self.running.set()
        self.orb.start()
        self.orb.set_state("muted" if self.muted else "idle")

        self.wake.load()
        self.vad.load()

        listening = False
        # Did HE start this listening window by saying "hey jarvis", or did a
        # noise trip it open? The answer decides whether a window that
        # produces no speech is worth saying anything about — see the
        # empty-utterance branch at the bottom of this loop.
        wake_initiated = False
        follow_up_until = 0.0
        follow_up_s = float(self.cfg.get_path("conversation.follow_up_timeout_s", 12))
        barge_in = bool(self.cfg.get_path("conversation.barge_in", True))
        barge_threshold = float(self.cfg.get_path("conversation.barge_in_threshold", 0.6))

        self.audit.write("system", summary=f"Jalen started (session {self.session_id})")

        def dispatch_turn(text: str, turn_id: int, timer: TurnTimer) -> None:
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
                self._await_playback()
                self._finish_timing(timer)
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

        frames_seen = 0
        with self.mic:
            for frame in self.mic.frames():
                # `python run.py --stop` (and the spoken "quit") set this. We
                # check it here, in the loop that owns the microphone, so the
                # device and the audit DB get released properly — a hard kill
                # from outside leaves the mic held.
                if self._quit.is_set() or runtime.stop_requested():
                    break

                # The global hotkey (scripts/hotkeys.py) is a separate
                # process and talks to us through a sentinel file. Polled
                # every ~10 frames rather than every frame: at 32ms a frame
                # that is a stat() call 31 times a second forever, and a
                # third of a second is imperceptible for a key press.
                frames_seen += 1
                if frames_seen % SIGNAL_POLL_FRAMES == 0:
                    signal = runtime.take_signal()
                    if signal is not None and self._apply_signal(signal):
                        # Same state transition the wake word performs, and
                        # for the same reason — the hotkey IS a wake word you
                        # press instead of say.
                        listening = True
                        wake_initiated = True
                        self.orb.set_state("listening")
                        self.collector._reset()
                        continue

                if self.paused:
                    # Paused means "stop reacting", not "go deaf" — the wake
                    # word stays live (it's ~3% of one core) so "Hey Jalen"
                    # brings it back. Deafening it entirely would make the
                    # spoken "resume" impossible to hear, which is how you
                    # end up with an assistant you can't turn back on.
                    if self.wake.feed(frame):
                        self.paused = False
                        listening = True
                        wake_initiated = True
                        self.orb.set_state("listening")
                        self.collector._reset()
                    continue

                if self.kill.is_set():
                    self.speaker.stop()
                    self.kill.clear()
                    listening = False
                    self.orb.set_state("idle")
                    continue

                # barge-in: you talking beats Jalen talking
                if barge_in and self.speaker.speaking:
                    if self.vad.probability(frame) >= barge_threshold:
                        self.speaker.stop()
                        listening = True
                        # NOT wake_initiated. This used to be True, reasoning
                        # that he had "talked over it on purpose" — but
                        # barge-in fires on any SOUND above the threshold, not
                        # on a decision to speak: a cough, the keyboard, the
                        # television, or Jalen's own voice coming back through
                        # the microphone. When nothing coherent followed, the
                        # window closed and announced "I didn't catch that" at
                        # a person who had said nothing.
                        #
                        # Seen in the log on 21 Aug: a 20-second reply
                        # finished and "I didn't catch that" was logged in the
                        # same second. He described it as being told mid-task
                        # that it didn't catch the task.
                        #
                        # Stopping the speech on a noise is still right —
                        # cheap and instantly reversible. Announcing a failure
                        # to understand something nobody said is not.
                        wake_initiated = False
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
                        wake_initiated = False   # a sound opened this, not him
                        self.orb.set_state("listening")
                    elif self.wake.feed(frame):
                        listening = True
                        wake_initiated = True
                        follow_up_until = 0.0
                        self.orb.set_state("listening")
                        # Do NOT drain here. The frames still queued behind
                        # this one are the rest of what was just said — for
                        # "Hey Jalen, open Chrome" spoken in one breath, the
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
                    # A listening window that produced no speech. What to do
                    # about it depends entirely on who opened it.
                    #
                    # He said "hey jarvis" and then nothing came through:
                    # worth saying, because silently returning to idle is
                    # indistinguishable from "heard you and ignored you".
                    #
                    # A NOISE opened it — a keystroke, a door, the tail of
                    # Jalen's own voice arriving back through the mic during
                    # the follow-up window — and announcing that is pure
                    # self-inflicted interruption. He never asked anything,
                    # so there was nothing to catch. Worse, the announcement
                    # is itself speech, which the mic hears, which can trip
                    # the window open again.
                    #
                    # This was not a rare edge: "I didn't catch that" was
                    # 30 of the 255 things Jalen said in data/audit.jsonl,
                    # 11.8% of its entire spoken output, and it fired in the
                    # middle of a RED delete confirmation — talking over the
                    # question it had just asked, then cancelling the action
                    # for "no answer". Flash the orb instead: visible if he
                    # is looking, silent if he is not.
                    self.orb.flash("blocked", 0.8)
                    if wake_initiated:
                        self.say("I didn't catch that.")
                    self.orb.set_state("muted" if self.muted else "idle")
                    continue

                # The stopwatch starts the moment the microphone decided he
                # had finished — not when the turn is dispatched. Everything
                # between here and the first sound is silence he sits in.
                timer = TurnTimer()
                timer.mark("speech_end")

                self.orb.set_state("thinking")
                try:
                    text = self.stt.transcribe(utterance)
                except Exception as exc:
                    self.audit.error("stt", exc)
                    self.say("I didn't catch that — speech recognition failed.")
                    self.orb.set_state("idle")
                    continue

                timer.mark("transcript")
                timer.text = text or ""

                if not text:
                    self.orb.set_state("idle")
                    continue

                # The primary engine failed and the fallback covered for it.
                # Worth one audit line: the turn succeeded, so nothing else
                # would ever mention it, and a silent demotion to the local
                # model is exactly the kind of quiet degradation that gets
                # reported months later as "it understands me less well now".
                if self.stt.last_fallback_reason:
                    self.audit.write(
                        "system",
                        summary=(
                            f"stt fell back to {self.stt.last_engine} — "
                            f"{self.stt.last_fallback_reason[:160]}"
                        ),
                    )

                # The utterance closed on the FAST threshold. If the words
                # say he is mid-sentence ("...open chrome and"), put the
                # audio back and keep listening on the patient one. The
                # whole phrase is re-transcribed once at the end rather
                # than stitched from two partial transcripts — one Whisper
                # pass over the complete audio is both more accurate and
                # cheaper than two over halves of it.
                if not self.collector.was_patient and looks_unfinished(text):
                    self.collector.resume(utterance)
                    listening = True
                    self.orb.set_state("listening")
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
                # The speaker's first-audio callback needs to find THIS
                # turn's timer, so publish it before the thread starts.
                # Overlapping turns overwrite it deliberately: the newest
                # turn is the one whose silence he is currently sitting in.
                self._turn_timer = timer
                threading.Thread(
                    target=dispatch_turn, args=(text, turn_id, timer), daemon=True,
                    name=f"jalen-turn-{turn_id}",
                ).start()

    def shutdown(self) -> None:
        self.running.clear()
        self.speaker.stop()
        self.orb.stop()
        self.mic.stop()
        self.audit.write("system", summary="Jalen stopped")
        self.audit.close()
        if self.brain is not None and self._loop is not None:
            try:
                self._run_coro(self.brain.stop())
            except Exception:
                pass
        self._stop_loop()
