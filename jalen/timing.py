"""
How long a turn actually took, broken into the parts a person can feel.

WHY THIS EXISTS. "Why is it so slow" has been reported repeatedly, and there
has never been a way to answer it. The only per-turn evidence in the audit
log is the wall-clock gap between his utterance and Jalen's, and that number
conflates two completely different complaints:

    he waited                    <- a latency problem
    Jalen talked for a long time <- a length problem

They feel identical from the listening chair and have opposite fixes. A real
example, 21 Aug: 43 seconds between his question and the reply being logged.
That reads as catastrophic latency. It was not — SpeechStream.close() blocks
until playback ends and the audit line is written after it, so most of those
43 seconds were Jalen speaking a two-paragraph answer out loud. Tuning the
endpointer, the obvious response to "43 seconds", would have made things
worse while fixing nothing.

So this measures four moments and reports the gaps between them:

    speech_end   he stopped talking
    transcript   the words existed
    first_audio  the first sound came out of the speakers
    done         the reply finished playing

TIME TO FIRST AUDIO IS THE NUMBER THAT MATTERS. Everything before it is
silence with a person waiting in it. Everything after it is Jalen talking,
which is a length question, not a speed one. Reporting them separately is
the entire point of this module.

Marks are idempotent: the first one wins. `first_audio` is fired by the
speaker once per sentence played, and only the first is the moment the
silence ended.
"""
from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field

# Kept in memory, not in sqlite: this is for "how fast were you just now"
# and a session median, not for history. Bounded so a long session cannot
# grow it without limit.
MAX_HISTORY = 200

MARKS = ("speech_end", "transcript", "first_audio", "answer_audio", "done")


@dataclass
class TurnTimer:
    """One turn's stopwatch. Created when the microphone closes the phrase."""

    text: str = ""
    route: str = "?"          # "router" or "brain" — they have different budgets
    # WHICH ENGINE ANSWERED, AND WHETHER SPEECH CAME OFF THE DISK.
    #
    # Added because a timing line without them cannot be acted on. Measured
    # on this machine, these two facts account for nearly all of the wait:
    #
    #     TTS from the phrase cache      25ms
    #     TTS from edge-tts            ~3000ms
    #     STT via Groq, healthy        ~1600ms
    #     STT via Groq, stalled     up to 60000ms before the fallback
    #
    # So "thought=2662ms" on its own is a number to stare at, while
    # "thought=2662ms tts=network" is a diagnosis. Recording them is the
    # difference between answering "why was that slow" in a second and
    # re-running the whole investigation that produced these figures.
    stt_engine: str = ""      # "groq" | "moonshine" | ""
    tts_source: str = ""      # "cache" | "network" | ""

    # THE SILENCE BEFORE THE STOPWATCH STARTS.
    #
    # `speech_end` is stamped where run() receives the utterance from the
    # collector, and the collector only hands it over after the endpoint
    # threshold of continuous silence has already elapsed. So the mark named
    # speech_end is not when he stopped talking — it is 1400ms later on the
    # fast path (vad.fast_silence_ms) and 4000ms later on the patient one.
    #
    # He sits through every one of those milliseconds. Leaving them out of
    # the measurement does not make them shorter; it makes the number
    # unfalsifiable, because the one figure he can check against a stopwatch
    # is the only one it does not report.
    endpoint_ms: float = 0.0

    # WAS THE FIRST SOUND THE ANSWER, OR "GIVE ME A SECOND"?
    #
    # brain.ack_after_ms pushes a pre-rendered filler into the same stream
    # when a turn has produced no text after 1400ms, so on a tool-using turn
    # the first audio out of the speakers is the filler and `first_audio`
    # marks that. Measured over the 240 brain turns in data/audit.jsonl: 156
    # of them — 65% — have a transcript-to-first-audio gap between 1350 and
    # 1750ms, a single spike on the 1400ms timer. Nothing organic clusters
    # that tightly. The published median "thinking time" was the
    # acknowledgement firing.
    #
    # The filler is exactly one sentence, so counting audio starts tells the
    # two apart deterministically: with a filler the SECOND start is the
    # answer, without one the first is.
    filler_pushed: bool = False
    audio_starts: int = 0

    _marks: dict[str, float] = field(default_factory=dict)

    @property
    def turn_id(self) -> str:
        """
        A short, stable handle for ONE turn.

        His spec asked for a correlation id so a complaint can be traced to
        a line rather than to a time of day. Derived from the object's
        identity and the first mark, so it costs nothing and cannot drift.
        """
        seed = self._marks.get("speech_end", 0.0)
        return f"{(hash((id(self), seed)) & 0xFFFF):04x}"

    def mark(self, name: str) -> None:
        """
        Record a moment. The FIRST call for a name wins.

        Idempotent because first_audio is fired per sentence by the speaker,
        and only the first one is the moment the waiting stopped. Making the
        caller track that would put the logic in three places instead of one.
        """
        if name not in MARKS:
            raise ValueError(f"unknown mark {name!r} (expected one of {MARKS})")
        self._marks.setdefault(name, time.perf_counter())

    def has(self, name: str) -> bool:
        return name in self._marks

    def _gap(self, start: str, end: str) -> float | None:
        if start in self._marks and end in self._marks:
            return self._marks[end] - self._marks[start]
        return None

    @property
    def thinking_s(self) -> float | None:
        """Transcript ready -> first sound. The router or the brain."""
        return self._gap("transcript", "first_audio")

    @property
    def hearing_s(self) -> float | None:
        """He stopped talking -> the words existed. Endpoint plus STT."""
        return self._gap("speech_end", "transcript")

    @property
    def wait_s(self) -> float | None:
        """
        Endpoint -> the first sound of ANY kind, filler included.

        KEPT, AND NO LONGER THE HEADLINE. Every timing line ever written to
        data/audit.jsonl records this one, so renaming or re-anchoring it
        would silently break comparison against a month of history. What
        changed is the claim made about it: it used to be documented as "the
        whole silence he sits through", and it is neither whole (the
        endpoint silence happens before it starts) nor always silence (on
        65% of brain turns it ends on "Give me a second"). Use felt_wait_s.
        """
        return self._gap("speech_end", "first_audio")

    @property
    def endpoint_s(self) -> float | None:
        """The silence he sat in before the microphone decided he had finished."""
        return (self.endpoint_ms / 1000.0) if self.endpoint_ms else None

    @property
    def answer_wait_s(self) -> float | None:
        """Endpoint -> the first sound of the ANSWER, never the filler."""
        return self._gap("speech_end", "answer_audio")

    @property
    def filler_wait_s(self) -> float | None:
        """
        Endpoint -> the acknowledgement, when there was one.

        Reported separately rather than folded in, because it is a real
        improvement to how the wait FEELS and no improvement at all to how
        long it is. Adding it to the answer figure would let a change that
        speaks sooner and thinks no faster read as a speed-up.
        """
        if not self.filler_pushed:
            return None
        return self._gap("speech_end", "first_audio")

    @property
    def felt_wait_s(self) -> float | None:
        """
        THE HONEST NUMBER: he stopped talking -> he hears the answer.

        Endpoint silence plus the wait to real speech. This is the only
        figure here that corresponds to something he could time with a
        stopwatch, and it is larger than everything this module used to
        report. If one number is quoted, quote this one.
        """
        answer = self.answer_wait_s
        if answer is None:
            answer = self.wait_s
        if answer is None:
            return None
        return answer + (self.endpoint_ms / 1000.0)

    @property
    def speaking_s(self) -> float | None:
        """How long Jalen talked. A LENGTH measure, never a latency one."""
        return self._gap("first_audio", "done")

    def summary(self) -> str:
        """One audit line. Missing marks are omitted, never guessed at."""
        parts = [f"turn={self.turn_id}", f"route={self.route}"]
        for label, value in (
            # The four original labels keep their meanings exactly, so a
            # month of history stays comparable. The three new ones are what
            # that history could not answer.
            ("heard", self.hearing_s),
            ("thought", self.thinking_s),
            ("wait", self.wait_s),
            ("spoke", self.speaking_s),
            ("endpoint", self.endpoint_s),
            ("answer", self.answer_wait_s),
            ("felt", self.felt_wait_s),
        ):
            if value is not None:
                parts.append(f"{label}={value * 1000:.0f}ms")
        # Omitted rather than written as empty: a turn that never spoke has
        # no tts source, and "tts=" would read as one that failed.
        if self.stt_engine:
            parts.append(f"stt={self.stt_engine}")
        if self.tts_source:
            parts.append(f"tts={self.tts_source}")
        return "timing " + " ".join(parts)

    def spoken_report(self) -> str:
        """
        Said out loud when he asks how fast that was. Seconds, one decimal,
        because milliseconds mean nothing to a listener.
        """
        # felt_wait_s, not wait_s. He is the one who was waiting, and the
        # endpoint silence and the filler both happened to him.
        wait = self.felt_wait_s
        if wait is None:
            return "I don't have a complete measurement for that turn."
        spoke = self.speaking_s
        line = f"You waited {wait:.1f} seconds before I started talking"
        if spoke is not None:
            line += f", and I talked for {spoke:.1f}"
        return line + "."


class TimingLog:
    """Every completed turn this session, and the medians over them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: list[TurnTimer] = []

    def record(self, timer: TurnTimer) -> None:
        with self._lock:
            self._turns.append(timer)
            if len(self._turns) > MAX_HISTORY:
                del self._turns[0]

    @property
    def last(self) -> TurnTimer | None:
        with self._lock:
            return self._turns[-1] if self._turns else None

    def median_wait_s(self, route: str | None = None) -> float | None:
        with self._lock:
            values = [
                t.wait_s for t in self._turns
                if t.wait_s is not None and (route is None or t.route == route)
            ]
        return statistics.median(values) if values else None

    def report(self) -> str:
        """
        Spoken answer to "how fast are you". Router and brain turns are
        reported separately on purpose — a router hit should be under half a
        second and a brain turn cannot be, so one blended median hides
        whichever of the two has regressed.
        """
        last = self.last
        if last is None:
            return "I haven't answered anything yet this session, so there's nothing to measure."

        lines = [last.spoken_report()]
        router = self.median_wait_s("router")
        brain = self.median_wait_s("brain")
        if router is not None:
            lines.append(f"Typically {router:.1f} seconds for a command I handle myself")
        if brain is not None:
            joiner = ", and " if router is not None else "Typically "
            lines.append(f"{joiner}{brain:.1f} for one that needs thinking")
        return " ".join(lines).replace(" , ", ", ") + ("." if len(lines) > 1 else "")
