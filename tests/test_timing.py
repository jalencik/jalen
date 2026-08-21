"""
Making "why is it so slow" a question with a number for an answer.

The failure this measures against is a real one from data/audit.jsonl: a
43-second gap between his question and Jalen's reply on 21 Aug, which reads
as catastrophic latency and was not. SpeechStream.close() blocks until
playback ends and the reply is logged after it, so most of those 43 seconds
were Jalen speaking a two-paragraph answer aloud.

Two complaints, identical in the log, opposite fixes:

    he waited                      -> latency
    Jalen talked for a long time   -> length

Everything here exists to keep those two apart.
"""
from __future__ import annotations

import pytest

from jarvis.timing import MARKS, TimingLog, TurnTimer


@pytest.fixture
def clock(monkeypatch):
    """A stopwatch we drive by hand, so timings are exact, not approximate."""
    now = {"t": 1000.0}
    monkeypatch.setattr("jarvis.timing.time.perf_counter", lambda: now["t"])

    def advance(seconds: float) -> None:
        now["t"] += seconds

    return advance


def test_the_four_gaps_are_measured_independently(clock):
    timer = TurnTimer(route="brain")
    timer.mark("speech_end")
    clock(0.4)
    timer.mark("transcript")
    clock(1.1)
    timer.mark("first_audio")
    clock(38.0)
    timer.mark("done")

    assert timer.hearing_s == pytest.approx(0.4)
    assert timer.thinking_s == pytest.approx(1.1)
    assert timer.wait_s == pytest.approx(1.5)
    assert timer.speaking_s == pytest.approx(38.0)


def test_the_43_second_turn_is_correctly_diagnosed(clock):
    """
    The whole point. A 43-second turn where he waited 1.5s and Jalen talked
    for 41.5s is a LENGTH problem. Reporting it as latency would send you to
    tune the endpointer, which was already correct.
    """
    timer = TurnTimer(route="brain")
    timer.mark("speech_end")
    clock(0.5)
    timer.mark("transcript")
    clock(1.0)
    timer.mark("first_audio")
    clock(41.5)
    timer.mark("done")

    assert timer.wait_s < 2.0, "he barely waited"
    assert timer.speaking_s > 40.0, "but it talked for forty seconds"


def test_first_audio_keeps_the_first_mark_not_the_last(clock):
    """
    The speaker fires on_audio_start once per SENTENCE. Only the first is
    the moment the silence ended; taking the last would report the start of
    the final sentence and make every multi-sentence reply look slow.
    """
    timer = TurnTimer()
    timer.mark("speech_end")
    clock(1.0)
    timer.mark("first_audio")   # sentence one
    clock(5.0)
    timer.mark("first_audio")   # sentence two
    clock(5.0)
    timer.mark("first_audio")   # sentence three

    assert timer.wait_s == pytest.approx(1.0)


def test_missing_marks_report_none_rather_than_a_guess(clock):
    """
    A muted turn, or a tool-only action with nothing to say, never produces
    audio. Inventing a number for it would poison the median with fiction.
    """
    timer = TurnTimer()
    timer.mark("speech_end")
    clock(0.3)
    timer.mark("transcript")

    assert timer.wait_s is None
    assert timer.speaking_s is None
    assert timer.hearing_s == pytest.approx(0.3)


def test_summary_omits_what_it_could_not_measure(clock):
    timer = TurnTimer(route="router")
    timer.mark("speech_end")
    clock(0.2)
    timer.mark("transcript")

    line = timer.summary()
    assert "route=router" in line
    assert "heard=200ms" in line
    assert "wait=" not in line, "an unmeasured gap must not appear at all"


def test_an_unknown_mark_is_a_programming_error():
    with pytest.raises(ValueError):
        TurnTimer().mark("finished_maybe")


def test_every_documented_mark_is_accepted():
    timer = TurnTimer()
    for name in MARKS:
        timer.mark(name)
    assert all(timer.has(name) for name in MARKS)


# --------------------------------------------------------------- the log
def make(route: str, wait: float, clock) -> TurnTimer:
    timer = TurnTimer(route=route)
    timer.mark("speech_end")
    clock(wait)
    timer.mark("first_audio")
    clock(1.0)
    timer.mark("done")
    return timer


def test_router_and_brain_medians_stay_separate(clock):
    """
    A router hit should be under half a second; a brain turn cannot be. One
    blended median hides whichever of the two has regressed — which is
    precisely the regression you most want to catch.
    """
    log = TimingLog()
    for wait in (0.2, 0.3, 0.25):
        log.record(make("router", wait, clock))
    for wait in (2.0, 3.0, 2.5):
        log.record(make("brain", wait, clock))

    assert log.median_wait_s("router") == pytest.approx(0.25)
    assert log.median_wait_s("brain") == pytest.approx(2.5)
    assert log.median_wait_s() == pytest.approx(1.15)


def test_report_before_anything_has_happened_says_so():
    assert "haven't answered anything yet" in TimingLog().report()


def test_report_names_the_last_turn(clock):
    log = TimingLog()
    log.record(make("brain", 2.0, clock))
    report = log.report()
    assert "2.0 seconds" in report
    assert "waited" in report


def test_history_is_bounded(clock):
    """A long session must not grow this without limit."""
    from jarvis.timing import MAX_HISTORY

    log = TimingLog()
    for _ in range(MAX_HISTORY + 50):
        log.record(make("router", 0.2, clock))
    assert len(log._turns) == MAX_HISTORY


def test_a_turn_is_not_finished_until_the_audio_is():
    """
    The wiring bug this exists to catch, which the arithmetic tests above
    could never see: say() is ASYNCHRONOUS. It hands playback to a worker
    thread and returns at once, so a router turn's dispatch thread reached
    its `finally` while Jalen was still talking — sometimes before the sound
    had started. "done" was marked there, producing turns that spoke for
    zero seconds and often carried no first_audio mark at all. That does not
    merely lose data; it drags the median toward zero, so a latency
    regression would show up as an improvement.

    _await_playback() closes that gap. This drives it with a fake speaker
    whose `speaking` flag flips on a schedule, exactly as the real one does.
    """
    import threading
    import time as real_time

    from jarvis.app import Jalen

    class LateStartingSpeaker:
        """Starts 100ms after the turn ends, then plays for 200ms."""

        def __init__(self):
            self.speaking = False
            threading.Timer(0.10, self._start).start()

        def _start(self):
            self.speaking = True
            threading.Timer(0.20, self._stop).start()

        def _stop(self):
            self.speaking = False

    class Bare:
        speaker = LateStartingSpeaker()

    started = real_time.monotonic()
    Jalen._await_playback(Bare(), grace_s=0.5, limit_s=5.0)
    waited = real_time.monotonic() - started

    assert waited >= 0.25, (
        f"returned after {waited:.3f}s — it did not wait for playback to finish"
    )
    assert Bare.speaker.speaking is False


def test_await_playback_gives_up_rather_than_hanging_forever():
    """
    A wedged audio device must not pin a dispatch thread for the life of the
    process. The bound is what makes waiting safe.
    """
    import time as real_time

    from jarvis.app import Jalen

    class StuckSpeaker:
        speaking = True

    class Bare:
        speaker = StuckSpeaker()

    started = real_time.monotonic()
    Jalen._await_playback(Bare(), grace_s=0.01, limit_s=0.15)
    assert real_time.monotonic() - started < 1.0


def test_the_turn_loop_actually_calls_the_wait():
    """
    _await_playback being correct is worth nothing if the dispatch path
    stops calling it. That is a one-line deletion away, and the symptom —
    slightly optimistic timings — is invisible without this test.
    """
    import inspect

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen.run)
    assert "_await_playback()" in source, (
        "dispatch_turn no longer waits for playback — every timing is now short"
    )
    assert source.index("_await_playback()") < source.index("_finish_timing(timer)"), (
        "the wait must happen BEFORE the turn is recorded, or it changes nothing"
    )


def test_speaker_exposes_the_audio_start_hook():
    """
    The mark comes from Speaker._play, the single point both the plain and
    the streaming path funnel through. If that attribute is renamed, timing
    silently stops being collected and every wait_s becomes None.
    """
    from jarvis.audio.tts import Speaker

    assert hasattr(Speaker, "_play")
    import inspect

    assert "on_audio_start" in inspect.getsource(Speaker._play), (
        "Speaker._play no longer signals first audio — timing will silently stop working"
    )
