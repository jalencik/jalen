"""
"... Say stop if you don't want that." - he said "stop", and it went ahead.

THE BUG
-------
By voice the AMBER window is safety.undo_window_s, 2 s as shipped, counted from
the moment the announcement finished. A sentence reaches process() only after
it has ended, waited out the endpoint silence and been transcribed: 3.2 s after
the sound ENDS at the median (1.4 s + 1.8 s - the repo's own figures, see
app.ECHO_TAIL_S). So "stop" said the instant Jalen finished arrived more than a
second after the window had shut and the action had run, and "stop" said OVER
the announcement was no better: the window opened when the barge-in stopped the
speaker, while the sentence was still being heard. The window offered a spoken
stop that almost never stopped anything.

NOW
---
A sentence of his that BEGAN before the window closed holds it until process()
has judged it, and at most app.ANSWER_HOLD_S (12 s) from when it began. The
microphone loop says what is on its way - the onset of the sentence it is
hearing or transcribing (`_hearing_since`), handed on to `_judging` when the
sentence is dispatched - and process() lets go right after _take_as_answer.

What does NOT change: silence. An announcement nobody answers still goes when
its window ends. A sentence that answers nothing lets the action go as soon as
it is judged, not at the end of the hold, and a sentence that began after the
window closed is not an answer to it and holds nothing.

The waits below run on the real clock, scaled down: a 0.3 s window and a 2 s
hold stand for the shipped 2 s and 12 s.
"""
from __future__ import annotations

import inspect
import threading
import time

import pytest

from jalen.app import ANSWER_STT_SLACK_S, SAYING_AN_ANSWER_S, ANSWER_HOLD_S, Jalen, answer_hold_s
from jalen.config import CONFIG, Cfg

from test_listening_does_not_obey_other_people_or_itself import Heard, Room, _Mic, _Stt, _Vad
from test_stop_window_takes_answers import ANNOUNCEMENT, _Stop, a_jalen, run_announce

WINDOW = 0.3          # stands for undo_window_s, 2 s as shipped
HOLD = 2.0            # stands for ANSWER_HOLD_S, 12 s as shipped
ARRIVES = 0.7         # when process() gets his words: after the window has shut


def a_listening_jalen():
    j = a_jalen()
    j._hearing_since = None
    j._judging = set()
    j._answer_hold_s = HOLD
    return j


def he_says(j, text, *, arrives=ARRIVES, began_at=None):
    """
    He begins to speak NOW - as the announcement ends - unless `began_at` says
    he began earlier, over it. Then what run() does with the sentence: it is
    heard and transcribed (`_hearing_since`, set here, before the window can
    close), handed on (`_judging`), and judged by process() `arrives` seconds
    from now, which is after the window has shut.
    """
    onset = time.monotonic() if began_at is None else began_at
    j._hearing_since = onset
    started = time.monotonic()

    def mic():
        time.sleep(max(0.0, arrives - (time.monotonic() - started)))
        j._judging.add(onset)
        j._hearing_since = None
        try:
            j.process(text, from_him=False, sound_began_at=onset)
        except _Stop:
            pass

    thread = threading.Thread(target=mic, daemon=True)
    thread.start()
    return thread


# --------------------------------------------------------------------------
# 1. A SPOKEN STOP NOW STOPS IT
# --------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["stop", "Jalen, stop.", "no, don't", "wait", "hold on"])
def test_stop_said_as_the_announcement_ends_stops_the_action(said):
    j = a_listening_jalen()
    worker, result = run_announce(j, window_s=WINDOW)
    sentence = he_says(j, said)
    worker.join(5)
    sentence.join(5)
    assert result["outcome"] == "cancelled by user", (
        f"{said!r} reached process() {ARRIVES}s after a {WINDOW}s window and the action had "
        f"already {result['outcome']!r}")
    assert result["took"] < HOLD, "it waited out the whole hold instead of his answer"


def test_stop_said_over_the_announcement_stops_the_action():
    """The barge-in case: the sentence began before the window opened at all."""
    j = a_listening_jalen()
    began = time.monotonic() - 0.5            # over the announcement, before the window opened
    j._hearing_since = began                  # still being heard as it plays
    worker, result = run_announce(j, window_s=WINDOW)
    sentence = he_says(j, "stop", began_at=began)
    worker.join(5)
    sentence.join(5)
    assert result["outcome"] == "cancelled by user"


def test_go_ahead_on_its_way_lets_it_go_when_it_arrives_not_at_the_end_of_the_hold():
    j = a_listening_jalen()
    worker, result = run_announce(j, window_s=WINDOW)
    sentence = he_says(j, "go ahead")
    worker.join(5)
    sentence.join(5)
    assert result["outcome"] == "went ahead"
    assert result["took"] < HOLD - 0.5


# --------------------------------------------------------------------------
# 2. WHAT DOES NOT CHANGE
# --------------------------------------------------------------------------
def test_silence_still_goes_when_the_window_ends():
    j = a_listening_jalen()
    worker, result = run_announce(j, window_s=WINDOW)
    worker.join(5)
    assert result["outcome"] == "went ahead"
    assert result["took"] < WINDOW + 0.6, f"nothing was said and it waited {result['took']:.2f}s"


@pytest.mark.parametrize("said", ["what time is it", "open chrome"])
def test_a_sentence_that_answers_nothing_lets_it_go_as_soon_as_it_is_judged(said):
    j = a_listening_jalen()
    worker, result = run_announce(j, window_s=WINDOW)
    sentence = he_says(j, said)
    worker.join(5)
    sentence.join(5)
    assert result["outcome"] == "went ahead"
    assert result["took"] < ARRIVES + 0.6, (
        f"it waited {result['took']:.2f}s - to the end of the hold, not for the sentence")
    assert j.routed == [said], "a sentence that is no answer is still a new instruction"


def test_a_sentence_that_never_arrives_holds_it_no_longer_than_the_hold():
    """Dropped somewhere that forgot to say so: the hold is the cap, not forever."""
    j = a_listening_jalen()
    worker, result = run_announce(j, window_s=WINDOW)
    j._hearing_since = time.monotonic()      # heard, and never handed on or let go
    worker.join(10)
    assert result["outcome"] == "went ahead"
    assert HOLD - 0.1 <= result["took"] < HOLD + 1.0, result["took"]


def test_a_sentence_that_began_after_the_window_closed_holds_nothing():
    """
    A began inside the window and holds it; B begins after the window closed,
    while A is still being judged. When A turns out to answer nothing the action
    goes - B is not an answer to a window that had shut before it began.
    """
    j = a_listening_jalen()
    worker, result = run_announce(j, window_s=WINDOW)
    a = time.monotonic()
    j._hearing_since = a
    time.sleep(WINDOW + 0.05)                # the window's 2 s are over; A holds it
    assert worker.is_alive(), "nothing held the window for A"
    j._judging.add(a)
    j._hearing_since = None
    time.sleep(0.05)
    j._hearing_since = time.monotonic()      # B, after the window closed
    time.sleep(0.2)
    j._judged(a)                             # A answered nothing
    worker.join(5)
    assert result["outcome"] == "went ahead"
    assert result["took"] < 1.2, f"B held it to {result['took']:.2f}s"


# --------------------------------------------------------------------------
# 3. WHAT HOLDS THE WINDOW, EXACTLY
# --------------------------------------------------------------------------
def test_nothing_on_its_way_holds_nothing():
    assert Jalen.__new__(Jalen)._held_for(100.0) == 0.0
    j = a_listening_jalen()
    assert j._held_for(100.0) == 0.0


def test_a_sentence_being_heard_holds_until_its_cap():
    j = a_listening_jalen()
    j._hearing_since = 99.5
    assert j._held_for(100.0) == 99.5 + HOLD


def test_a_sentence_handed_on_holds_until_its_cap():
    j = a_listening_jalen()
    j._judging = {98.0, 99.0}
    assert j._held_for(100.0) == 99.0 + HOLD


def test_one_that_began_at_or_after_the_close_holds_nothing():
    j = a_listening_jalen()
    j._hearing_since = 100.0
    j._judging = {100.5}
    assert j._held_for(100.0) == 0.0


def test_a_sentence_over_the_announcement_holds_from_when_it_began():
    j = a_listening_jalen()
    j._hearing_since = 97.0                    # began before the window opened
    assert j._held_for(100.0) == 97.0 + HOLD


def test_judging_one_lets_go_of_it_and_only_it():
    j = a_listening_jalen()
    j._judging = {98.0, 99.0}
    j._judged(99.0)
    j._judged(None)                            # typed text: nothing to let go of
    assert j._judging == {98.0}


# --------------------------------------------------------------------------
# 4. process() LETS GO ONLY ONCE THE ANSWER IS WHERE THE WINDOW WILL FIND IT
# --------------------------------------------------------------------------
class _Watching(set):
    """A `_judging` that notes what had happened when the sentence was let go."""

    def __init__(self, j, *items):
        super().__init__(items)
        self.j, self.let_go = j, []

    def discard(self, item):
        self.let_go.append((item, self.j._answer_q.qsize(), list(self.j.routed)))
        super().discard(item)


@pytest.mark.parametrize("said,answer", [("stop", False), ("no, don't", False), ("go ahead", True)])
def test_the_answer_is_on_its_queue_before_the_window_is_let_go(said, answer):
    j = a_listening_jalen()
    j._awaiting_stop, j._stop_question = True, ANNOUNCEMENT
    j._judging = _Watching(j, 42.0)
    j.process(said, from_him=False, sound_began_at=42.0)
    assert j._judging.let_go[0] == (42.0, 1, []), (
        "let go before the answer was queued: the window could close in the gap and run "
        "the action on a stop already given")
    assert j._answer_q.get_nowait() is answer and 42.0 not in j._judging


def test_a_sentence_that_answers_nothing_is_let_go_before_it_is_routed():
    j = a_listening_jalen()
    j._awaiting_stop, j._stop_question = True, ANNOUNCEMENT
    j._judging = _Watching(j, 42.0)
    with pytest.raises(_Stop):
        j.process("open chrome", from_him=False, sound_began_at=42.0)
    assert j._judging.let_go == [(42.0, 0, [])], "the window waited for the whole new turn"
    assert j.routed == ["open chrome"]


def test_the_order_in_process_is_pinned():
    source = inspect.getsource(Jalen.process)
    taken = source.index("self._take_as_answer(")
    let_go = source.index("self._judged(sound_began_at)")
    assert taken < let_go < source.index("if taken:") < source.index("self.router.route")


# --------------------------------------------------------------------------
# 5. THE MICROPHONE LOOP SAYS WHAT IS ON ITS WAY - driven through the real run()
# --------------------------------------------------------------------------
class _VadOn(_Vad):
    """Speech on the given idle frames (1-based) and silence on every other."""

    def __init__(self, *on):
        self.on, self.calls = set(on), 0

    def probability(self, frame):
        self.calls += 1
        return 1.0 if self.calls in self.on else 0.0


class _Noting(_Stt):
    """Recognition that notes what the window could see while it ran."""

    def __init__(self, collector, jalen, seen):
        super().__init__(collector)
        self.jalen, self.seen = jalen, seen

    def transcribe(self, utterance):
        self.seen.append((getattr(self.jalen, "_hearing_since", None),
                          set(getattr(self.jalen, "_judging", ()))))
        return super().transcribe(utterance)


def a_stop_window_room(monkeypatch, said, **room):
    """run() with an AMBER window open: a sound opens the answer window on the
    first frame, the sentence is heard on the second, and the third is quiet."""
    r = Room(monkeypatch, [Heard(said, takes=0.4)], **room)
    j = r.jalen
    j._awaiting_stop, j._stop_question = True, ANNOUNCEMENT
    j.vad = _VadOn(1)
    j.mic = _Mic(3 + (4 if room.get("sync_turns") else 0))
    r.seen = []
    j.stt = _Noting(r.collector, j, r.seen)
    r.began = r.clock.now
    return r


def test_while_his_answer_is_transcribed_the_window_can_see_it(monkeypatch):
    r = a_stop_window_room(monkeypatch, "stop").run()
    assert r.seen == [(r.began, set())], (
        "the sentence was on its way and nothing said so: the window shut on it")


def test_it_is_handed_on_with_the_sentence_and_the_microphone_lets_go(monkeypatch):
    r = a_stop_window_room(monkeypatch, "stop").run()
    assert r.texts() == ["stop"]
    assert r.dispatched[0][5] == r.began, "the turn does not know which sentence it carries"
    assert r.jalen._judging == {r.began}, "handed on to process(), which has not judged it yet"
    assert r.jalen._hearing_since is None


def test_a_sentence_that_is_dropped_holds_nothing_afterwards(monkeypatch):
    """Recognition came back empty: dropped on the microphone's own thread."""
    r = a_stop_window_room(monkeypatch, "").run()
    assert r.seen == [(r.began, set())] and r.texts() == []
    assert r.jalen._hearing_since is None and r.jalen._judging == set()


def test_a_turn_that_never_judged_its_sentence_lets_go_when_it_ends(monkeypatch):
    """process() is replaced here and never lets go: dispatch_turn's finally does."""
    r = a_stop_window_room(monkeypatch, "stop", sync_turns=True).run()
    assert r.processed and r.processed[0][0] == "stop"
    assert r.jalen._judging == set()


def test_the_loop_adds_before_it_starts_the_turn_and_passes_the_onset():
    source = inspect.getsource(Jalen.run)
    added = source.index("self._judging.add(sound_began_at)")
    assert added < source.index("dispatch_turn, args=(text")
    assert "args=(text, turn_id, timer, from_him, door, sound_began_at)" in source
    body = source[source.index("def dispatch_turn"):source.index("self.prewarm()")]
    assert "sound_began_at=sound_began_at" in body
    assert body.index("self._judged(sound_began_at)") > body.index("finally:")


# --------------------------------------------------------------------------
# 6. THE HOLD IS DERIVED FROM WHAT IT IS MADE OF
# --------------------------------------------------------------------------
def test_the_hold_is_the_sum_of_saying_it_the_longest_endpoint_and_transcription():
    assert ANSWER_HOLD_S == SAYING_AN_ANSWER_S + 4.0 + ANSWER_STT_SLACK_S == 12.0
    assert answer_hold_s(CONFIG) == ANSWER_HOLD_S       # vad.silence_ms ships 4000
    assert answer_hold_s(Cfg({"vad": {"silence_ms": 1000}})) == 9.0


def test_the_hold_covers_a_short_refusal_at_the_slow_end_of_the_pipeline():
    word, patient_endpoint, stt_p95 = 1.0, 4.0, 4.4
    assert ANSWER_HOLD_S >= word + patient_endpoint + stt_p95


def test_the_window_alone_could_not_have_heard_a_stop():
    """The arithmetic of the bug: the window shut before the median answer arrived."""
    window = float(CONFIG.get_path("safety.undo_window_s"))
    endpoint = float(CONFIG.get_path("vad.fast_silence_ms")) / 1000.0
    stt_median = 1.8
    assert window < endpoint + stt_median


def test_jalen_takes_its_hold_from_config_and_the_stop_window_uses_it():
    assert "self._answer_hold_s = answer_hold_s(cfg)" in inspect.getsource(Jalen.__init__)
    assert "hold=self._held_for" in inspect.getsource(Jalen._wait_for_stop)
