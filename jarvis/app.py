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
from dataclasses import dataclass
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
from .brain.router import (
    Intent, IntentRouter, NAME_ACK, addressed_to_jalen, is_kill_phrase,
    solicits_an_answer, widened_door, without_a_misheard_name,
)
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

# ---------------------------------------------------------------------------
# A DANGLING TAIL IS NOT A BLANK CHEQUE.
#
# _continues_last_utterance used to end on
#
#     return is_continuation(text) or looks_unfinished(self._last_user_text)
#
# and the second half never looks at `text` at all. So for the whole of
# conversation.stitch_window_s — eight seconds — after any utterance ending
# on a word in _UNFINISHED_TAIL, the address gate admitted ANYTHING.
# Reproduced against the real predicate with _last_user_text="send it to"
# and nothing pending; every one of these returned True:
#
#     "transfer the funds"                  "empty the recycle bin"
#     "Julian said he'd call back"          "asdf qwerty zxcv"
#     ""                                    <- the empty string
#     "Sure, I can open that for you."      <- and this one is the problem
#
# That last string is pinned in tests/test_address_gate.py as JALEN'S OWN
# VOICE coming back through the speakers, so this was a live self-triggering
# path inside the gate whose entire purpose is refusing it.
#
# TWO CLASSES OF DANGLING WORD, and only one of them can be made safe by
# looking at the fragment. A tail ending on a PREPOSITION or a DETERMINER is
# waiting for a noun — "send it to ..." can only be followed by a noun
# phrase, and a noun phrase has a small, recognisable set of openers. A tail
# ending on a CONJUNCTION or an auxiliary ("open chrome and ...") is waiting
# for a verb phrase, which looks exactly like a new command and cannot be
# told from one. So the clause now fires only for the first class.
#
# The cost is the second class: "open chrome and" [pause] "go to youtube"
# now needs his name. That case is already caught an entire layer earlier,
# acoustically — run() sees looks_unfinished() on the fast transcript and
# calls collector.resume() to keep listening on the patient endpoint, so the
# two halves arrive as ONE utterance and never reach this predicate. This
# clause is the backstop for when that fails, and a backstop that admits the
# assistant's own voice is worse than no backstop.
_NOUN_EXPECTING_TAIL = re.compile(
    r"(?:^|\s)(?:to|for|with|from|into|onto|at|in|on|of|about|by|as|"
    r"the|a|an|my|your|his|her|their|its|this|these)$"
)

# What the rest of a noun phrase can start with. Deliberately closed and
# deliberately small: every word here is one that cannot begin a command.
# "you" is absent although "your" is present, because "You have four unread
# emails" is Jalen talking.
_FRAGMENT_OPENER = re.compile(
    r"^(?:the|a|an|my|your|his|her|its|our|their|this|that|these|those|"
    r"to|for|with|from|into|onto|at|in|on|of|about|by|as|"
    r"it|them|him|us|me|"
    r"one|two|three|four|five|first|second|third|both|either|neither)\b"
)


def opens_like_a_fragment(text: str) -> bool:
    """True when `text` reads as the rest of a noun phrase, not a new command."""
    return bool(_FRAGMENT_OPENER.match((text or "").strip().lower()))


def expects_a_noun(text: str) -> bool:
    """True when the transcript breaks off waiting for a noun phrase."""
    cleaned = (text or "").strip().lower().rstrip(".,!?;:")
    if not cleaned:
        return False
    return bool(_NOUN_EXPECTING_TAIL.search(cleaned))


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

# How fast Jalen actually talks, in characters per second.
#
# MEASURED, not estimated. 320 characters through en-US-AndrewNeural at
# +18% returns 85,824 bytes of 48 kbps mp3 = 14.3 seconds of audio, so
# 22.4 chars/sec. The previous figure of 14.0 was a guess and it made every
# duration estimate 60% too long.
#
# Re-measure with scripts/ if the voice or the rate changes: both move this
# number, and it is the only thing standing between him and a four-minute
# answer he was not warned about.
SPOKEN_CHARS_PER_SECOND = 22.4

# Words, for comparing what the microphone heard against what Jalen just
# said. Apostrophes are kept so "don't" is one word rather than two, and
# everything else — punctuation, the em dashes the model likes, the stray
# unicode speech recognition emits — is a separator.
_SPOKEN_WORD = re.compile(r"[\w']+", re.UNICODE)

# HOW LONG JALEN'S OWN VOICE CAN STILL BE ARRIVING.
#
# Echo is an ACOUSTIC event with a physical bound, and that bound is the
# only thing that reliably separates it from an answer. Text cannot: when
# Jalen asks "the whole thread read out, or just the last message?", the
# natural answer is one of those phrases, so the answer IS a substring of
# the question by construction. Measured against the real log, that is
# invisible — before the question window existed nobody could answer
# without saying the name, so the corpus contains almost no bare
# option-answers to be wrong about.
#
# Three seconds covers the tail of playback plus whatever the microphone
# had buffered. A person answering a question takes longer than that
# essentially always; a speaker bleeding into a microphone never does.
ECHO_TAIL_S = 3.0

# HOW LONG AFTER AN ANSWER WINDOW CLOSES A SENTENCE THAT BEGAN INSIDE IT CAN
# STILL ARRIVE. See Jalen._window_outlived_by_the_sentence.
#
# The window is judged when he BEGAN to speak (run() remembers it), and the
# sentence reaches the address gate only after it has ended and been
# transcribed. One utterance is cut at vad.max_utterance_s (30), and that
# buffer includes the trailing silence, so a sentence that began on the last
# tick of the window ends at most that long after it closes; then it is
# transcribed. That last step is `heard=` on the timing rows: 1.8s median,
# 4.4s at p95 and 36s at the maximum over the 349 timing rows in
# data/audit.jsonl. Five seconds covers the p95; the 36s case was a local
# fallback transcription and is refused - which is the behaviour before this
# existed, not a new failure.
ANSWER_STT_SLACK_S = 5.0

# HOW LONG AFTER HIS VOICE STOPS A SENTENCE MADE OF IT CAN STILL REACH THE GATE.
# The echo test for every way in that has no name to vouch for it (the three
# doors of router.all_doors, and an answer that outlived its window).
#
# It is NOT ECHO_TAIL_S, and the difference is the review's second finding.
# ECHO_TAIL_S is how long the SOUND lingers in the room; this is how long
# after the sound stopped the TEXT can arrive at the gate, because the gate is
# asked at the end of the pipeline and not at the microphone:
#
#     3.0   the tail in the room (ECHO_TAIL_S, as before)
#   + 4.0   the longest endpoint silence a sentence can wait through - the
#           patient one, vad.silence_ms; the fast one is 1.4
#   + 5.0   transcription: `heard=` on the 349 timing rows in data/audit.jsonl
#           is 1.8s at the median and 4.4s at p95 (ANSWER_STT_SLACK_S)
#   = 12.0
#
# Measured nowhere as a whole - the audio is not kept - so it is the sum of
# three figures that each have a source, and it errs long on purpose: a door's
# own sentences ("could you please ...") are almost never a run of what he has
# just heard, so a long tail costs the doors nothing, whereas the same tail on
# the ANSWER path would refuse "just the last message" to "...or just the last
# message?" for twelve seconds. Jalen.__init__ recomputes it from the shipped
# vad.silence_ms.
ECHO_REACHES_THE_GATE_S = ECHO_TAIL_S + 4.0 + ANSWER_STT_SLACK_S


# Words that turn an answer into a CORRECTION: he is not saying yes to what
# was asked, he is saying what to do instead. Padded with spaces because the
# parser pads the sentence the same way.
_CORRECTION_MARKERS = (" instead ", " actually ", " rather ", " but ",
                       " first ", " change ")


class ConfirmAnswer:
    """
    What he said to a spoken "Confirm?" - truthy ONLY on a real yes.

    Truthiness keeps both callers working unchanged (`if not approved:`), and
    `outcome` says WHICH no it was, because they are not the same thing:

        yes          approve
        no           he refused
        timeout      nobody answered - he may never have heard the question
        correction   he answered with what to do instead: "send it to Rodion
                     instead". `words` carries his sentence, so the model can
                     act on the correction inside the turn that asked, rather
                     than a second turn being started in parallel.

    Before this, every falsy answer reached the model as "He said no. Don't
    retry", so a timeout was reported to him as a refusal he never made
    (2026-08-24T05:10, emptying the recycle bin).
    """

    __slots__ = ("outcome", "words")

    def __init__(self, outcome: str, words: str = "") -> None:
        self.outcome = outcome
        self.words = words

    def __bool__(self) -> bool:
        return self.outcome == "yes"

    def __repr__(self) -> str:
        return f"ConfirmAnswer({self.outcome!r}, {self.words!r})"


@dataclass(frozen=True)
class Expectation:
    """
    Jalen asked him something and is waiting for the answer.

    THE THING THAT WAS MISSING. There were two gates and only one of them
    had ever heard of the follow-up window: `follow_up_until` is a local
    inside run() that decides whether a sound OPENS a microphone window,
    while should_act_on() decides whether the words that came out of it are
    acted on — and being a method, it could not see the local. So the window
    opened, Whisper transcribed, and the address gate dropped the sentence
    for not starting with his name. 45 real answers went that way in
    data/audit.jsonl; see tests/test_answering_a_question.py.

    A flag would not have been enough. This carries what the answer has to
    be checked against:

        question   the exact words Jalen said, so its own voice arriving
                   back through the microphone can be told from his answer.
                   The address gate USED to be the echo defence — Jalen's
                   sentences do not start with his name — and opening a free
                   window removes it exactly when the echo is loudest.
        turn_id    which turn asked. A window left over from three turns ago
                   is identifiable as stale rather than silently answering
                   for the newest question.
        expires_at monotonic, and stamped AFTER playback finished rather
                   than when the model produced the text — otherwise a
                   forty-second answer spends its whole window being spoken.
        kind       "" for a question Jalen really asked; "bare-wake" for the
                   "Yes, Boss?" window a wake word with nothing after it
                   opens. A sentence admitted through the second has no
                   question behind it, only a greeting, so the audit row
                   names it separately and the false accepts of that window
                   can be counted on their own.
    """

    question: str
    turn_id: int
    opened_at: float
    expires_at: float
    kind: str = ""


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
        # The confirmation currently being asked, and when it finished
        # playing - for recognising its own echo. See confirm().
        self._confirm_lock: asyncio.Lock | None = None
        self._confirm_question = ""
        self._confirm_asked_at = 0.0
        self._confirm_reasked = False
        # "after the work has been done, it should ask the user, Hey boss,
        # How do you rate my work out of 10". Holds the context of the job
        # being rated, or None. Deliberately NOT reusing _awaiting_reply:
        # that routes into a queue a waiting coroutine sits on, and nothing
        # is waiting here - the question is asked after the turn is over.
        self._pending_rating: dict | None = None
        # An ordinary question Jalen asked and is waiting on — see the
        # Expectation docstring above. Distinct from _awaiting_reply, which
        # is the ask_user TOOL and has a coroutine sitting on a queue: this
        # one has nobody waiting, because the turn that asked has finished.
        self._expecting: Expectation | None = None
        # The last thing Jalen actually said, kept for two jobs: deciding
        # whether it was a question, and telling his answer apart from
        # Jalen's own voice coming back through the microphone.
        self._last_reply_text = ""
        # WHEN he last said it, because the echo test is a question about
        # time before it is a question about words. See ECHO_TAIL_S.
        self._last_reply_at = 0.0
        # What has been handed to the speaker of a reply that is STILL BEING
        # WRITTEN, a chunk at a time - see _note_voice. A streamed reply is
        # filed as `_last_reply_text` only when it has finished playing, so
        # for the whole time it is on air the gate had the PREVIOUS reply to
        # compare against, and its own voice matched nothing.
        self._voice_so_far = ""
        # Read once, here, not on the gate path - the file's own rule.
        self._answer_window_s = float(
            cfg.get_path("conversation.answer_window_s", 30))
        # How long after his voice stops a sentence made of it can still reach
        # the address gate - the long tail for the doors. See
        # ECHO_REACHES_THE_GATE_S: the room, the longest endpoint, the STT.
        self._door_echo_tail_s = (
            ECHO_TAIL_S
            + float(cfg.get_path("vad.silence_ms", 4000)) / 1000.0
            + ANSWER_STT_SLACK_S)
        # How long past the end of an answer window a sentence that BEGAN
        # inside it can still arrive: the longest one utterance can run, plus
        # the time to transcribe it. See ANSWER_STT_SLACK_S.
        self._answer_overrun_s = float(
            cfg.get_path("vad.max_utterance_s", 30)) + ANSWER_STT_SLACK_S
        # How long he is listened to after a wake word that was followed by
        # nothing - the follow-up length, not the answer window; see
        # _say_yes_and_listen.
        self._follow_up_s = float(
            cfg.get_path("conversation.follow_up_timeout_s", 12))
        # IS THE BRAIN ABLE TO THINK AT ALL? Set when Claude answers with a
        # typed failure - an expired sign-in, a billing problem, a usage
        # limit - rather than an answer. See _latch_brain_down. Without it
        # every turn paid a round-trip to be told the same thing, and read
        # the CLI's error sentence aloud as if it were the answer.
        self._brain_down: str | None = None
        self._brain_down_detail = ""
        self._brain_down_at = 0.0
        self._brain_down_told = False
        # Signing in rewrites this file, which is how a latched brain notices
        # he has fixed it without waiting for a restart or a timer.
        self._credentials_file = Path.home() / ".claude" / ".credentials.json"
        self._credentials_seen = self._credentials_stamp()
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
        # ...and a yes/no, through the same confirm() a RED action gets. For
        # the code that types his secrets: it asks a question naming the
        # site IT read from the address bar, and only his own yes to that
        # question lets it type. fill_credential's approved_once used to be
        # the whole approval - a flag the model set, about a site nobody
        # named. See interaction.confirm.
        interaction.install_confirm(
            lambda question: self._run_coro(self.confirm(question))
        )

        # Per-turn stopwatch. "Why is it so slow" had no answer before this,
        # because the only evidence was the wall-clock gap between his
        # utterance and Jalen's — a number that conflates waiting with
        # Jalen talking. See jarvis/timing.py.
        self.timings = TimingLog()
        self._turn_timer: TurnTimer | None = None
        self.speaker.on_audio_start = self._mark_first_audio

        # Bring the Chrome-extension bridge up now, not on first use: the
        # extension in his everyday Chrome needs the app's loopback server
        # listening in order to connect at all. Cheap - a socket bind, a
        # file, an accept thread - and guarded so a bind failure degrades to
        # "extension unavailable" rather than taking startup down with it.
        try:
            from .bridge.server import get_server
            bridge = get_server()
            bridge.on_event(self._on_bridge_event)
            bridge.start()
        except Exception as exc:  # noqa: BLE001
            self.audit.error("bridge.start", exc)

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
            jobs.append(("brain", lambda: self._run_coro(self._prewarm_brain())))

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
        self._note_said(text)
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
        self._note_said(text)
        self.speaker.say_now(text)

    # ------------------------------------------------------------ confirmations
    def _prompt_lock(self) -> asyncio.Lock:
        """
        ONE spoken prompt at a time, confirmation or stop-window.

        Two turns can be in flight and both confirm() and announce() wait on
        the same _answer_q. Without this, one "yes" answered whichever prompt
        was waiting OLDEST - not the one he had just heard - and confirm()'s
        finally cleared _awaiting_confirmation for both. Reproduced by an
        independent audit against the real code. Created lazily because
        tests build Jalen with __new__; both callers run on the brain's one
        event loop, so the None check and the assignment cannot interleave.
        """
        if getattr(self, "_confirm_lock", None) is None:
            self._confirm_lock = asyncio.Lock()
        return self._confirm_lock

    async def confirm(self, question: str) -> "ConfirmAnswer":
        """RED tier: ask out loud and wait for a real yes (spec F45/F46)."""
        timeout = float(self.cfg.get_path("safety.confirm_timeout_s", 20))
        async with self._prompt_lock():
            self.orb.set_state("blocked")
            self.say_blocking(question)
            # Stamped AFTER the question has finished playing, so the echo
            # window measures the tail of "...Confirm?" arriving back through
            # the microphone - not the length of the question.
            self._confirm_question = question
            self._confirm_asked_at = time.monotonic()
            self._confirm_reasked = False
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
            return ConfirmAnswer("timeout")
        if isinstance(answer, ConfirmAnswer):
            return answer
        # A bare bool: the kill switch puts False, deliberately - the AMBER
        # stop-window below tests `is False` on the same queue.
        return ConfirmAnswer("yes" if answer else "no")

    async def announce(self, text: str) -> None:
        """AMBER tier: say it, then give a short window to say stop."""
        window = float(self.cfg.get_path("safety.undo_window_s", 4))
        async with self._prompt_lock():
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

    def _echoes_the_confirmation(self, text: str) -> bool:
        """
        Is this the tail of "...Confirm?" coming back through the microphone?

        Every RED prompt ends in "Confirm?" and " confirm " is on the YES
        list, so the echo approved the action it was asking about. The
        general echo test could not catch it: it treats only three words or
        more as echo, so that a one-word answer such as "ChatGPT" is never
        taken for Jalen's own voice. Here the question is KNOWN, so a heard
        utterance that is the question's own ending - "Confirm.", "notepad.
        Confirm?" - within ECHO_TAIL_S of it finishing is the echo. "yes" is
        not in the question and so can never be mistaken for it.
        """
        if time.monotonic() - getattr(self, "_confirm_asked_at", 0.0) > ECHO_TAIL_S:
            return False
        heard = _SPOKEN_WORD.findall((text or "").lower())
        asked = _SPOKEN_WORD.findall((getattr(self, "_confirm_question", "") or "").lower())
        return bool(heard) and len(heard) <= len(asked) and asked[-len(heard):] == heard

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
    def _is_correction(text: str) -> bool:
        """
        "Send it to Rodion instead" - an answer that says what to do instead.

        Normalised exactly as _parse_yes_no normalises, so the two agree on
        what a marker is. A correction is never a yes to what was asked.
        """
        flattened = (text or "").strip().lower()
        for curly in ("’", "ʼ", "‘", "`"):
            flattened = flattened.replace(curly, "'")
        low = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", flattened)) + " "
        return any(marker in low for marker in _CORRECTION_MARKERS)

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
        # THE APOSTROPHE SPEECH RECOGNITION ACTUALLY EMITS is U+2019, not
        # U+0027, and the character class below keeps only the straight one.
        # So "don't send it" was scrubbed to " don t send it ", the NO list's
        # " don't " never matched, and what remained was " send it " - which
        # is in the YES list. The refusal sent it.
        flattened = (text or "").strip().lower()
        for curly in ("’", "ʼ", "‘", "`"):
            flattened = flattened.replace(curly, "'")
        low = " " + re.sub(r"[^a-z0-9' ]", " ", flattened) + " "
        low = re.sub(r"\s+", " ", low)
        if not low.strip():
            return None

        # --- ONE AGREEMENT THAT IS SPELLED WITH A NEGATIVE WORD ---
        #
        # Neutralised BEFORE either list is searched, because the bare-negation
        # rule below would otherwise read it as a refusal. "why not" is already
        # in the YES list and has always returned True; without this line the
        # new rule would silently take that away.
        #
        # EXACTLY ONE ENTRY, and it used to be eight. An independent review of
        # the first version measured the other seven against HEAD and every one
        # of them flipped:
        #
        #     no problem      False -> True        no doubt      False -> True
        #     no worries      False -> True        no objection  False -> True
        #     no rush         False -> True        nothing wrong  None -> True
        #     not a problem    None -> True
        #
        # That is a NO-to-YES flip on the last gate before a RED action runs,
        # which is the exact direction this function was rewritten to close.
        # "No rush" is not permission to send an email. The colloquial reading
        # of "no problem" as agreement is real, and it is still not worth
        # buying with six other phrases that are not agreement at all — so all
        # seven go back to what they did before, which is refuse.
        #
        # The cost is the false NO, stated rather than hidden: "yes, no
        # problem" reads as a refusal. That is unchanged from before this
        # function was touched, it costs him a sentence, and it is the
        # direction that cannot send anything.
        low = low.replace(" why not ", " yes ")

        # --- A BARE NEGATION, which the phrase list below never had ---
        #
        # THE WORST BUG IN THIS FILE. The NO list is phrases, so a refusal
        # built out of "not" plus any agreement word skipped it entirely and
        # landed on the YES list. Reproduced against the real function:
        #
        #     "of course not"       -> True   (" of course " matched)
        #     "I'm not sure"        -> True   (" sure " matched)
        #     "not right now"       -> True   (" right " matched)
        #     "that's not right"    -> True   (" that's right " matched)
        #     "certainly not"       -> True   (" certainly " matched)
        #     "not okay with that"  -> True   (" okay " matched)
        #
        # Each of those is a person refusing a RED action and getting it done.
        # "As long as I am showing any kind of agreement, it should confirm"
        # was a request about "Of course", not a licence to read "of course
        # not" the same way.
        #
        # The contraction half covers won't, can't, isn't, didn't, shouldn't
        # and the rest, none of which were on the NO list either.
        # " cannot " is one word, so none of the other three see it, and it
        # was not on the phrase list either - "I cannot" returned None and
        # cost a twenty-second confirm() timeout instead of an answer.
        if (" not " in low or " cannot " in low or " never " in low
                or re.search(r"[a-z]+n't ", low)):
            return False

        # --- NO, and first, because a refusal usually mentions the action ---
        for phrase in (
            " no ", " nope ", " nah ", " don't ", " dont ", " do not ",
            " stop ", " cancel ", " abort ", " no thanks ", " never mind ",
            " nevermind ", " wait ", " hold on ", " not yet ", " not now ",
            " forget it ", " leave it ", " skip it ", " negative ",
            " definitely not ", " absolutely not ", " i'd rather not ",
            # Colloquial refusals the agreement words below would otherwise
            # catch: "I'm good" is "no thanks", and " good " is a YES word.
            # Reproduced as approvals by an independent audit.
            " i'm good ", " im good ", " i'm busy ", " im busy ",
        ):
            if phrase in low:
                return False

        # --- NOT AN ANSWER: a correction, a question, somebody else's assistant ---
        #
        # Agreement is searched for ANYWHERE in the sentence, which is right
        # for "you, of course" and wrong for a sentence that only contains an
        # agreement word on its way to saying something else. Each of these
        # approved the ORIGINAL action, reproduced against this function:
        #
        #     "send it to Rodion instead"            " send it "
        #     "great, but first fix the typo"        " great "
        #     "ok so what does it say"               " ok "
        #     "Okay Google"                          " okay "
        #     "good question, who is Ulughbek"       " good "
        #
        # None, not False: confirm() keeps waiting and asks once, or - for a
        # correction - process() hands his words back to the turn that asked.
        # Moving YES to None is the safe direction; nothing here can turn a
        # refusal into approval.
        if any(marker in low for marker in _CORRECTION_MARKERS):
            return None
        if any(w in low for w in (" what ", " who ", " why ", " how ", " which ",
                                  " where ", " when ")):
            return None
        if any(w in low for w in (" google ", " alexa ", " siri ")):
            return None

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

    # ------------------------------------------------- chat panel (extension)
    def _on_bridge_event(self, msg: dict) -> None:
        """
        Something happened in his Chrome. Today: a line typed into the
        extension's chat panel.

        Handled on its OWN thread, not the bridge read loop's, because a
        brain turn takes seconds and the read loop must stay free to carry
        the very commands that turn will issue. The panel text is untrusted
        user input from a browser surface, so it goes through the ordinary
        brain path - considered, never obeyed as a command.
        """
        if msg.get("event") != "user_message":
            return
        text = (msg.get("payload") or {}).get("text", "").strip()
        if not text:
            return
        threading.Thread(target=self._answer_panel, args=(text,),
                         name="jalen-panel-turn", daemon=True).start()

    def _answer_panel(self, text: str) -> None:
        from .bridge.server import get_server, BridgeError
        try:
            reply = self._run_coro(self.handle_with_brain(text))
        except Exception as exc:  # noqa: BLE001
            self.audit.error("panel.turn", exc)
            reply = f"Something went wrong handling that: {type(exc).__name__}"
        try:
            get_server().send_command("show_message",
                                      {"role": "jalen", "text": reply}, timeout=10)
        except BridgeError:
            pass          # panel closed or extension gone; nothing to show

    # ------------------------------------------------------------- timing
    def _mark_first_audio(self) -> None:
        timer = self._turn_timer
        if timer is not None:
            timer.audio_starts += 1
            timer.mark("first_audio")
            # WHICH SOUND WAS THE ANSWER. The acknowledgement pushed by
            # brain.ack_after_ms is exactly one sentence into the same
            # stream, so with a filler the SECOND start is the answer and
            # without one the first is. Counting is deterministic; comparing
            # against "has real text arrived yet" is not, because the text
            # can land between the filler being queued and its audio
            # starting — and that race resolves in the flattering direction.
            if timer.audio_starts > (1 if timer.filler_pushed else 0):
                timer.mark("answer_audio")
            # Captured HERE, at the moment the silence ended, rather than at
            # the end of the turn: a long reply renders many sentences and
            # the later ones overwrite last_source, so reading it afterwards
            # would report how the turn FINISHED speaking, not what he
            # actually waited for.
            if not timer.tts_source:
                timer.tts_source = getattr(self.speaker, "last_source", "") or ""

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

    def handle_local(self, intent, *, his_own_words: bool = False) -> Optional[str]:
        """
        Router hit — execute without ever touching an LLM.

        `his_own_words` is True only for a rule that matched what he SAID,
        word for word (process() passes it; nothing else does): see the
        origin note at the classify call below.
        """
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

        # WHERE THIS CAME FROM. The default is the taint check, the same one
        # the brain path makes, so the router is never a way around the
        # guard: a habit recalled while an email was on screen, or a sentence
        # that expand_references() filled in from what was read ("open it" ->
        # "open <an email subject>"), is not automatically his.
        #
        # EXCEPT a rule that matched what he SAID, word for word. His words
        # are speech, typing, or a message from his own authenticated
        # Telegram chat - a page cannot write them - and refusing them made
        # "scroll down", "open Spotify" and "cancel that" fail after Jalen
        # had read anything, until he addressed him by name again (rule
        # matches measured 2026-10-01; tests/
        # test_his_own_words_are_not_refused_after_a_read.py). What taint
        # protects against is the MODEL acting on read text, and that path
        # (the brain's tool hook) is not touched. Never-touch still applies:
        # it is checked before origin.
        origin = "user" if his_own_words else taint.origin_now()
        verdict = self.safety.classify(tool, intent.args,
                                       origin=origin,
                                       named_by_him=taint.named())
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

    # --------------------------------------------------- when Claude can't
    #
    # How long a latched brain-down state lasts before the next turn tries
    # Claude again even though nothing visible has changed.
    #
    # Five minutes, because the signal that normally clears it - the
    # credentials file changing when he signs in - is not the only way he
    # fixes it: a usage limit simply expires, and a billing problem is fixed
    # on claude.ai. A failed retry costs one round-trip of about 400ms
    # (measured against the live expired sign-in on 2026-09-30) and spends
    # nothing, because an unauthenticated request is never billed. So the
    # only cost of a short interval is that latency, paid at most once per
    # five minutes and only when he actually asks something.
    BRAIN_RETRY_S = 300.0

    # Latched: nothing will change until he acts, so asking again is waste.
    # Not latched: Claude's servers having a bad minute, or a malformed
    # request, which the very next turn may not repeat.
    _LATCHING_KINDS = ("authentication_failed", "billing_error", "rate_limit")

    def _credentials_stamp(self) -> float:
        try:
            return self._credentials_file.stat().st_mtime
        except OSError:
            return 0.0

    def _latch_brain_down(self, kind: str, detail: str = "") -> None:
        self._brain_down = kind
        self._brain_down_detail = detail
        self._brain_down_at = time.monotonic()
        self._credentials_seen = self._credentials_stamp()

    def _clear_brain_down(self) -> None:
        self._brain_down = None
        self._brain_down_detail = ""
        self._brain_down_told = False

    def _brain_still_down(self) -> str | None:
        """The latched failure, or None if it is time to try Claude again."""
        kind = self._brain_down
        if kind is None:
            return None
        if self._credentials_stamp() != self._credentials_seen:
            return None           # he signed in: try it now, not in five minutes
        if time.monotonic() - self._brain_down_at >= self.BRAIN_RETRY_S:
            return None
        return kind

    def _brain_down_sentence(self, kind: str, detail: str, first: bool) -> str:
        """
        One sentence per failure he can act on. Never the CLI's raw text,
        except where that text is the useful part - a usage limit's reset
        time.
        """
        if kind == "authentication_failed":
            if first:
                return (
                    "My Claude sign-in has expired, so I can't do anything that "
                    "needs thinking until you sign me back in. Quick commands - "
                    "opening apps, reading Telegram, the time - still work. Run "
                    "jalen check and it'll show you the one command to type."
                )
            return ("I still can't think - my Claude sign-in has expired. "
                    "Run jalen check for the fix.")
        if kind == "billing_error":
            if first:
                return (
                    "Claude is reporting a billing problem on the account, so I "
                    "can't think right now. That one needs you at claude.ai - "
                    "quick commands still work."
                )
            return "Still blocked on Claude's billing problem - that needs you at claude.ai."
        if kind == "rate_limit":
            said = f" It says: {detail.rstrip('.')}." if detail else ""
            if first:
                return (f"I've hit Claude's usage limit.{said} Quick commands "
                        "still work until it resets.")
            return f"Still over Claude's usage limit.{said}"
        if kind == "server_error":
            return ("Claude's servers are struggling right now. Give it a "
                    "minute and ask me again.")
        if kind == "invalid_request":
            return ("Claude rejected that request as malformed - that's a fault "
                    "on my side, not something you said.")
        return "Claude returned an error I don't recognise, so I couldn't answer that."

    async def handle_with_brain(self, text: str, on_text=None) -> str:
        from .brain.agent import BrainUnavailable

        down = self._brain_still_down()
        if down is not None:
            # Known down, and nothing has changed: answer from memory rather
            # than paying a round-trip to be told the same thing again.
            return self._brain_down_sentence(down, self._brain_down_detail, first=False)

        if self.brain is None:
            try:
                await self._start_brain()
            except Exception as exc:
                # A brain that cannot START is a different failure from one
                # that breaks mid-answer, and it used to be the worst kind:
                # this call sat OUTSIDE the try below, so the exception flew
                # past the handler that turns a brain error into a spoken
                # sentence, past process(), and into dispatch_turn — which
                # records the task FAILED, re-raises into a daemon thread
                # nobody joins, and says nothing. The traceback reached
                # data/crash.log and the room stayed silent. Observed for
                # real on 20 Sept: a claude-agent-sdk upgrade installed a
                # source-built wheel with no bundled claude.exe, the SDK fell
                # through to npm's claude.CMD and refused to spawn a batch
                # script, and every turn produced nothing at all.
                #
                # It also must not say "try again". The causes here are
                # configuration — binary missing, unauthenticated,
                # unspawnable — so repeating the question loops forever.
                # Point at the diagnostic instead.
                self.audit.error("brain-start", exc)
                self.brain = None
                return (
                    f"I couldn't start my brain — {type(exc).__name__}. "
                    "That's a setup problem rather than a bad moment, so "
                    "asking again won't help: run jalen check."
                )
        try:
            reply = await self.brain.ask(text, on_text=on_text)
        except BrainUnavailable as exc:
            # Recorded as an ERROR, which it is - it used to be logged as an
            # ordinary utterance with outcome null, so "why has Jalen been
            # useless for ten days" had no answer in the audit trail.
            self.audit.error(f"brain.{exc.kind}", exc)
            first = not (self._brain_down == exc.kind and self._brain_down_told)
            if exc.kind in self._LATCHING_KINDS:
                self._latch_brain_down(exc.kind, exc.detail)
                self._brain_down_told = True
            return self._brain_down_sentence(exc.kind, exc.detail, first=first)
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

        # It answered, so whatever was latched is over.
        if self._brain_down is not None:
            self._clear_brain_down()
        return reply

    async def _prewarm_brain(self) -> None:
        """
        Start the brain in the background, and say ONCE if it is signed out.

        Connecting succeeds with no credential at all, so prewarm used to
        log success while signed out and he found out only when a real
        request failed. Saying it at startup, once, is the difference
        between "Jalen is broken" and "Jalen told me what to fix".
        """
        await self._start_brain()
        if self.brain is None:
            return
        if await self.brain.signed_in() is False:
            self._latch_brain_down("authentication_failed")
            self._brain_down_told = True
            self.audit.write("system", summary="brain signed out at startup")
            self.say(self._brain_down_sentence("authentication_failed", "", first=True))

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
        # A new reply starts with nothing handed over. A turn that failed
        # half way never reaches _note_said, which is what clears this.
        self._voice_so_far = ""
        # "read my email out loud" removes the cap for THIS turn only. The
        # cap is a default about length, not a rule about what he may hear.
        read_it_all = wants_it_all(user_text)
        max_spoken = float("inf") if read_it_all else self.speaker.max_spoken
        state = {"chars": 0, "overflowed": False, "warned": False}

        # SAY HOW LONG IT IS, ONCE, when he asked for the whole thing.
        #
        # His spec: "If the content is very long: announce the scope if
        # needed, but still continue until the requested end unless the user
        # interrupts." Both halves matter. Twenty turns in his log spoke for
        # over forty seconds and several were cut off by him talking over
        # them - not because the content was wrong, but because he had no
        # idea he had asked for four minutes of speech.
        #
        # 22.4 characters a second. RE-MEASURED, because 14.0 was wrong and
        # the error was all in one direction: a 320-character reply rendered
        # by en-US-AndrewNeural at +18% comes back as 85,824 bytes of 48kbps
        # mp3, which is 14.3 seconds of speech - 22.4 chars/sec, not 14.
        #
        # At 14.0 this told him "about three minutes" for something that
        # actually ran under two, so the one warning designed to stop him
        # being ambushed by a long read was itself overstating by sixty per
        # cent. A warning that cries wolf gets ignored, and then the real
        # four-minute answer arrives unannounced.
        # EVERYTHING HANDED TO THE SPEAKER IS ALSO REMEMBERED, as it is handed
        # over: the address gate compares what the microphone hears against
        # it, and the reply is not filed as `_last_reply_text` until it has
        # finished playing. See _note_voice.
        def push(piece: str) -> None:
            stream.push(piece)
            self._note_voice(piece)

        def maybe_warn_about_length(so_far: int) -> None:
            if state["warned"] or not read_it_all:
                return
            seconds = so_far / SPOKEN_CHARS_PER_SECOND
            if seconds < 60:
                return
            state["warned"] = True
            push(f" This runs to about {round(seconds / 60)} minutes — "
                 f"say stop whenever you've heard enough. ")

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
                push(chunk)
                maybe_warn_about_length(state["chars"])
                return

            state["overflowed"] = True
            head = chunk[:room] if room > 0 else ""
            cut = max(head.rfind("."), head.rfind("!"), head.rfind("?"))
            if cut > 0:
                head = head[: cut + 1]
            if head.strip():
                push(head)
            push(" That's the short version, the full text is on screen.")

        # A turn that calls tools first produces no text for seconds. Silence
        # reads as "it's broken" — he said exactly that, more than once, in
        # the log. One short line costs nothing (it is pre-rendered into the
        # phrase cache at startup) and turns dead air into visible work.
        ack_after = float(self.cfg.get_path("brain.ack_after_ms", 1400)) / 1000.0

        def acknowledge() -> None:
            if state["chars"] == 0:
                push("Give me a second.")
                # RECORDED, so the stopwatch can tell this sound from the
                # answer. Without it, 65% of brain turns reported the
                # acknowledgement firing as their thinking time.
                timer = self._turn_timer
                if timer is not None:
                    timer.filler_pushed = True

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
            #
            # The filler goes with it. Anything queued on this stream is
            # discarded, so a filler that was pushed is now never heard —
            # and leaving the flag set would make the stopwatch wait for a
            # second sound that never comes, losing the measurement
            # entirely on exactly the tool-heavy turns worth measuring.
            timer = self._turn_timer
            if timer is not None:
                timer.filler_pushed = False
            stream.abandon()
            self._voice_so_far = ""
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
        self._note_said(reply)
        # Filed whole now; what was kept piece by piece while it streamed is
        # the same words and would only be compared twice. Cleared HERE and
        # not in _note_said: an announcement spoken in the middle of a stream
        # (say_blocking) files itself too, and must not wipe the reply that is
        # still on air.
        self._voice_so_far = ""

    def process(self, text: str, *, from_him: bool = True, door: str = "") -> None:
        """
        Handle one utterance.

        `from_him` says whether it is POSITIVELY his: spoken with his name or
        the wake word, or typed. The microphone loop passes False for speech
        it admitted without the name - the question window, a stitched
        continuation - because that may be the television. Every other front
        door (text mode, the Telegram bot, the extension's chat panel) is
        text he typed, so the default is True.
        """
        text = (text or "").strip()
        if not text:
            return

        low = text.lower().rstrip(".!?")

        # kill switch always wins - over a waiting question too. The same
        # matcher as the address gate's exemption; see is_kill_phrase.
        if is_kill_phrase(text, self.kill_phrases):
            self.audit.utterance(text, who="user")
            self.speaker.stop()
            self.kill.set()
            self._answer_q.put(False)
            return

        # ANSWERS FIRST, and an answer is not an instruction.
        #
        # These three branches used to sit BELOW the taint clear, the
        # stitching and the reference expansion, so an answer to a waiting
        # question counted as a fresh instruction from him. An independent
        # audit reproduced it against this function: after reading a web
        # page, one word - "Amen." - handed to ask_user turned the same
        # turn's next send from BLACK into GREEN. And in the production log
        # on 26 August, ask_user ran with origin=content at 10:59:50, he
        # answered "Hey Jelen.", and the same turn's next ask_user at
        # 11:01:33 ran with origin=user.
        #
        # Above the stitching for two smaller reasons. An answer used to
        # replace self._plan with read_plan("the notes one"), so the turn
        # that asked was judged afterwards against the wrong contract; and
        # expand_references would rewrite "it" inside a phone number.
        #
        # A rating he was just asked for. Before _awaiting_reply because
        # nothing is waiting on a queue here, and before the router because
        # a bare "eight" would otherwise route to whatever "eight" matches.
        if self._pending_rating is not None:
            if time.time() - self._pending_rating.get("asked_at", 0) > self.RATING_EXPIRES_S:
                self._pending_rating = None      # he moved on; so do we
            elif self._take_rating(text):
                self.audit.utterance(text, who="user")
                return

        # A pending open QUESTION takes everything he says as the answer:
        # "open chrome" said in answer to "what's your phone number" is an
        # answer, not a command, and routing it would be both wrong and
        # irreversible.
        if self._awaiting_reply:
            self.audit.utterance(text, who="user")
            self._reply_q.put(text)
            return

        # a pending yes/no takes priority over everything else
        if self._awaiting_confirmation:
            # Jalen's own "...Confirm?" coming back through the microphone.
            # Ignored silently: it is not him, so it is neither an answer
            # nor a new instruction.
            if self._echoes_the_confirmation(text):
                return
            # "Send it to Rodion instead." Not a yes to what was asked - it
            # was approving the ORIGINAL action - and not a plain no either:
            # it is what he wants done. His words go back with it, so the
            # turn that asked can act on them, instead of this falling
            # through and starting a second turn in parallel.
            if self._is_correction(text):
                self.audit.utterance(text, who="user")
                self._answer_q.put(ConfirmAnswer("correction", text))
                return
            answer = self._parse_yes_no(text)
            if answer is not None:
                self.audit.utterance(text, who="user")
                self._answer_q.put(ConfirmAnswer("yes" if answer else "no", text))
                return
            # Not readable as either. It used to fall straight through to
            # the router and the brain, starting a second turn while the
            # first was still waiting on him. Asked about ONCE; after that a
            # new instruction is his to give, so it is handled as before
            # rather than trapping him inside the confirmation.
            if not getattr(self, "_confirm_reasked", False):
                self._confirm_reasked = True
                self.audit.utterance(text, who="user")
                self.say("Sorry - was that a yes or a no?")
                return

        # ------------------------------------------------------------------
        # From here on this is a NEW instruction.
        # ------------------------------------------------------------------

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
        #
        # Two conditions, and both are over-blocking by design - the
        # direction taint.py chose, because the other direction is an email
        # sending mail on its author's behalf:
        #
        #   from_him     admitted by his name or the wake word, or typed. The
        #                question window admits speech with no name, which is
        #                right for hearing an answer and wrong for certifying
        #                that what was read no longer matters.
        #   no overlap   taint is process-wide and two turns can run at
        #                once, so this turn clearing it would un-taint the
        #                OTHER turn while it is still acting on the email it
        #                read. taint.py said overlap could only over-block;
        #                it could also under-block, until this.
        fresh = from_him and not self._another_turn_in_flight()
        if fresh:
            taint.he_asked_again()

        # WHAT "IT" MEANS. Expanded before anything routes, because "send it
        # to Saved Messages" reaching the router as literally "it" is how a
        # request becomes a guess. Conservative: only expands when there IS a
        # remembered subject, never invents one.
        expanded = conversation.expand_references(text)
        # Whether what the router sees is exactly what he said. Expansion can
        # put READ text into the sentence, so an expanded one keeps the taint
        # check in handle_local.
        # A sentence let in by a DOOR (a misheard name, a polite request, the
        # bare-wake window - run() names it) is the least certain speech
        # there is: it keeps the taint check, like an expanded one does.
        his_own_words = expanded == text and not door
        if expanded != text:
            self.audit.write("system",
                             summary=f"read '{text[:60]}' as '{expanded[:80]}'")
            text = expanded

        # THE CONTRACT FOR THIS TURN. He said send, or draft, or save, and to
        # where. Kept so that what actually runs can be compared against what
        # he asked for - see _check_the_plan below.
        self._plan = planning.read_plan(text)
        # And WHERE he said to put it, for the gate's one taint exception:
        # a send to his own Saved Messages survives Jalen having read an
        # email only when HIS words named Saved Messages. Same condition
        # as the clear above, for the same reason.
        if fresh:
            taint.he_named(self._plan.destination)

        self.audit.utterance(text, who="user")

        # Recomputed: stitching and expansion may have changed the text, and
        # the end-phrase test below is about what he now means.
        low = text.lower().rstrip(".!?")

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
            reply = self.handle_local(intent, his_own_words=his_own_words)
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
        # WHICH GATE OPENED THE WINDOW, for the address gate to read:
        #   "wake"       the wake word, the hotkey, or un-pausing
        #   "follow_up"  a sound inside the 12 seconds after a reply
        #   "answer"     a sound while a question of Jalen's is open
        #   "barge"      a sound over his own speech
        # Set at EVERY place `listening` becomes True - a window opened
        # without saying why would inherit the previous one's, and a barge-in
        # on a cough would walk in on the follow-up window's exemption.
        opened_by = ""
        # AND WHICH QUESTION OF JALEN'S WAS OPEN when a sound opened the
        # window - the Expectation itself, or None. The window is asked again
        # by the address gate once the sentence has ended, and a long answer
        # that began inside it has outlived it by then. Reset at every place
        # `listening` becomes True, exactly like `opened_by`.
        vouched_by = None
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

        def dispatch_turn(text: str, turn_id: int, timer: TurnTimer,
                          from_him: bool = True, door: str = "") -> None:
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
            # Monotonic, and captured before anything speaks: the tail below
            # has to know whether THIS turn said something, or a turn that
            # spoke nothing re-stamps a brand-new window on a question that
            # is minutes old. say() returns early when muted, before it
            # records the text, so "muted" and "said nothing" look identical
            # from _last_reply_text alone - and muted, that re-arming never
            # stops.
            began_at = time.monotonic()
            task_id = conversation.start_task(text[:80], conversation.THINKING)
            try:
                with systools.com_initialized():
                    self.process(text, from_him=from_him, door=door or "")
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
                # INSIDE THE finally, not after it. Everything below closes
                # or re-opens a window that waives his name, and the branch
                # above re-raises - so on a brain failure, a tool failure or
                # a cancellation the old code skipped all of it and left the
                # previous question's window open for the rest of its thirty
                # seconds, with no reply ever spoken. That is the unbounded
                # exemption this feature was built to avoid, arriving
                # precisely when Jalen is least able to notice.
                #
                # Only the newest turn owns the UI state and the follow-up
                # window: a slow turn finishing late must not reopen it and
                # reset the orb long after he moved on.
                if turn_id == self._turn_seq:
                    if self.cfg.get_path("conversation.follow_up", True):
                        follow_up_until = time.monotonic() + follow_up_s
                    # DID IT ASK HIM SOMETHING? Decided here, after
                    # _await_playback() above has returned, so the clock
                    # starts when Jalen stopped talking rather than when the
                    # model stopped generating — see _expect_an_answer.
                    #
                    # Replaced every turn, never accumulated: the newest
                    # question is the only one he can be answering, and a
                    # reply that asks nothing CLOSES the window rather than
                    # leaving the last question's open.
                    #
                    # And only when THIS turn actually spoke. A turn that
                    # said nothing - muted, or an intent that returns no
                    # line - leaves _last_reply_text holding the previous
                    # turn's question, and re-stamping from that renews the
                    # exemption forever instead of closing it.
                    spoke_this_turn = self._last_reply_at > began_at
                    if spoke_this_turn and solicits_an_answer(self._last_reply_text):
                        self._expect_an_answer(self._last_reply_text, turn_id)
                    elif spoke_this_turn:
                        self._forget_expectation()
                    # Recomputed, not assumed: another turn may still be
                    # working, and telling him it is idle while it is not is
                    # the same lie in the other direction.
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
                        opened_by = "wake"
                        vouched_by = None
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
                        opened_by = "wake"
                        vouched_by = None
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
                    opened_by = "barge"
                    vouched_by = None     # a barge-in vouches for nothing
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
                    # A QUESTION JALEN ASKED IS ALSO A LISTENING WINDOW, and
                    # a longer one than the ordinary follow-up. Two gates
                    # have to agree for an answer to land: this one opens
                    # the microphone, and the address gate below decides
                    # whether the words are his. Without the same condition
                    # in both, an answer given after twelve seconds is never
                    # recorded at all and the gate never gets to accept it.
                    #
                    # Measured over the answers that DID get through by
                    # repeating his name: 31 of 49 arrived more than twelve
                    # seconds after the question. Saying the name again is
                    # what you do when the window has shut.
                    if ((in_follow_up or awaiting_reply or self._expectation_open())
                            and self.vad.probability(frame) >= self.vad.threshold):
                        listening = True
                        wake_initiated = False   # a sound opened this, not him
                        # The follow-up window vouches for a polite request;
                        # an open question has already vouched for anything.
                        opened_by = "follow_up" if in_follow_up else "answer"
                        # The question that is open at THIS instant - the
                        # window the sound opened, whichever of the three
                        # reasons above let it in. Taken here, not when the
                        # sentence is over: by then it may have closed.
                        vouched_by = self._open_expectation()
                        # _refresh_orb, NOT set_state("listening"). This is
                        # the line he saw: a noise during the follow-up window
                        # painted the orb blue while a turn was still working,
                        # for as long as the turn took.
                        self._refresh_orb(listening=True)
                    elif self.wake.feed(frame):
                        listening = True
                        wake_initiated = True
                        opened_by = "wake"
                        vouched_by = None
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
                    #
                    # AND WHEN HE DID SAY "hey Jalen", the answer is the one
                    # the router gives the same words typed. The wake model
                    # fires about 0.77s in, at the end of the name, so a call
                    # that is only the name leaves nothing behind it - and
                    # "I didn't catch that" to a person who has just said
                    # your name is the deaf assistant. The words he gets
                    # instead have a record: 6 of the 7 "Yes, Boss?" in the
                    # log were followed by a sentence of his within 22s
                    # (3 acted on, 3 dropped by the gate as it was) and none
                    # was echoed back. Measured since the address gate
                    # shipped: 50 of those announcements, and in 11 of them
                    # he said something again within 40 seconds (4 within
                    # 12). 21 of the 50 had no other activity in the 60
                    # seconds before and seven sessions hold 31 of them, so
                    # some are false wakes, and a false wake now opens a
                    # window. That is why it is the follow-up length (12s)
                    # and not the 30s a real question gets; 0 of the 105
                    # ignored rows arrive within 12s of any of the 50.
                    self.orb.flash("blocked", 0.8)
                    self._note_nothing_heard("no-speech", opened_by,
                                             vouched=self._vouching(vouched_by))
                    if wake_initiated:
                        self._acknowledge_a_bare_wake()
                    self.orb.set_state("muted" if self.muted else "idle")
                    continue

                # The stopwatch starts the moment the microphone decided he
                # had finished — not when the turn is dispatched. Everything
                # between here and the first sound is silence he sits in.
                timer = TurnTimer()
                timer.mark("speech_end")
                # AND THE SILENCE THAT CAME BEFORE IT. The collector only
                # hands the utterance over after the endpoint threshold of
                # continuous quiet has already passed, so this mark is
                # 1400ms (or 4000ms on the patient path) after he actually
                # stopped talking. He sits through all of it, and no number
                # this module reported had ever included it.
                timer.endpoint_ms = (
                    self.collector.patient_silence_ms
                    if self.collector.was_patient
                    else self.collector.fast_silence_ms
                )

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
                # Which engine actually answered. `last_engine` is set by
                # Transcriber.transcribe on every call, and a turn that
                # quietly fell back to the local model is exactly the turn
                # worth being able to find afterwards.
                timer.stt_engine = getattr(self.stt, "last_engine", "") or ""

                if not text:
                    # Audio that was long enough to transcribe and came back
                    # as nothing. Silent, as it always was - but written
                    # down, because until now there was no row for it and
                    # "how often does recognition return nothing" could not
                    # be answered from the log.
                    self._note_nothing_heard(
                        "empty-transcript", opened_by,
                        seconds=round(len(utterance) / float(self.collector.sample_rate), 1),
                        vouched=self._vouching(vouched_by),
                    )
                    self.orb.set_state("idle")
                    continue

                # Decided BEFORE the gate, because the gate closes the answer
                # window as it accepts: a sentence admitted as the answer to
                # a question is an answer, and must reach the question
                # untouched even if its first word is a name on the list.
                # `outlived`: his sentence began inside an answer window that
                # has closed since - see _window_outlived_by_the_sentence.
                outlived = (
                    vouched_by is not None and not self._expectation_open()
                    and self._window_outlived_by_the_sentence(vouched_by)
                )
                was_an_answer = (
                    self._awaiting_confirmation or self._awaiting_stop
                    or self._awaiting_reply or self._rating_is_pending()
                    or self._expectation_open() or outlived
                    or self._continues_last_utterance(text)
                )
                # WHICH question it answers, taken before the gate closes it,
                # so the row below can say whether it came through the window
                # "Yes, Boss?" opened (which has no question behind it).
                answered = self._open_expectation() or (vouched_by if outlived else None)
                vouching = self._vouching(vouched_by)

                # THE UNFINISHED SENTENCE IS ASKED ABOUT FIRST, AND THE WINDOW
                # IS NOT USED UP BY ASKING. The utterance closed on the FAST
                # threshold; if the words say he is mid-sentence ("...open
                # chrome and"), put the audio back and keep listening on the
                # patient one - but only if he would be heard at all. The
                # whole phrase is re-transcribed once at the end rather than
                # stitched from two partial transcripts - one Whisper pass
                # over the complete audio is both more accurate and cheaper
                # than two over halves of it.
                #
                # This used to come AFTER the gate, which closes the answer
                # window as it accepts. The fragment was accepted and the
                # window shut, the audio was put back, and the WHOLE sentence
                # then met a gate with nothing left to answer: refused. The
                # long dictated answers the outlived-window rule exists for
                # are exactly the ones that pause on a connector. Television
                # that ends on "and" is still refused at once, as it always
                # was, and not held on to for four more seconds.
                if (not self.collector.was_patient and looks_unfinished(text)
                        and self.should_act_on(text, wake_initiated, opened_by=opened_by,
                                               vouched_by=vouched_by, consume=False)):
                    self.collector.resume(utterance)
                    listening = True
                    self.orb.set_state("listening")
                    continue

                if not self.should_act_on(text, wake_initiated, opened_by=opened_by,
                                          vouched_by=vouched_by):
                    self.audit.write(
                        "system",
                        summary="ignored - not addressed to Jalen",
                        # `opened_by` and `vouched_by` are additive: the
                        # history was read by `heard` alone, and the next
                        # measurement can now split the refusals by the gate
                        # that opened the window and by what was open.
                        detail={"heard": text[:200], "opened_by": opened_by,
                                "vouched_by": vouching},
                    )
                    self._refresh_orb()
                    continue

                # WAS IT POSITIVELY HIM? The address gate admits some speech
                # with no name - an answer to its own question, a stitched
                # continuation, anything while a confirmation is pending -
                # and that is right for HEARING him. It is not enough to
                # certify that an email Jalen just read no longer matters,
                # because it may be the television. Only the wake word or
                # his name can do that; see the taint clear in process().
                from_him = wake_initiated or addressed_to_jalen(text)

                # HOW IT GOT IN, when it was not his name or the wake word -
                # decided here, on the words as they were heard, and written
                # below once the sentence is really going on to a turn: a
                # sentence that is put back for more audio comes through this
                # gate twice and must leave one row. Not for an answer, which
                # is the question's own door and has always been free.
                door = None
                heard = text
                if not wake_initiated and not from_him:
                    if getattr(answered, "kind", "") == "bare-wake":
                        door = "bare-wake-window"
                    elif outlived:
                        door = "after-the-window-closed"
                    elif was_an_answer:
                        door = None
                    else:
                        door = widened_door(text, opened_by)

                # HIS NAME, MISHEARD, COMES OFF BEFORE THE ROUTER SEES IT.
                # "Dylan, what time is it" reached the brain whole - a model
                # round trip for a question the router answers in 90ms - and
                # a bare "Helen." was a model call to say "Yes, Boss?".
                # After `from_him` on purpose: rewriting the name to "Jalen"
                # first would let a misheard name certify that it was him.
                if not was_an_answer:
                    text = without_a_misheard_name(text)

                # Going on to a turn, so the row is written now - with the
                # words as heard, not as the router will see them.
                if door:
                    self._note_how_it_got_in(door, heard, opened_by, vouched=vouching)

                with self._turn_lock:
                    self._turn_seq += 1
                    turn_id = self._turn_seq
                    in_flight = len(self._active_turns)
                    self._active_turns.add(turn_id)
                # Backlog guard. Turns used to be dispatched with no limit and
                # no cancellation, so a handful of commands issued while one
                # was still working would all complete later and speak in a
                # burst — the reported "old commands execute minutes later".
                #
                # NEVER FOR AN ANSWER. When a turn is waiting on a
                # confirmation or a question, it is itself one of the turns
                # in flight - so with one other turn running, his "yes" was
                # refused with "I'm still on the last one", the confirmation
                # timed out, and the timeout was reported as a refusal. An
                # answer starts no work; it releases work already waiting.
                answering = (self._awaiting_confirmation or self._awaiting_reply
                             or self._awaiting_stop or self._rating_is_pending())
                if in_flight >= MAX_IN_FLIGHT_TURNS and not answering:
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
                    target=dispatch_turn, args=(text, turn_id, timer, from_him, door), daemon=True,
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
    def should_act_on(self, text: str, wake_initiated: bool,
                      opened_by: str = "", vouched_by=None,
                      consume: bool = True) -> bool:
        """
        Three ways through, and the middle one is the one he chose when asked.

        wake word fired     he said "hey Jalen" out loud, so the name is in
                            the AUDIO and will not be in the transcript
        Jalen just asked    "which file did you mean?" — requiring the name
                            to answer a question he was this moment asked
                            would be absurd, and it is the only reason the
                            follow-up window still exists
        starts with a name  everything else, in any pronunciation

        And three narrower doors at the very end, for the sentences that were
        plainly for him and were refused anyway - 40 of the 78 in
        data/audit.jsonl once the question window was counted, of which these
        rescue 13 (and the long answers that outlived their window, below,
        another 5); see scripts/measure_address_gate.py and router.all_doors:

        his name misheard  "Dylan, what time is it" - 7 names, each of which
                           rescued a refused sentence; not in barge-in
        "hey Jalen" last   "...open it. Hey Jalen."
        a polite request   "could you please..." / "I would like you to...",
                           but ONLY when `opened_by == "follow_up"`

        `opened_by` is the other half of the two-gates rule. run() knows which
        sound opened the microphone window ("follow_up", "answer", "barge" or
        "wake"); this method used to be unable to see it, which is how a
        window opened FOR his next sentence ended with that sentence thrown
        away. Callers that do not know pass nothing and get no exemption.

        `vouched_by` is the same idea for the answer window: the Expectation
        that was open when the sound began, so a long answer is not refused
        for ending after the window did. See the branch that reads it.

        `consume=False` asks whether he WOULD be heard without using up the
        question he is answering. run() asks that first of a sentence that
        looks unfinished, which it may put back and ask about again whole.

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
        # NEVER ITS OWN VOICE, whatever comes below. Checked before the two
        # remaining exemptions rather than inside one of them, because the
        # review found the rating branch was the hole: it returned True with
        # no echo test at all, for five minutes, starting from a question
        # Jalen had just said out loud. The three flags above are different -
        # each has a coroutine blocked on a queue that swallows the text -
        # whereas a non-numeric utterance here falls straight through to the
        # router and the brain as a command.
        if (self._rating_is_pending() or self._expectation_open()) \
                and self._sounds_like_its_own_voice(text):
            return False
        if self._rating_is_pending():
            return True
        # AN ORDINARY QUESTION HE IS ANSWERING.
        #
        # The three flags above are the ask_user tool, the RED confirmation
        # and the AMBER stop-window: all three are set by code that then
        # BLOCKS on a queue. None of them is set when Jalen simply ends a
        # reply with a question, which is 31.5% of everything it says — so
        # until this branch existed, the docstring above was describing
        # behaviour the code did not have, and the answer was dropped.
        #
        # The echo test is not optional here. This gate WAS the echo
        # defence, because Jalen's own sentences do not begin with his name,
        # and a free window opens at the exact moment the microphone is most
        # likely to be hearing the tail of the question just asked.
        if self._expectation_open():
            # ONE ANSWER, THEN CLOSED. Leaving it open is what made this a
            # self-triggering loop: the answer is accepted, the next turn
            # runs, and its reply comes back through the microphone into a
            # window that is still open and still comparing against the OLD
            # question. Closing here breaks that at the first step, and the
            # next reply opens its own window at its own tail if it asks
            # anything. The cost is a two-part answer - "yeah." then "the
            # second one" - where the second half now needs his name.
            #
            # Only when `consume`. run() asks first, without consuming, whether
            # a sentence that looks unfinished would be heard at all: it is put
            # back for more audio if so, and the WHOLE sentence is asked about
            # a second time. Closing the window on the first, partial, ask
            # left the second with nothing to be an answer to.
            if consume:
                self._forget_expectation()
            return True
        # AN ANSWER THAT OUTLIVED ITS WINDOW. The window above is asked at the
        # END of his sentence, after the sentence itself, the endpoint and the
        # transcription - but the microphone opened for it when the sentence
        # BEGAN. A long answer that started in the last seconds of the 30 is
        # open to one gate and shut to the other, and was refused for being
        # late when it was not. run() hands over the question that was open
        # when the sound began (`vouched_by`); it is honoured only while it is
        # still the newest thing Jalen asked (identity, not time - a later
        # turn that asks or forgets replaces it) and for as long as one
        # utterance can run. The echo test is not optional: this is a way in
        # without his name, and a late one, so it looks back as far as the
        # doors do. `vouched_by is not None` comes first, so a caller that
        # does not know (every older test double) never reaches the methods
        # this needs.
        door_tail = getattr(self, "_door_echo_tail_s", ECHO_REACHES_THE_GATE_S)
        if vouched_by is not None and self._window_outlived_by_the_sentence(vouched_by):
            if self._sounds_like_its_own_voice(text, tail_s=door_tail):
                return False
            if consume:
                self._forget_expectation()
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
        if is_kill_phrase(text, self.kill_phrases):
            return True
        if addressed_to_jalen(text):
            return True
        # THE NARROW DOORS, and the echo test is not optional for any of them
        # - this gate is the echo defence, and each of these is a way to be
        # accepted without starting with his name. Jalen reading out an email
        # that says "Dylan, could you please..." must not be a command.
        #
        # For the whole time his voice is on air and for `door_tail` after it
        # stops: a sentence made of his own voice reaches here after the
        # endpoint silence and the transcription, not when the sound stopped
        # (ECHO_REACHES_THE_GATE_S). The test runs only for a sentence a door
        # would admit, so the ordinary refusal stays as cheap as it was.
        if widened_door(text, opened_by) is not None:
            return not self._sounds_like_its_own_voice(text, tail_s=door_tail)
        return False

    def _continues_last_utterance(self, text: str) -> bool:
        """Is this the rest of the sentence he was already saying?"""
        window = float(self.cfg.get_path("conversation.stitch_window_s", 8))
        if not self._last_user_text:
            return False
        if time.monotonic() - self._last_user_at > window:
            return False
        # NEVER ITS OWN VOICE, whatever else is true. Checked first because
        # the dangling-tail branch below is the one place a sentence can get
        # through without opening like a continuation at all, and the echo
        # is the failure that feeds itself.
        if self._sounds_like_its_own_voice(text):
            return False
        if is_continuation(text):
            return True
        # See the comment on _NOUN_EXPECTING_TAIL above: a tail waiting for a
        # noun admits a noun phrase, and nothing else. It used to admit
        # everything, because this line did not look at `text`.
        return expects_a_noun(self._last_user_text) and opens_like_a_fragment(text)

    # --------------------------------------------------- an open question
    def _expect_an_answer(self, question: str, turn_id: int,
                          window_s: float | None = None, kind: str = "") -> None:
        """
        Jalen has just finished ASKING him something. Open the window.

        Called from dispatch_turn AFTER _await_playback(), which is the
        whole reason twelve seconds ever looked too short: anchored at
        generation time instead, a forty-second answer would spend its
        entire window being read out loud, and every answer to it would
        arrive "late".

        `kind` is "bare-wake" for the window "Yes, Boss?" opens - see
        Expectation.
        """
        if window_s is None:
            window_s = self._answer_window_s
        now = time.monotonic()
        self._expecting = Expectation(
            question=question, turn_id=turn_id,
            opened_at=now, expires_at=now + window_s, kind=kind,
        )

    def _note_said(self, text: str) -> None:
        """
        Record what Jalen just said, and WHEN.

        The when is load-bearing: echo is an acoustic event with a physical
        time bound, and that bound is the only thing that reliably tells it
        from a person answering with the words the question offered. See
        ECHO_TAIL_S and _sounds_like_its_own_voice.
        """
        self._last_reply_text = text
        self._last_reply_at = time.monotonic()

    def _note_voice(self, piece: str) -> None:
        """
        Record a piece of a reply that is being spoken while it is written.

        The streamed path (every brain reply) hands the speaker one chunk at a
        time and files the reply with _note_said only after the last of it has
        been played. In between - which for a long answer is most of what
        Jalen says all day - the address gate asked "was that his own voice?"
        and had nothing to compare it with. The cap is 60,000 characters, about
        45 minutes of speech at the measured 22.4 characters a second: text is
        generated far faster than it is spoken, so what is being played can
        be a long way behind the end of what has been handed over, and
        dropping the oldest would drop exactly the part that is on air.
        """
        if not piece:
            return
        self._voice_so_far = (self._voice_so_far + " " + piece)[-60000:]

    def _forget_expectation(self) -> None:
        self._expecting = None

    # ------------------------------------------- a window that heard nothing
    @staticmethod
    def _vouching(vouched_by) -> str:
        """
        What was open when the sound began, as the word the audit rows carry:
        "" for nothing, "question" for something Jalen really asked, or the
        Expectation's own kind ("bare-wake").
        """
        if vouched_by is None:
            return ""
        return getattr(vouched_by, "kind", "") or "question"

    def _note_nothing_heard(self, stage: str, opened_by: str,
                            seconds: float | None = None,
                            vouched: str | None = None) -> None:
        """
        Leave a row when a microphone window ends with nothing to act on.

        There was none. "I didn't catch that" was spoken (and now "Yes,
        Boss?"), but a window that produced no speech, or audio that
        transcribed to an empty string, left no trace in data/audit.jsonl -
        so "how often does recognition return nothing, and which gate opened
        the window it happened in" could only be guessed at. That is the
        measurement this capability was blind to.

        stage       "no-speech"        the collector gave up: 2.5s with no
                                       sound above the VAD threshold
                    "empty-transcript" there was audio and the transcript
                                       came back empty
        opened_by   see the comment in run()
        vouched     what was open when the sound began, as _vouching words it
                    ("" for nothing); run() always says, so every row it
                    writes carries `vouched_by`, and rows from callers that do
                    not say simply do not have the key
        """
        detail: dict = {"stage": stage, "opened_by": opened_by}
        if vouched is not None:
            detail["vouched_by"] = vouched
        if seconds is not None:
            detail["seconds"] = seconds
        self.audit.write("system", summary=f"heard nothing - {stage}", detail=detail)

    def _note_how_it_got_in(self, door: str, text: str, opened_by: str,
                            vouched: str | None = None) -> None:
        """
        Leave a row when a sentence was acted on WITHOUT his name.

        The "ignored" row counts what the gate refused; nothing counted what
        the narrow doors admitted, so a door that was letting the television
        in could only be found by reading the log by eye. With this row the
        false accepts of each door are a count - `door` is one of
        router.all_doors' names, "after-the-window-closed" for an answer that
        began inside its window, or "bare-wake-window" for what came through
        the twelve seconds "Yes, Boss?" opens (which has no question behind
        it, so it is the least vouched-for way in there is) - and the figures
        in the commit that added them can be checked against live use.
        """
        detail = {"heard": text[:200], "opened_by": opened_by}
        if vouched is not None:
            detail["vouched_by"] = vouched
        self.audit.write(
            "system",
            summary=f"acted on without his name - {door}",
            detail=detail,
        )

    def _acknowledge_a_bare_wake(self) -> None:
        """
        The wake word fired and no speech followed. Say so like the name alone.

        Called from the microphone loop, so it must not block it: the work
        waits for playback and runs on its own thread.

        Not while a question is waiting on him. A RED confirmation, an AMBER
        stop-window or an ask_user already has his next sentence spoken for,
        and "I didn't catch that" over a delete confirmation - then
        cancelling it for "no answer" - is the failure the old announcement
        was caught doing.
        """
        if self._awaiting_confirmation or self._awaiting_stop or self._awaiting_reply:
            return
        threading.Thread(
            target=self._say_yes_and_listen, daemon=True, name="jalen-bare-wake",
        ).start()

    def _say_yes_and_listen(self) -> None:
        """
        "Yes, Boss?" - and then listen for the command, for the follow-up
        length.

        The same words the router gives the bare name typed or caught by the
        follow-up window, and for the same reason: it tells him he was
        heard. The window opens only if something was actually said. A muted
        Jalen says nothing, and a window stamped after silence is the
        unbounded exemption dispatch_turn already refuses to create - see
        `spoke_this_turn` there.
        """
        # Compared for a CHANGE, not for "later than a timestamp I took a
        # moment ago": time.monotonic() ticks every ~15ms on Windows, and
        # say() is called straight after, so "later than" can be false for a
        # reply that was spoken.
        before = self._last_reply_at
        try:
            self.say(NAME_ACK)
            self._await_playback()
            if self._last_reply_at != before:
                # kind="bare-wake": this window has a greeting behind it and no
                # question, so what comes through it is counted under its own
                # door (`bare-wake-window`) and its false accepts can be read
                # off the log on their own.
                self._expect_an_answer(NAME_ACK, self._turn_seq,
                                       window_s=self._follow_up_s,
                                       kind="bare-wake")
        finally:
            self._refresh_orb()

    def _another_turn_in_flight(self) -> bool:
        """
        Is some OTHER turn still working? The calling turn counts itself.

        In voice mode run() adds a turn to _active_turns before its thread
        starts, so a turn sees itself in the set; the other front doors
        never add one, so for them the set is empty.
        """
        with self._turn_lock:
            return len(self._active_turns) > 1

    def _rating_is_pending(self) -> bool:
        """
        Is he still being asked how that went?

        THE EXPIRY HAS TO BE HERE TOO. process() has always aged this out at
        RATING_EXPIRES_S, but the address gate only ever asked
        `_pending_rating is not None` - so from the moment Jalen said "how do
        you rate my work out of ten?" the name was not required again for as
        long as nobody answered. Five minutes was the documented bound and
        the gate did not know about it; the real bound was "until somebody
        next speaks", which is not a bound.

        Read-only on purpose. This runs on the microphone loop and the field
        belongs to the turn threads; process() and _take_rating do the
        clearing, and a stale entry expiring twice is not worth a lock.
        """
        pending = self._pending_rating
        if pending is None:
            return False
        return time.time() - pending.get("asked_at", 0) <= self.RATING_EXPIRES_S

    def _expectation_open(self) -> bool:
        """
        True while an answer to Jalen's own question is still welcome.

        READ-ONLY, and that is a fix rather than an omission. This runs on
        the microphone loop and _expect_an_answer writes the same attribute
        from a turn thread. The first version cleared an expired window
        here, which is a read-then-write with `time.monotonic()` and a
        comparison in between: a turn thread installing a fresh window in
        that gap had it destroyed by the mic loop's unconditional `= None`.
        The window is most likely to open at exactly that moment, because
        the turn thread reaches the tail the instant _await_playback
        returns, which is the instant the mic loop starts seeing frames
        again. Leaving the stale object costs one comparison per frame -
        measured at 0.09 microseconds against a 32ms frame.
        """
        pending = self._expecting
        return pending is not None and time.monotonic() < pending.expires_at

    def _open_expectation(self) -> "Expectation | None":
        """
        The question that is open RIGHT NOW, as an object, or None.

        What run() remembers at the instant a sound opens the microphone, so
        that the address gate can be told which window the sentence came out
        of - see `vouched_by` in should_act_on. Read once into a local, for
        the reason _expectation_open gives: the field belongs to the turn
        threads. Kept apart from _expectation_open rather than built on it,
        because several test doubles bind that one alone.
        """
        pending = self._expecting
        if pending is not None and time.monotonic() < pending.expires_at:
            return pending
        return None

    def _window_outlived_by_the_sentence(self, vouched_by: "Expectation") -> bool:
        """
        Did the question that opened the microphone for this sentence close
        while he was still saying it?

        True only when `vouched_by` is STILL the newest thing Jalen asked -
        the same object, so a later turn that asked something else, or asked
        nothing and cleared it, ends the exemption - and the sentence cannot
        have run on longer than one utterance can: see ANSWER_STT_SLACK_S.
        """
        pending = self._expecting
        if pending is None or pending is not vouched_by:
            return False
        return time.monotonic() <= pending.expires_at + self._answer_overrun_s

    # ------------------------------------------------- is that him, or us?
    def _sounds_like_its_own_voice(self, text: str, tail_s: float | None = None) -> bool:
        """
        Is this the question coming back through the microphone?

        There is no acoustic echo cancellation on this machine, so the
        microphone cannot tell Jalen's voice from his. Until now it never
        had to: a sentence that did not start with his name was refused
        whoever said it, and Jalen never says its own name. Opening a free
        window after a question removes that defence at the worst possible
        moment, so it is replaced here by comparing the WORDS against what
        was just said.

        TWO RULES, AND THE THREE-WORD FLOOR IS THE IMPORTANT PART.

            contiguous  three or more words appearing in that order inside
                        the reply. Catches both the whole sentence and any
                        tail of it.
            leaky       four or more words of which 85% appear in the reply
                        at all. Speech recognition of a speaker bleeding
                        through a microphone drops and mangles words, so an
                        exact run is not always there to find.

        Under three words, nothing is treated as echo. "ChatGPT or Gemini?"
        invites the answer "ChatGPT", and a filter eager enough to catch a
        two-word echo would reject the one answer the question asked for.

        Measured against the real corpus in data/audit.jsonl: 0 of the 853
        genuine user utterances is refused when paired with the reply that
        actually preceded it, and every one of the 725 question-shaped
        replies is refused when fed back whole, from its midpoint, and as
        its last five words. That second figure is reproducible from the
        log; an earlier version of this docstring cited "978 of 978" from a
        scratch script that is not in the repository, which a review
        correctly called unauditable.

        TIME IS THE GATE, AND TEXT IS ONLY THE TEST. An independent review
        of the first version of this found both halves of that wrong:

          - it compared against pending.question only, so while turn N's
            window was open, JALEN'S OWN REPLY TO TURN N+1 matched nothing
            and was accepted as the user. A self-triggering loop inside the
            gate whose entire purpose is refusing exactly that, on the
            ordinary path, needing no race and no second speaker.
          - and with no time bound, "just the last message" — the natural
            answer to "...read out, or just the last message?" — is a
            contiguous run inside the question and was silently dropped.
            That is the bug this whole feature exists to fix, reintroduced
            for the answers most worth having.

        So: only what Jalen said in the last ECHO_TAIL_S is a candidate,
        and BOTH the question and whatever he last said are compared. Past
        that window his voice is not in the room any more and a text match
        is the user using his words, which is what answering a question is.

        AND THE CLOCK STARTS WHEN HE STOPS TALKING, not when he started. An
        independent review of the doors added since reproduced this: the
        window was measured from the moment a reply was handed to the
        speaker, so a seventeen-second reply read back at five seconds or at
        ten was compared against nothing. Now his voice is a candidate for as
        long as the speaker is on air, and for `tail_s` after it stops
        (Speaker.quiet_for); on the streamed path, where the reply is only
        filed when it has finished, what has been handed over so far
        (`_voice_so_far`) is compared too.

        `tail_s` is ECHO_TAIL_S for the answer path, and the doors pass the
        longer ECHO_REACHES_THE_GATE_S. NOT FIXED, found while measuring it:
        even 3s from the end is shorter than the pipeline - endpoint silence
        and transcription put the gate 3.2s after the sound at the median - so
        an echo of a QUESTION usually reaches the gate after the answer path's
        tail has shut. Lengthening it would refuse "just the last message" as
        an answer to "...or just the last message?"; the fix is to judge by
        when the sound BEGAN, which needs a measured physical tail the log
        cannot give (the audio is not kept).
        """
        now = time.monotonic()
        tail = ECHO_TAIL_S if tail_s is None else tail_s
        # The longest any rule below looks back. Only the two-word rule uses
        # more than `tail`: it can afford to, because no answer is a two-word
        # reply said back.
        far = max(tail, getattr(self, "_door_echo_tail_s", ECHO_REACHES_THE_GATE_S))
        # `getattr`, not attribute access: the speaker and the streamed text
        # are new, and every test double built before them has neither. A
        # double without a speaker keeps the older clock - since the reply
        # was handed over - which is exactly what it always had.
        quiet_for = getattr(getattr(self, "speaker", None), "quiet_for", None)
        since_voice = quiet_for() if callable(quiet_for) else now - self._last_reply_at
        since_voice = min(since_voice, now - self._last_reply_at)
        candidates = []                       # (what he said, within `tail`?)
        if since_voice <= far:
            near = since_voice <= tail
            candidates.append((self._last_reply_text, near))
            streamed = getattr(self, "_voice_so_far", "")
            if streamed:
                candidates.append((streamed, near))
        pending = self._expecting
        if pending is not None and now - pending.opened_at <= far:
            candidates.append((pending.question, now - pending.opened_at <= tail))

        heard_words = _SPOKEN_WORD.findall((text or "").lower())
        span = len(heard_words)

        for said, near in candidates:
            said_words = _SPOKEN_WORD.findall((said or "").lower())
            if not said_words:
                continue
            # A TWO-WORD REPLY HEARD BACK WHOLE. "Yes, Boss?" is what a bare
            # wake word is answered with, and its echo - "Yes, boss." - is two
            # words, below the floor under which nothing is echo, so it was
            # accepted as the answer to its own question and cost a model turn.
            # The floor protects an ANSWER that happens to use words the
            # question offered ("ChatGPT" to "ChatGPT or Gemini?"); nobody
            # answers a two-word reply by saying it back, so this one looks
            # back as far as the doors do.
            if len(said_words) == 2 and span and span % 2 == 0 \
                    and heard_words == said_words * (span // 2):
                return True
            if not near or span < 3:
                continue
            for start in range(len(said_words) - span + 1):
                if said_words[start:start + span] == heard_words:
                    return True
            # EIGHTY PERCENT, AND THE TWO DECIMAL PLACES ARE THE WHOLE
            # POINT. At 85% a five-word echo with one mangled word scores
            # 0.83 and got through — "um which one did you mean" was not
            # caught, which is precisely the case the rule exists for. At
            # "one word of slack" a genuine four-word answer gets caught:
            # "eight out of ten" shares out/of/ten with "how do you rate my
            # work out of ten?", scores 3 of 4, and would have been refused
            # as Jalen's own voice. 0.80 separates them:
            #
            #     um which one did you mean      5/6 = 0.83   echo
            #     witch one did you mean         4/5 = 0.80   echo
            #     eight out of ten               3/4 = 0.75   his answer
            if span >= 4:
                vocabulary = set(said_words)
                hits = sum(1 for word in heard_words if word in vocabulary)
                if hits / span >= 0.80:
                    return True
        return False

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
            state = job.get("state")
            # Named the way devwork names it, so a background COMMAND (the
            # self-test) is not announced as "the coding job in jarvis".
            name = devwork._describe(job)
            name = name[:1].upper() + name[1:]
            if state == "finished" and job.get("summary"):
                # A background COMMAND (the self-test) already knows how to
                # describe its own result, so say that rather than "go and
                # review it" — there is nothing to review, the answer is the
                # sentence.
                line = f"{job.get('prompt', 'That')} finished: {job['summary']}"
            elif state == "finished" and job.get("kind") == "command":
                line = f"{name} finished after {minutes:.0f} minutes."
            elif state == "finished":
                line = (
                    f"{name} just finished after "
                    f"{minutes:.0f} minutes. Say review the coding job and "
                    "I'll tell you what it actually changed."
                )
            elif state == "timeout":
                line = f"{name} ran out of time after {minutes:.0f} minutes."
                if job.get("alive_pid"):
                    # devwork keeps holding a process its tree-kill could not
                    # end; he is the only one who can finish the job.
                    line += (
                        f" I couldn't stop it, though - process "
                        f"{job['alive_pid']} is still running. Say stop the "
                        "background job to try again, or end it in Task Manager."
                    )
            else:
                line = f"{name} failed: {job.get('error', 'no reason given')}."
            self.audit.write("system", summary=f"coding job {job.get('id')}: {state}",
                             detail={"folder": str(job.get("folder", ""))})
            self.say(line)

    def _close_stt(self) -> None:
        """Release the transcriber's connection pool, if it ever built one."""
        transcriber = getattr(self, "stt", None)
        if transcriber is not None:
            transcriber.close()

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
            # The transcriber now owns an httpx connection pool, held open
            # for five minutes at a time so a voice turn stops paying 301ms
            # to rebuild it. What we open, we close.
            #
            # Via a method, not self.stt.close: the tuple is built BEFORE
            # the loop's try/except, so an attribute that does not exist
            # yet raises out of shutdown entirely rather than being caught
            # and logged like every other failing step. Shutdown has to
            # survive a half-constructed Jalen — that is the whole point of
            # tests/test_crash_diagnosis.py.
            ("stt", self._close_stt),
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
