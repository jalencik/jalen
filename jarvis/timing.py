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

MARKS = ("speech_end", "transcript", "first_audio", "done")


@dataclass
class TurnTimer:
    """One turn's stopwatch. Created when the microphone closes the phrase."""

    text: str = ""
    route: str = "?"          # "router" or "brain" — they have different budgets
    _marks: dict[str, float] = field(default_factory=dict)

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
        The whole silence he sits through. This is the latency number; if
        only one figure is ever reported, report this one.
        """
        return self._gap("speech_end", "first_audio")

    @property
    def speaking_s(self) -> float | None:
        """How long Jalen talked. A LENGTH measure, never a latency one."""
        return self._gap("first_audio", "done")

    def summary(self) -> str:
        """One audit line. Missing marks are omitted, never guessed at."""
        parts = [f"route={self.route}"]
        for label, value in (
            ("heard", self.hearing_s),
            ("thought", self.thinking_s),
            ("wait", self.wait_s),
            ("spoke", self.speaking_s),
        ):
            if value is not None:
                parts.append(f"{label}={value * 1000:.0f}ms")
        return "timing " + " ".join(parts)

    def spoken_report(self) -> str:
        """
        Said out loud when he asks how fast that was. Seconds, one decimal,
        because milliseconds mean nothing to a listener.
        """
        wait = self.wait_s
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
