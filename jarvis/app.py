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
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import numpy as np

from .audio.mic import Microphone
from .audio.stt import Transcriber
from .audio.tts import Speaker
from .audio.vad import VAD, UtteranceCollector
from .audio.wake import WakeWord
from .audit import AuditLog
from . import conversation, habits, plan as planning
from . import taint
from .brain.router import Intent, IntentRouter, addressed_to_jalen
from .config import CONFIG, SECRETS
from . import crashlog
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


# Did he ask to HEAR the whole thing?
#
# Long answers are capped at tts.max_spoken_chars and the rest goes to the
# screen. That is right for an answer he did not ask to hear in full, and
# wrong - obviously, infuriatingly wrong - when he did:
#
#     "it is not reading emals till the end, it just moved to antoher email
#      when I wanted him to read that out loud till the end"
#
# The cap is a default about length, never a rule about what he is allowed
# to hear. When he says "read it", the cap does not apply.
_READ_IT_ALL = re.compile(
    r"\bread\b(?![a-z])"
    r"|\bout loud\b|\baloud\b"
    r"|\btill the end\b|\bto the end\b|\bin full\b|\bfull(?:ly)?\b"
    r"|\bwhole thing\b|\ball of (?:it|them)\b|\bentire\b|\bevery word\b",
    re.I,
)


def wants_it_all(text: str) -> bool:
    """True when he asked to be read something rather than told about it."""
    return bool(_READ_IT_ALL.search(text or ""))


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

# Mic frames between checks for a finished background coding job. At 32ms
# that is about five seconds — fast enough that "it just finished" is true,
# slow enough to be a stat() every 150 frames rather than every one.
JOB_POLL_FRAMES = 150


class Jalen:
    def __init__(self, cfg=CONFIG, secrets=SECRETS, mode: str = "voice") -> None:
        self.cfg = cfg
        self.secrets = secrets
        self.mode = mode
        self.session_id = uuid.uuid4().hex[:12]

        self.audit = AuditLog(cfg, self.session_id)

        # Before anything overwrites it: how did the LAST run end?
        #
        # On 21 August one instance vanished 15 seconds in and left nothing
        # in the audit log to say so — see jarvis/crashlog.py. The record is
        # read here, reported into the audit if it says the previous process
        # never shut down, and only then reclaimed for this run. A clean
        # previous stop says nothing at all: a line on every single startup
        # is noise, and noise is what stops anyone reading the file.
        previous = crashlog.previous_exit()
        note = crashlog.describe_previous_exit(previous)
        if note:
            self.audit.write("system", summary=note, outcome="failed",
                             detail={"previous_exit": previous})
        crashlog.mark_running(self.session_id, mode)
        crashlog.install(
            on_crash=lambda where, exc: self.audit.error(f"crash.{where}", exc)
        )

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
        # Why the mic loop ended, set by run() the moment it knows. shutdown()
        # falls back to it so the audit line names the actual cause rather
        # than the generic "shutdown" its caller passes.
        self._exit_reason: str | None = None
        self.paused = False              # "pause": stay alive, stop listening
        self._restart_requested = False  # run.py re-execs when this is set
        self._turn_lock = threading.Lock()
        self._turn_seq = 0               # monotonic id of the newest turn
        self._active_turns: set[int] = set()
        self._answer_q: queue.Queue[Optional[bool]] = queue.Queue()
        # Free-text answers, separate from the yes/no queue. Sharing one
        # queue would let a stray "yes" satisfy "what's your phone
        # number", and a phone number satisfy a delete confirmation.
        self._reply_q: queue.Queue[str] = queue.Queue()
        self._awaiting_reply = False
        self._awaiting_confirmation = False
        self._awaiting_stop = False
        # "after the work has been done, it should ask the user, Hey boss,
        # How do you rate my work out of 10". Holds the context of the job
        # being rated, or None. Deliberately NOT reusing _awaiting_reply:
        # that routes into a queue a waiting coroutine sits on, and nothing
        # is waiting here - the question is asked after the turn is over.
        self._pending_rating: dict | None = None
        # The action + destination he named this turn, if he named them.
        self._plan = planning.Plan()
        # Last thing he said, for re-attaching a continuation fragment —
        # see the stitching block in process().
        self._last_user_text = ""
        self._last_user_at = 0.0
        # The last answer that was cut short for length, so he can ask
        # to hear the rest.
        self._last_full_text = ""

        self.end_phrases = [p.lower() for p in cfg.get_path("conversation.end_phrases", [])]
        self.kill_phrases = [p.lower() for p in cfg.get_path("safety.kill_phrases", [])]
        self.address = cfg.get_path("identity.address_user_as", "")

        # Not orb.set_state directly: the speaker is one input among
        # several. See _refresh_orb.
        self._orb_listening = False
        self.speaker.on_state = self._on_speaker_state

        # Let the brain ask him a question and wait for the answer. The
        # tool layer gets exactly this one capability rather than a
        # reference to the whole app.
        from .tools import interaction
        interaction.install(
            lambda question, timeout_s: self._run_coro(
                self.ask_user(question, timeout_s)
            )
        )

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
            self._last_full_text = full
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
        """
        Yes, no, or "that was not an answer".

        THIS WAS A LIST OF EXACT STRINGS, and it cost him a send. In the log:

            Jalen:  "send email: antonis@gmu.edu. Confirm?"
            He:     "Of course."
            Jalen:  "No answer, so I've cancelled it."

        "Of course" was not in the list, so an unmistakable yes became a
        timeout. His words afterwards: "as long as I am showing any kind of
        agreement, it should confirm you know". He is right, and exact-match
        was always going to lose this fight - people do not answer a
        confirmation from a menu.

        So: NEGATIVES ARE CHECKED FIRST, then agreement is looked for
        anywhere in the sentence rather than as the whole of it. The order
        matters more than the lists do - "no, don't send it" contains "send
        it", and a yes-first search would send the email he just refused.
        """
        low = " " + re.sub(r"[^a-z0-9' ]", " ", text.strip().lower()) + " "
        low = re.sub(r"\s+", " ", low)
        if not low.strip():
            return None

        # --- NO, and first, because a refusal usually mentions the action ---
        for phrase in (
            " no ", " nope ", " nah ", " don't ", " dont ", " do not ",
            " stop ", " cancel ", " abort ", " no thanks ", " never mind ",
            " nevermind ", " wait ", " hold on ", " not yet ", " not now ",
            " forget it ", " leave it ", " skip it ", " negative ",
            " definitely not ", " absolutely not ", " i'd rather not ",
        ):
            if phrase in low:
                return False

        # --- YES, anywhere in the sentence ---
        for phrase in (
            " yes ", " yeah ", " yep ", " yup ", " ya ", " sure ", " ok ",
            " okay ", " okey ", " of course ", " course ", " certainly ",
            " definitely ", " absolutely ", " please do ", " do it ",
            " go ahead ", " go on ", " go for it ", " send it ", " send them ",
            " confirm ", " confirmed ", " correct ", " affirmative ",
            " that's right ", " thats right ", " exactly ", " right ",
            " carry on ", " continue ", " proceed ", " fine ", " alright ",
            " all right ", " why not ", " i agree ", " agreed ", " approve ",
            " approved ", " sounds good ", " perfect ", " great ", " good ",
            " let's do it ", " lets do it ", " make it happen ", " yes please ",
        ):
            if phrase in low:
                return True
        return None

    # ---------------------------------------------------------------- handling
    async def ask_user(self, question: str, timeout_s: float = 180.0) -> str:
        """
        Say a question and wait for a spoken answer. Blocks the turn.

        THREE MINUTES, not the twenty seconds a yes/no gets. He asked for
        exactly this: "after asking the question, it might take some time,
        and it should not execute anything until I give my answer to it."
        Looking up a passport number or a referee's email is not a
        twenty-second job, and a form half-filled with a guess is worse
        than one that waited.

        Returns "" if he never answers, and the caller must treat that as
        "stop", never as "carry on without it".
        """
        self.orb.set_state("blocked")
        self.say_blocking(question)
        self._awaiting_reply = True
        # Drain first: anything already queued predates the question and is
        # an answer to something else.
        while not self._reply_q.empty():
            try:
                self._reply_q.get_nowait()
            except queue.Empty:
                break
        try:
            answer = await asyncio.get_running_loop().run_in_executor(
                None, self._wait_for_reply, timeout_s
            )
        finally:
            self._awaiting_reply = False
            self.orb.set_state("thinking")
        if not answer:
            self.say_blocking("No answer, so I've left that one.")
            return ""
        return answer

    def _wait_for_reply(self, timeout: float) -> str:
        try:
            return self._reply_q.get(timeout=timeout)
        except queue.Empty:
            return ""

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
        if tool == "jalen_read_all":
            # No cap. He asked for this by name: "it should have read that
            # aloud till the end". The length limit is a default for answers
            # he did not ask to hear in full, not a rule about what he is
            # allowed to hear.
            if not self._last_full_text:
                return "There's nothing on screen I cut short."
            threading.Thread(
                target=self.speaker.say, args=(self._last_full_text,),
                daemon=True, name="jalen-read-all",
            ).start()
            return None
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

        # NOT hardcoded "user" any more. A router-matched intent is usually
        # his - but a habit recalled while an email was on screen is not
        # automatically his, and this must be the same check the brain path
        # makes or the router becomes the way around the guard.
        verdict = self.safety.classify(tool, intent.args,
                                       origin=taint.origin_now())
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
        # "read my email out loud" removes the cap for THIS turn only. The
        # cap is a default about length, not a rule about what he may hear.
        read_it_all = wants_it_all(user_text)
        max_spoken = float("inf") if read_it_all else self.speaker.max_spoken
        state = {"chars": 0, "overflowed": False}

        def on_text(chunk: str) -> None:
            # Spec B15 still applies: long answers are summarised aloud and
            # shown in full on screen. Streaming means enforcing that as the
            # text arrives rather than measuring a finished string.
            #
            # THE CHUNK THAT CROSSES THE LIMIT IS SPOKEN, NOT DISCARDED. It
            # used to be thrown away whole and replaced with the "short
            # version" line, which is why he reported Jalen showing him the
            # transcript window and then stopping "after 2 words": the model
            # does not stream one character at a time, and a single chunk can
            # be a whole paragraph. If the FIRST chunk was over the limit,
            # literally nothing of the answer was spoken — just the apology
            # for not speaking it.
            #
            # Now it speaks up to the limit, cut at a sentence end so it
            # stops on a full stop rather than mid-word.
            if state["overflowed"]:
                return
            room = max_spoken - state["chars"]
            state["chars"] += len(chunk)
            if state["chars"] <= max_spoken:
                stream.push(chunk)
                return

            state["overflowed"] = True
            head = chunk[:room] if room > 0 else ""
            cut = max(head.rfind("."), head.rfind("!"), head.rfind("?"))
            if cut > 0:
                head = head[: cut + 1]
            if head.strip():
                stream.push(head)
            stream.push(" That's the short version, the full text is on screen.")

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

        # On screen whenever there is enough of it to be worth reading back,
        # not only when speech was cut short. He asked for a full read AND
        # complained the text never appeared; those are two requests, and
        # satisfying the first must not cancel the second.
        if state["overflowed"] or len(reply) > self.speaker.max_spoken:
            self.transcript.show("Full answer", reply)
            # Kept so "read it all" can speak the part he did not hear. The
            # cap exists because a forty-second monologue is unbearable, not
            # because the rest of the answer is worthless.
            self._last_full_text = reply
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

        # HE SPOKE. Everything Jalen read before this moment stops being in
        # play - this is the only event that can honestly mean "this is his
        # instruction, not a web page's". Deliberately the single caller.
        taint.he_asked_again()

        # WHAT "IT" MEANS. Expanded before anything routes, because "send it
        # to Saved Messages" reaching the router as literally "it" is how a
        # request becomes a guess. Conservative: only expands when there IS a
        # remembered subject, never invents one.
        expanded = conversation.expand_references(text)
        if expanded != text:
            self.audit.write("system",
                             summary=f"read '{text[:60]}' as '{expanded[:80]}'")
            text = expanded

        # THE CONTRACT FOR THIS TURN. He said send, or draft, or save, and to
        # where. Kept so that what actually runs can be compared against what
        # he asked for - see _check_the_plan below.
        self._plan = planning.read_plan(text)

        self.audit.utterance(text, who="user")

        low = text.lower().rstrip(".!?")

        # kill switch always wins
        if low in self.kill_phrases:
            self.speaker.stop()
            self.kill.set()
            self._answer_q.put(False)
            return

        # A pending open QUESTION takes everything he says as the answer.
        # Checked before the yes/no branch and before the router, because
        # while a question is open there is no other interpretation: "open
        # chrome" said in answer to "what's your phone number" is an answer,
        # not a command, and routing it would be both wrong and irreversible.
        # A rating he was just asked for. Before _awaiting_reply because
        # nothing is waiting on a queue here, and before the router because
        # a bare "eight" would otherwise route to whatever "eight" matches.
        if self._pending_rating is not None:
            if time.time() - self._pending_rating.get("asked_at", 0) > self.RATING_EXPIRES_S:
                self._pending_rating = None      # he moved on; so do we
            elif self._take_rating(text):
                return

        # "YES, GO ON."
        #
        # His complaint: "I said yes go on, but it has stopped man, it should
        # have a consistent memory." It had none - "go on" matched no rule,
        # meant nothing to the brain without context, and the turn ended.
        #
        # Checked AFTER the pending-question branches below would have caught
        # a real answer, and before the router, because "go on" is neither a
        # command nor a new request: it is a reference to something already
        # under way.
        if conversation.is_continuation_request(text) and not (
            self._awaiting_reply or self._awaiting_confirmation
        ):
            resumed = self._resume_something()
            if resumed is not None:
                self.say(resumed)
                return

        if self._awaiting_reply:
            self._reply_q.put(text)
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

        # A HABIT: the same sentence, decided the same way, three times.
        #
        # Worth 1.2 seconds at the median and 2.2 at the 95th, measured
        # against his own sessions - not the dramatic saving it looks like,
        # because most of a turn is speech recognition and synthesis. What it
        # buys beyond the second is an API call not made, and the same
        # sentence producing the same action every time. A model asked twice
        # can answer differently the second time, and "it did something
        # different this time" is a bug nobody can reproduce.
        #
        # It goes through handle_local, which classifies it through the
        # safety engine exactly like a fresh intent. Being fast is never a
        # reason to skip a confirmation he would otherwise have been asked.
        remembered = habits.recall(text)
        if remembered is not None:
            tool, args = remembered
            if self._turn_timer is not None:
                self._turn_timer.route = "router"
            reply = self.handle_local(Intent(tool=tool, args=args, reply=None))
            if reply is not None:
                habits.note_use(text)
                self.say(reply)
                return
            # handle_local declined it - not a local tool after all, or the
            # safety engine refused. Fall through to the brain rather than
            # leaving him with silence.

        self.orb.set_state("thinking")
        brain_started = time.time()
        self.speak_brain_reply(text)
        self._learn_from(text, brain_started)

    def _learn_from(self, text: str, started_at: float) -> None:
        """
        Remember what the brain just decided, if it is worth remembering.

        ONE tool only. A multi-step plan is precisely where the brain is
        earning its cost, and replaying one from memory is how last week's
        email reaches this week's person.
        """
        try:
            calls = systools.calls_since(started_at)
            if len(calls) != 1:
                return
            tool, args = calls[0]
            habits.remember(text, tool, args, seconds=time.time() - started_at)
        except Exception as exc:  # noqa: BLE001
            self.audit.error("habits", exc)

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
        barge_threshold = float(self.cfg.get_path("conversation.barge_in_threshold", 0.75))
        barge_grace_s = float(self.cfg.get_path("conversation.barge_in_grace_s", 1.2))
        barge_frames = int(self.cfg.get_path("conversation.barge_in_frames", 6))
        barge_run = 0                    # consecutive frames over the threshold
        # Barge-in has already fired for the CURRENT playback. Re-armed when
        # the speaker actually falls silent - see the latch in the loop.
        barge_fired = False

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
            started_at = time.time()
            task_id = conversation.start_task(text[:80], conversation.THINKING)
            try:
                with systools.com_initialized():
                    self.process(text)
                conversation.update_task(task_id, state=conversation.COMPLETED)
            except BaseException as exc:      # noqa: BLE001
                conversation.update_task(task_id, state=conversation.FAILED,
                                         error=f"{type(exc).__name__}: {exc}")
                raise
            finally:
                self._await_playback()
                self._finish_timing(timer)
                with self._turn_lock:
                    self._active_turns.discard(turn_id)
                self._check_the_plan(started_at)
                self._maybe_ask_for_a_rating(text, started_at)
            # Only the newest turn owns the UI state and the follow-up window.
            # Without this, a slow turn finishing late would reopen the
            # follow-up window and reset the orb long after the user moved on.
            if turn_id == self._turn_seq:
                if self.cfg.get_path("conversation.follow_up", True):
                    follow_up_until = time.monotonic() + follow_up_s
                # Recomputed, not assumed: another turn may still be working,
                # and telling him it is idle while it is not is the same lie
                # in the other direction.
                self._refresh_orb()

        self.prewarm()

        frames_seen = 0
        with self.mic:
            for frame in self.mic.frames():
                # `python run.py --stop` (and the spoken "quit") set this. We
                # check it here, in the loop that owns the microphone, so the
                # device and the audit DB get released properly — a hard kill
                # from outside leaves the mic held.
                #
                # The two conditions are recorded separately even though they
                # do the same thing. "He said quit" and "something outside
                # this process asked us to stop" are different events, and
                # telling them apart afterwards is the whole point of
                # _exit_reason — see jarvis/crashlog.py.
                if self._quit.is_set():
                    self._exit_reason = "quit-requested"
                    break
                if runtime.stop_requested():
                    self._exit_reason = "stop-file"
                    break

                # The global hotkey (scripts/hotkeys.py) is a separate
                # process and talks to us through a sentinel file. Polled
                # every ~10 frames rather than every frame: at 32ms a frame
                # that is a stat() call 31 times a second forever, and a
                # third of a second is imperceptible for a key press.
                frames_seen += 1

                # "The agent might think for an hour... as soon as it has
                # finished, it should take the lead." Without this, a job
                # that finished an hour ago is only noticed when he happens
                # to ask — which is the whole thing he was complaining about.
                #
                # Polled on the mic loop rather than pushed from the worker
                # thread on purpose: this loop already owns speech, so an
                # announcement made from here cannot collide with a turn in
                # flight. Every ~5s at 32ms a frame.
                if frames_seen % JOB_POLL_FRAMES == 0:
                    self._announce_finished_jobs()

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

                # barge-in: you talking beats Jalen talking.
                #
                # THIS IS WHERE JALEN WAS INTERRUPTING HIMSELF. Measured in
                # data/audit.jsonl: 17 replies ended far earlier than their
                # text needed, and six of them stopped at 747-764ms — the
                # same instant every time. Nothing random clusters like that.
                # What happens ~750ms into playback is Jalen's own voice
                # arriving back through the microphone, the VAD crossing the
                # threshold, and stop() firing on the first frame over it.
                #
                # There is no acoustic echo cancellation here, so the
                # microphone genuinely cannot tell his voice from the
                # speakers. Three cheap defences instead, and they compose:
                #
                #   GRACE      ignore the opening of playback entirely. The
                #              echo takes a moment to build, and nobody
                #              interrupts before the first word anyway.
                #   SUSTAINED  require several CONSECUTIVE frames, not one.
                #              A single 32ms blip must not kill a 60-second
                #              answer, and that is exactly what it was doing.
                #   THRESHOLD  higher while speaking than while listening.
                #
                # Real speech over the top clears all three in about a fifth
                # of a second, which still feels immediate.
                if not self.speaker.speaking:
                    # Playback is over, so the latch below re-arms for the
                    # next reply.
                    barge_fired = False

                if barge_in and self.speaker.speaking:
                    if self.speaker.speaking_for() < barge_grace_s:
                        barge_run = 0
                        continue
                    if self.vad.probability(frame) >= barge_threshold:
                        barge_run += 1
                    else:
                        barge_run = 0
                    if barge_run < barge_frames:
                        continue
                    barge_run = 0

                    # ONCE PER REPLY, not once per six frames.
                    #
                    # Seen live on 22 August: barge-in fired 22 times in
                    # seven seconds, at exactly 192 ms intervals — which is
                    # barge_frames x frame_ms, i.e. the moment the counter
                    # refilled. `_speaking` stays set while the stream waits
                    # for the model to produce the NEXT sentence, so between
                    # sentences the microphone is open, nothing is playing,
                    # and every continuous noise re-triggered the whole
                    # branch: 22 calls to stop(), 22 audit lines, and the
                    # collector reset out from under itself each time.
                    #
                    # The first firing already did everything that matters.
                    # This latch makes the rest no-ops until playback
                    # genuinely ends.
                    if barge_fired:
                        continue
                    barge_fired = True
                    self.audit.write(
                        "system",
                        summary=(
                            f"barge-in stopped playback after "
                            f"{self.speaker.speaking_for():.1f}s"
                        ),
                    )
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
                    awaiting_reply = (
                        self._awaiting_confirmation or self._awaiting_stop
                        or self._awaiting_reply
                    )
                    if (in_follow_up or awaiting_reply) and self.vad.probability(frame) >= self.vad.threshold:
                        listening = True
                        wake_initiated = False   # a sound opened this, not him
                        # _refresh_orb, NOT set_state("listening"). This is
                        # the line he saw: a noise during the follow-up window
                        # painted the orb blue while a turn was still working,
                        # for as long as the turn took.
                        self._refresh_orb(listening=True)
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

                if not self.should_act_on(text, wake_initiated):
                    self.audit.write(
                        "system",
                        summary="ignored - not addressed to Jalen",
                        detail={"heard": text[:200]},
                    )
                    self._refresh_orb()
                    continue

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
                    self._refresh_orb()
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

        # Falling out of the loop without a reason means the mic generator
        # itself ended: Microphone.frames() stops yielding the moment
        # _running is cleared, and does it by returning normally. Before this
        # line that was indistinguishable from a clean quit — the loop simply
        # ended and run() returned, with the same (empty) evidence either
        # way. It is now a named cause, because "the audio device went away
        # underneath us" and "he said quit" deserve different answers.
        if self._exit_reason is None:
            self._exit_reason = "mic-stream-ended"
        self.audit.write(
            "system",
            summary=f"mic loop ended: {self._exit_reason}",
            detail={"frames_seen": frames_seen, "dropped_frames": self.mic.dropped},
        )

    # ------------------------------------------------------------------ orb
    #
    # ONE PLACE DECIDES WHAT THE ORB SHOWS.
    #
    # He reported it plainly: "it is saying I'm on the last one, but the orb
    # is orbiting like in blue... should not it have been green while it is
    # working?" He was right, and the cause was that six different places
    # called orb.set_state() and the last one to fire won.
    #
    # In the log: a turn is dispatched, the orb is set to `thinking`, and
    # then ANY sound in the room takes the follow-up branch, sets
    # `listening`, and the orb sits blue for the three minutes the brain
    # spends working. The state he could see had nothing to do with what it
    # was doing.
    #
    # So the states are now a PRIORITY ORDER, computed from what is actually
    # true, and every caller asks for a refresh rather than asserting a
    # colour. The order is the one he described:
    #
    #     muted/paused  it is switched off
    #     speaking      it is talking to you           GREEN
    #     working       a turn is in flight            YELLOW
    #     listening     a window is open for you       BLUE
    #     idle          nothing is happening           TEAL
    #
    # "Working beats listening" is the whole fix: while it is thinking, a
    # noise in the room must not make it look like it is waiting for you.
    ORB_PRIORITY = ("muted", "speaking", "thinking", "listening", "idle")

    # ---------------------------------------------------------- who counts
    #
    # "It should not respond to its own voice, or any other noise that is
    # happening or disrupting the flow of AI... it should only respond to
    # messages starting with either Jalen or Hey Jalen."
    #
    # Everything upstream of here is acoustic, and acoustics cannot answer
    # the question. The microphone genuinely cannot separate his voice from
    # the television, from a podcast, or from Jalen's own reply arriving
    # back through the speakers. Voice activity detection only ever knew
    # "that was speech" — and all three of those are speech. That is how a
    # 20-second answer could end with "I didn't catch that" said to a person
    # who had not spoken at all.
    #
    # By this point the audio is a SENTENCE, so the test can be about words
    # rather than volume: a sentence that does not begin with his name was
    # not addressed to him. No model, no threshold, and nothing a loud room
    # can defeat.
    def should_act_on(self, text: str, wake_initiated: bool) -> bool:
        """
        Three ways through, and the middle one is the one he chose when asked.

        wake word fired     he said "hey Jalen" out loud, so the name is in
                            the AUDIO and will not be in the transcript
        Jalen just asked    "which file did you mean?" — requiring the name
                            to answer a question he was this moment asked
                            would be absurd, and it is the only reason the
                            follow-up window still exists
        starts with a name  everything else, in any pronunciation

        Rejection is SILENT at the call site, deliberately. Announcing "I
        didn't catch that" to a room that was not talking to him is the
        exact self-inflicted interruption this exists to end — and the
        announcement is itself speech, which the microphone hears, which can
        trip the window open again.
        """
        if wake_initiated:
            return True
        if self._awaiting_confirmation or self._awaiting_stop or self._awaiting_reply:
            return True
        if self._pending_rating is not None:
            return True
        # STILL THE SAME SENTENCE.
        #
        # He gets cut off at the fast endpoint, keeps talking, and the rest
        # arrives as its own utterance a second later. Without this, the gate
        # threw that fragment away for not starting with his name - which is
        # exactly the "why do I have to keep repeating hey Jalen" complaint,
        # and it was a hole I opened when I added the gate.
        #
        # Narrow on purpose: it is not "anything within N seconds". Either
        # the fragment OPENS like a continuation ("and also...", "to my
        # channel") or what he said last ENDED like one ("...send it to").
        # Noise satisfies neither.
        if self._continues_last_utterance(text):
            return True
        # THE EMERGENCY STOP IS EXEMPT, and it has to be.
        #
        # "stop", "cancel" and "abort" are safety.kill_phrases — the thing
        # you say when Jalen is doing something you want stopped NOW. Making
        # them wait for his name would mean the one command you need under
        # pressure is the one with an extra hurdle in front of it.
        #
        # This does reopen a noise path: a television saying "stop" can stop
        # him. That trade is not close. Stopping is instantly reversible and
        # costs a sentence; an assistant that will not stop when told costs
        # rather more, and barge-in already halts playback on any loud noise
        # whatsoever.
        if (text or "").lower().strip().rstrip(".!?") in self.kill_phrases:
            return True
        return addressed_to_jalen(text)

    def _continues_last_utterance(self, text: str) -> bool:
        """Is this the rest of the sentence he was already saying?"""
        window = float(self.cfg.get_path("conversation.stitch_window_s", 8))
        if not self._last_user_text:
            return False
        if time.monotonic() - self._last_user_at > window:
            return False
        return is_continuation(text) or looks_unfinished(self._last_user_text)

    # -------------------------------------------------------- what's going on
    def _resume_something(self) -> "str | None":
        """
        "Go on" — carry on with what, exactly.

        Returns what to say, or None to let the turn route normally. None
        matters: "go on" with nothing pending is not a failure, it is just a
        sentence, and swallowing it would be worse than passing it through.
        """
        proposal = conversation.last_proposal()
        if proposal is not None:
            conversation.clear_proposal()
            return (f"Carrying on with {proposal.describe()}."
                    if proposal.describe() else None)

        chosen = conversation.which_task("")
        if isinstance(chosen, conversation.Task):
            if chosen.state in conversation.WAITING_STATES:
                return (f"I'm still on {chosen.objective} — waiting for "
                        f"{chosen.waiting_reason or 'the other side'}.")
            return f"Still going on {chosen.objective}. {chosen.progress or ''}".strip()
        # A string means either nothing running, or more than one and it
        # wants to know which. Both are honest answers to "go on".
        return chosen if "Which one" in chosen else None

    def _say_progress(self, text: str = "") -> str:
        """"What are you doing?" / "how far are you?" with a real answer."""
        return conversation.describe_progress(text)

    def _check_the_plan(self, started_at: float) -> None:
        """
        Did the turn do what he asked, or something adjacent?

        From the log: he said "send them in my saved messages" and Jalen ran
        save_telegram_draft. He had to ask twice. The failure is not that a
        draft is a bad idea - it is that he said send.

        Reported rather than corrected. Automatically re-running it as a
        send would be a second guess on top of the first, and the one thing
        worse than doing the wrong thing is doing it twice.
        """
        current = getattr(self, "_plan", None)
        if current is None or not current.specific:
            return
        try:
            used = systools.tools_since(started_at)
            wrong = current.betrayed_by(used)
            if not wrong:
                return
            self.audit.write(
                "system",
                summary=f"plan mismatch: asked to {current.describe()}, ran {wrong}",
                detail={"asked": current.text[:200], "tools": used},
            )
            self.say(planning.complaint(current, wrong))
        except Exception as exc:  # noqa: BLE001
            self.audit.error("plan-check", exc)

    # ------------------------------------------------------------- feedback
    RATING_EXPIRES_S = 300.0

    def _maybe_ask_for_a_rating(self, about: str, started_at: float) -> None:
        """
        Ask how it went, but only after something worth having an opinion on.

        The restraint IS the feature. An assistant that asks for a score
        after "what's the time" is not collecting feedback, it is collecting
        resentment - and the numbers it gets back are worthless anyway,
        because nobody thinks about a question they are asked forty times a
        day. feedback.worth_asking_about() says no by default.

        Asked AFTER playback has finished, never before: talking over the
        answer he is still listening to, in order to ask him to rate it,
        would be its own small insult.
        """
        if self.muted or self.paused or self._pending_rating is not None:
            return
        try:
            from .tools import feedback

            used = systools.tools_since(started_at)
            if not feedback.worth_asking_about(used, time.time() - started_at):
                return
            self._pending_rating = {
                "about": about,
                "did": ", ".join(dict.fromkeys(used)) or "(no tools)",
                "asked_at": time.time(),
            }
            self.say(feedback.the_question())
        except Exception as exc:  # noqa: BLE001
            # Feedback is a nicety. It must never take a turn down with it.
            self.audit.error("rating-prompt", exc)
            self._pending_rating = None

    def _take_rating(self, text: str) -> bool:
        """
        Treat this utterance as the answer to "how do you rate my work".

        Returns True if it was consumed. A NUMBER is required: if he said
        something else, that was a new request rather than a rating, so it
        falls through to the router and the question is dropped. Asking a
        second time would be worse than never asking.
        """
        pending, self._pending_rating = self._pending_rating, None
        if pending is None:
            return False
        try:
            from .tools import feedback

            score = feedback.parse_rating(text)
            if score is None:
                return False
            self.say(feedback.record_rating(
                score=score, comment=text,
                about=pending.get("about", ""), did=pending.get("did", ""),
            ))
        except Exception as exc:  # noqa: BLE001
            self.audit.error("rating", exc)
            self.say("Thanks - I couldn't file that, but I heard you.")
        return True

    def _refresh_orb(self, listening: bool = False) -> None:
        """Recompute what the orb should show from what is actually true."""
        if self.muted or self.paused:
            state = "muted"
        elif self.speaker.speaking:
            state = "speaking"
        elif self._active_turns:
            state = "thinking"
        elif listening or self._awaiting_reply or self._awaiting_confirmation:
            state = "listening"
        else:
            state = "idle"
        self._orb_listening = listening
        self.orb.set_state(state)

    def _on_speaker_state(self, state: str) -> None:
        """
        The speaker changing state is INPUT to the decision, not the decision.

        Wired straight to orb.set_state before, which meant the end of a
        sentence set the orb to `idle` while the turn behind it was still
        working — the same bug from the other direction.
        """
        if state == "speaking":
            self.orb.set_state("muted" if self.muted else "speaking")
            return
        self._refresh_orb(self._orb_listening)

    def _announce_finished_jobs(self) -> None:
        """
        Speak up when a background coding agent finishes.

        Says only that it finished and how to look at it — NOT whether it
        went well. Judging that needs the diff read against what he asked
        for, which is a brain turn (review_coding_job), and starting one
        unprompted would mean an agent finishing at 3am wakes the machine up
        talking. He asks, and then it takes the lead.

        Never speaks over a turn in flight or a pending question: the
        announcement waits for the next poll instead. An interruption is
        exactly what he has been complaining about.
        """
        try:
            from .tools import devwork

            if (
                self.muted
                or self.paused
                or self.speaker.speaking
                or self._active_turns
                or self._awaiting_confirmation
                or self._awaiting_stop
                or self._awaiting_reply
            ):
                return
            done = devwork.finished_unreported_jobs()
        except Exception as exc:  # noqa: BLE001 - a poll must never kill the loop
            crashlog.write(f"job poll failed: {type(exc).__name__}: {exc}")
            return

        for job in done:
            minutes = (job.get("ended_at", 0) - job.get("started_at", 0)) / 60
            folder = Path(str(job.get("folder", ""))).name
            state = job.get("state")
            if state == "finished" and job.get("summary"):
                # A background COMMAND (the self-test) already knows how to
                # describe its own result, so say that rather than "go and
                # review it" — there is nothing to review, the answer is the
                # sentence.
                line = f"{job.get('prompt', 'That')} finished: {job['summary']}"
            elif state == "finished":
                line = (
                    f"The coding job in {folder} just finished after "
                    f"{minutes:.0f} minutes. Say review the coding job and "
                    "I'll tell you what it actually changed."
                )
            elif state == "timeout":
                line = f"The coding job in {folder} ran out of time after {minutes:.0f} minutes."
            else:
                line = f"The coding job in {folder} failed: {job.get('error', 'no reason given')}."
            self.audit.write("system", summary=f"coding job {job.get('id')}: {state}",
                             detail={"folder": str(job.get("folder", ""))})
            self.say(line)

    def shutdown(self, reason: str = "") -> None:
        """
        Tear down, recording WHY first and surviving a failure in any step.

        The ordering here is the fix for a real hole. This method used to
        call speaker.stop(), orb.stop() and mic.stop() and only THEN write
        "Jalen stopped" — so any one of those three raising took the audit
        line down with it, and the process disappeared leaving no record it
        had ever stopped. That is one of the three reasons the 21 August
        exit was undiagnosable (jarvis/crashlog.py has the other two).

        So: the reason is written to disk and to the audit BEFORE anything
        that can fail, and every teardown step gets its own try/except so a
        broken speaker cannot also cost you the microphone release.
        """
        reason = reason or self._exit_reason or "shutdown"
        crashlog.note_exit(reason, session_id=self.session_id, mode=self.mode)
        self.running.clear()

        # Written first, and separately from close(), so that the line
        # exists even if every single teardown step below throws.
        try:
            self.audit.write("system", summary=f"Jalen stopped ({reason})")
        except Exception:
            pass

        for label, step in (
            ("speaker", self.speaker.stop),
            ("orb", self.orb.stop),
            ("mic", self.mic.stop),
        ):
            try:
                step()
            except Exception as exc:
                # Report it and keep going. A failure to stop the speaker is
                # not a reason to leave the microphone device held open.
                crashlog.write(f"shutdown step {label!r} failed: {type(exc).__name__}: {exc}")
                try:
                    self.audit.error(f"shutdown.{label}", exc)
                except Exception:
                    pass

        if self.brain is not None and self._loop is not None:
            try:
                self._run_coro(self.brain.stop())
            except Exception:
                pass
        try:
            self._stop_loop()
        except Exception as exc:
            crashlog.write(f"shutdown step 'loop' failed: {type(exc).__name__}: {exc}")
        try:
            self.audit.close()
        except Exception:
            pass
