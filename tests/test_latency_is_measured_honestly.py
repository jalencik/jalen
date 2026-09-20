"""
The published latency median was the time to "Give me a second."

WHAT THE STOPWATCH USED TO CLAIM
--------------------------------
jarvis/timing.py documented `wait_s` as "the whole silence he sits through.
This is the latency number; if only one figure is ever reported, report this
one." It is neither the whole silence nor always silence, and both halves of
that are measurable.

ONE: THE STOPWATCH STARTS AFTER THE SILENCE HE SAT IN
-----------------------------------------------------
`speech_end` is stamped in run() the instant UtteranceCollector.feed() hands
the utterance over - and feed() only does that once the endpoint threshold of
continuous quiet has ALREADY elapsed. config/jarvis.yaml ships
vad.fast_silence_ms: 1400 and vad.silence_ms: 4000, so the mark named
"speech_end" is 1.4 seconds after he stopped talking on the common path and
4 seconds after on the patient one. He sits through every one of those
milliseconds and none of them were ever counted.

TWO: THE FINISH LINE WAS THE FILLER
-----------------------------------
brain.ack_after_ms pushes a pre-rendered "Give me a second." into the SAME
speech stream when a turn has produced no text after 1400ms. `first_audio` is
fired by Speaker._play once per sentence and the first one wins - so on a
tool-using turn, the moment being measured is the acknowledgement, not the
answer.

MEASURED over the 240 brain turns in data/audit.jsonl:

    thought (transcript -> first_audio)      n=240
      1250-1499ms   33
      1500-1749ms  123      <-- 156 of 240, 65%, on a 1400ms timer
      1750-1999ms    2

Nothing organic clusters that tightly. That spike IS brain.ack_after_ms.

SO THE HEADLINE FIGURE WAS WRONG IN BOTH DIRECTIONS AT ONCE: too small by the
endpoint silence at the front, and stopped early by the filler at the back.
p50 `wait` over the 316 complete turns in the log is 4275ms, and the true
end-of-speech-to-answer figure is not recoverable from that history at all -
which is why this is instrumentation and not a speed-up.

WHAT IS NOT CLAIMED
-------------------
Nothing here makes Jalen faster. It makes the number checkable against a
stopwatch, which is the precondition for any later claim that something did.
"""
from __future__ import annotations

import pytest

from jarvis.timing import MARKS, TurnTimer


def _timer(**kw) -> TurnTimer:
    return TurnTimer(**kw)


# ---------------------------------------------------------------------------
# THE ENDPOINT SILENCE
# ---------------------------------------------------------------------------
def test_the_endpoint_silence_is_counted():
    t = _timer()
    t.endpoint_ms = 1400
    t.mark("speech_end")
    t.mark("answer_audio")
    assert t.endpoint_s == pytest.approx(1.4)
    # felt = endpoint + (speech_end -> answer_audio). The second part is
    # ~0 here, so felt must still be at least the endpoint.
    assert t.felt_wait_s >= 1.4


def test_the_patient_endpoint_costs_more_and_says_so():
    fast, patient = _timer(), _timer()
    fast.endpoint_ms = 1400
    patient.endpoint_ms = 4000
    for t in (fast, patient):
        t.mark("speech_end")
        t.mark("answer_audio")
    assert patient.felt_wait_s > fast.felt_wait_s
    assert patient.felt_wait_s - fast.felt_wait_s == pytest.approx(2.6, abs=0.05)


def test_an_unmeasured_endpoint_is_omitted_rather_than_guessed_at():
    t = _timer()
    t.mark("speech_end")
    t.mark("answer_audio")
    assert t.endpoint_s is None
    assert "endpoint=" not in t.summary()


# ---------------------------------------------------------------------------
# THE FILLER
# ---------------------------------------------------------------------------
def test_with_no_filler_the_first_sound_is_the_answer():
    t = _timer()
    t.mark("speech_end")
    t.audio_starts += 1
    t.mark("first_audio")
    if t.audio_starts > (1 if t.filler_pushed else 0):
        t.mark("answer_audio")
    assert t.has("answer_audio")
    assert t.filler_wait_s is None


def test_with_a_filler_the_second_sound_is_the_answer():
    """
    THE BUG. The filler plays first, and before this the stopwatch stopped
    on it - reporting the acknowledgement as the answer on 65% of brain
    turns.
    """
    t = _timer()
    t.mark("speech_end")
    t.filler_pushed = True

    t.audio_starts += 1                      # "Give me a second."
    t.mark("first_audio")
    if t.audio_starts > 1:
        t.mark("answer_audio")
    assert not t.has("answer_audio"), "the filler was measured as the answer"

    t.audio_starts += 1                      # the actual reply
    if t.audio_starts > 1:
        t.mark("answer_audio")
    assert t.has("answer_audio")


def test_the_filler_and_the_answer_are_reported_separately():
    """
    Speaking sooner and thinking no faster is a real improvement to how the
    wait FEELS and no improvement at all to how long it is. Folding them
    into one figure lets the first read as the second.
    """
    t = _timer()
    t.mark("speech_end")
    t.filler_pushed = True
    t.mark("first_audio")
    t.mark("answer_audio")
    assert t.filler_wait_s is not None
    assert t.answer_wait_s is not None
    assert "answer=" in t.summary()


def test_a_turn_with_no_filler_reports_no_filler_figure():
    t = _timer()
    t.mark("speech_end")
    t.mark("first_audio")
    assert t.filler_wait_s is None


# ---------------------------------------------------------------------------
# WHAT IS QUOTED OUT LOUD
# ---------------------------------------------------------------------------
def test_the_spoken_report_quotes_the_figure_he_could_time_himself():
    """
    "How fast was that" is a question about the wall clock in his room, and
    the answer used to leave out the endpoint silence and stop on the
    filler. Both of those happened to him.
    """
    t = _timer()
    t.endpoint_ms = 1400
    t.mark("speech_end")
    t.filler_pushed = True
    t.mark("first_audio")
    t.mark("answer_audio")
    t.mark("done")
    spoken = t.spoken_report()
    assert "waited" in spoken
    assert t.felt_wait_s >= t.wait_s, (
        "the number said out loud is smaller than the one in the log"
    )


# ---------------------------------------------------------------------------
# A MONTH OF HISTORY STAYS COMPARABLE
# ---------------------------------------------------------------------------
def test_the_four_original_labels_keep_their_exact_meanings():
    """
    Every timing line in data/audit.jsonl carries heard/thought/wait/spoke.
    Re-anchoring or renaming any of them silently invalidates the only
    before-and-after evidence there is.
    """
    t = _timer()
    t.endpoint_ms = 1400
    t.mark("speech_end")
    t.mark("transcript")
    t.mark("first_audio")
    t.mark("answer_audio")
    t.mark("done")
    summary = t.summary()
    for label in ("heard=", "thought=", "wait=", "spoke="):
        assert label in summary, f"{label} disappeared from the audit line"
    assert t.wait_s == t._gap("speech_end", "first_audio")


def test_the_new_labels_are_additions_rather_than_replacements():
    t = _timer()
    t.endpoint_ms = 1400
    t.mark("speech_end")
    t.mark("transcript")
    t.mark("first_audio")
    t.mark("answer_audio")
    t.mark("done")
    summary = t.summary()
    for label in ("endpoint=", "answer=", "felt="):
        assert label in summary


def test_answer_audio_is_a_known_mark():
    assert "answer_audio" in MARKS
    with pytest.raises(ValueError):
        _timer().mark("not_a_mark")


def test_felt_falls_back_to_first_audio_when_the_answer_was_never_marked():
    """
    A turn that was interrupted, or whose stream was abandoned, may never
    stamp answer_audio. Reporting nothing would bias the median toward the
    turns that went well - the same mistake _finish_timing already refuses
    to make.
    """
    t = _timer()
    t.endpoint_ms = 1400
    t.mark("speech_end")
    t.mark("first_audio")
    assert t.answer_wait_s is None
    assert t.felt_wait_s is not None
    assert t.felt_wait_s >= 1.4


# ---------------------------------------------------------------------------
# THE SHIPPED CALLER. The properties above are worth nothing if the hook
# that actually fires on every sentence does not use them.
# ---------------------------------------------------------------------------
class _Speaker:
    last_source = "cache"


class _Jalen:
    """Just enough Jalen for the audio hook: a timer and a speaker."""

    def __init__(self, timer):
        self._turn_timer = timer
        self.speaker = _Speaker()

    _mark_first_audio = None        # bound below


from jarvis.app import Jalen as _RealJalen        # noqa: E402

_Jalen._mark_first_audio = _RealJalen._mark_first_audio


def test_the_real_hook_skips_the_filler_and_stops_on_the_answer():
    timer = TurnTimer()
    timer.mark("speech_end")
    timer.filler_pushed = True
    jalen = _Jalen(timer)

    jalen._mark_first_audio()                     # "Give me a second."
    assert timer.has("first_audio")
    assert not timer.has("answer_audio"), (
        "Speaker._play fired for the filler and the stopwatch stopped on it"
    )

    jalen._mark_first_audio()                     # the reply
    assert timer.has("answer_audio")


def test_the_real_hook_stops_on_the_first_sound_when_there_was_no_filler():
    timer = TurnTimer()
    timer.mark("speech_end")
    jalen = _Jalen(timer)
    jalen._mark_first_audio()
    assert timer.has("first_audio") and timer.has("answer_audio")


def test_the_real_hook_still_records_where_the_audio_came_from():
    """Unchanged behaviour, and the reason tts=cache/network is in the log."""
    timer = TurnTimer()
    timer.mark("speech_end")
    _Jalen(timer)._mark_first_audio()
    assert timer.tts_source == "cache"


def test_the_real_hook_survives_having_no_timer():
    """A turn can start audio before a timer is published; it must not raise."""
    jalen = _Jalen(None)
    jalen._mark_first_audio()
