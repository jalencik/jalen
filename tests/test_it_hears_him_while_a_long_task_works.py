"""
"It is still becoming deaf even after getting my prompt and then it is even
taking 10 seconds to respond to what's up ... it should not treat everything
at the same time ... it should take the necessary amount for each task."
                                                        - his words, 2026-10-24

A brain reply is spoken through a SpeechStream, and the stream holds the
speaker - its `speaking` flag and its lock - for the WHOLE brain turn: through
every tool call, a minute and more with nothing playing. Four things followed
from that one fact, and each is pinned here against the real Speaker:

  * the microphone loop treated a silent stream as Jalen talking and heard
    nothing but the wake word - his "what's up", and the "yes" to a "Confirm?"
    the brain had just asked, never reached a turn (Speaker.on_air);
  * "Hey Jalen" or Ctrl+Alt+J over that silence stopped a reply that was not
    playing, and the answer still to come was lost;
  * every other line - the answer to "what's up", ready at once - waited on
    the lock behind the long task's last word (Speaker.say, speak_between);
  * and the STILL_WORKING lines waited for a silence that, by `speaking`,
    never came. Their own tests faked a speaker that was never speaking.

Above the speaker: small talk went to the brain, which takes one turn at a
time; a sentence that had to wait for the brain was met with silence; and a
third sentence was turned away even when the router answers it without the
brain. Every test double here says what it fakes.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from jalen import app as app_module
from jalen.app import Jalen
from jalen.audio.tts import Speaker
from jalen.brain.router import IntentRouter
from jalen.config import CONFIG

from test_confirmation_deadlock import Cfg
from test_listening_does_not_obey_other_people_or_itself import Heard, Room, _Audit


def wait_until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return bool(condition())


@pytest.fixture
def speaker(monkeypatch):
    """
    A REAL Speaker - its lock, its flags, its streams and their threads are
    the code under test. Only the network and the sound card are replaced: a
    sentence renders at once and 'plays' for 30 ms, and a barge-in cuts it.
    """
    sp = Speaker(Cfg())
    sp.played = []

    def render(sentence):
        return np.zeros(160, dtype="float32"), 16000

    def play(pcm, rate):
        time.sleep(0.03)
        return not sp._interrupt.is_set()

    real_play_sentence = sp._play_sentence

    def play_sentence(text, pcm, rate):
        sp.played.append(text)
        return real_play_sentence(text, pcm, rate)

    monkeypatch.setattr(sp, "_render_cached", render)
    monkeypatch.setattr(sp, "_play", play)
    monkeypatch.setattr(sp, "_play_sentence", play_sentence)
    return sp


def _after_the_last_sentence(sp):
    playing, ended = sp._last_sentence()
    return sp.played and not playing and ended > 0


# ===========================================================================
# THE SPEAKER: on air is a sound in the room, not a session open
# ===========================================================================
def test_a_reply_waiting_on_the_model_is_off_air_though_its_stream_is_open(speaker, monkeypatch):
    monkeypatch.setattr(Speaker, "STREAM_GAP_S", 0.5)
    stream = speaker.open_stream()
    stream.push("Give me a second. ")
    assert wait_until(lambda: _after_the_last_sentence(speaker))
    assert speaker.speaking and speaker.on_air, "between two sentences of a reply it is still on air"
    assert wait_until(lambda: not speaker.on_air, timeout=3.0), (
        "a stream with nothing to say for longer than the gap is still 'speaking' to the microphone"
    )
    assert speaker.speaking, "the session is open: the turn is still working"
    assert speaker.quiet_for() > 0.0, "the echo test counts a silent stream as his voice in the room"
    stream.push("Here is the answer. ")
    assert stream.close(timeout=5) == "Give me a second. Here is the answer."


def test_a_question_said_inside_a_reply_leaves_the_microphone_open_the_moment_it_ends(speaker):
    """The brain's "Confirm?" is said inside its own turn's stream: his yes has to be heard."""
    stream = speaker.open_stream()
    assert wait_until(lambda: stream.live)
    assert speaker.say_now("Send it to Ali? Confirm?") is True
    assert speaker.speaking, "the reply's stream is still open, waiting for the answer"
    assert not speaker.on_air, "the microphone still took the waiting stream for Jalen talking"
    stream.close(timeout=5)


def test_a_say_session_is_on_air_from_start_to_end(speaker):
    """Nothing changes for say(): its sentences follow each other with nothing to wait for."""
    seen = []
    speaker.on_state = lambda state: seen.append(speaker.on_air) if state == "speaking" else None
    speaker.say("One. Two.")
    assert seen == [True]
    assert not speaker.on_air and not speaker.speaking


def test_another_turns_line_is_said_at_once_while_a_long_reply_waits_on_its_tools(speaker):
    stream = speaker.open_stream()
    stream.push("Give me a second. ")
    assert wait_until(lambda: speaker.played == ["Give me a second."])

    said = []
    started = time.monotonic()
    other_turn = threading.Thread(
        target=lambda: said.append(speaker.say("I'm here, Boss. Still working on it.")), daemon=True)
    other_turn.start()
    other_turn.join(5)

    assert said == [True], "the line waited for the long task to finish speaking"
    assert time.monotonic() - started < 2.0
    assert speaker.played[-2:] == ["I'm here, Boss.", "Still working on it."]
    assert not stream.finished, "the long task's reply was ended to make room for it"
    stream.push("Here is the answer. ")
    assert "Here is the answer." in stream.close(timeout=5), "the long task's answer was lost"


def test_a_line_the_stream_never_began_is_withdrawn_and_never_said_twice(speaker, monkeypatch):
    hold = threading.Event()

    def a_long_sentence(pcm, rate):
        hold.wait(5)
        return not speaker._interrupt.is_set()

    monkeypatch.setattr(speaker, "_play", a_long_sentence)
    stream = speaker.open_stream()
    stream.push("A long sentence of the reply. ")
    assert wait_until(lambda: speaker.played)
    assert stream.speak_between("Too late for this.", timeout=0.2) is None
    hold.set()
    stream.close(timeout=5)
    assert "Too late for this." not in speaker.played, "a line given up on was said anyway"


def test_a_line_cut_off_by_barge_in_says_so(speaker, monkeypatch):
    stream = speaker.open_stream()
    assert wait_until(lambda: stream.live)

    def cut(pcm, rate):
        speaker._interrupt.set()
        return False

    monkeypatch.setattr(speaker, "_play", cut)
    assert speaker.say("First of two. Second of two.") is False
    assert "Second of two." not in speaker.played
    stream.close(timeout=5)


def test_a_confirm_goes_to_the_stream_that_is_speaking_not_the_newest_one(speaker):
    """
    Two brain turns: the second opens its stream while the first still holds
    the speaker. The newest stream was `_active_stream`, so the first turn's
    "Confirm?" was handed to a stream that could not speak, waited 30 s, then
    blocked on the lock the first turn's own stream held: the deadlock again.
    """
    first = speaker.open_stream()
    assert wait_until(lambda: first.live)
    second = speaker.open_stream()
    time.sleep(0.05)
    assert speaker._active_stream is first and not second.live

    said = []
    asking = threading.Thread(target=lambda: said.append(speaker.say_now("Send it? Confirm?")),
                              daemon=True)
    asking.start()
    asking.join(5)
    try:
        assert said == [True], "the first turn's question went to a stream that could not speak it"
        assert "Confirm?" in speaker.played
    finally:
        first.close(timeout=5)
        assert wait_until(lambda: second.live), "the waiting stream never got the speaker"
        second.close(timeout=5)


def test_stop_wakes_the_stream_that_is_speaking(speaker):
    first = speaker.open_stream()
    first.push("Give me a second. ")
    assert wait_until(lambda: speaker.played)
    second = speaker.open_stream()
    time.sleep(0.05)
    speaker.stop()
    assert wait_until(lambda: first.finished, timeout=2), (
        "stop() nudged the newest stream, and the one asleep on the model's next sentence never woke"
    )
    second.close(timeout=5)


# ===========================================================================
# THE MICROPHONE LOOP: a silent stream does not make him deaf
# ===========================================================================
class _SilentReply:
    """A brain reply's stream open and waiting on its tools: a session, and nothing playing."""

    speaking = True
    on_air = False

    def __init__(self):
        self.stopped = 0

    def speaking_for(self):
        return 30.0

    def stop(self):
        self.stopped += 1

    def quiet_for(self):
        return 30.0


def test_the_yes_to_its_own_confirm_is_heard_while_the_reply_waits(monkeypatch):
    room = Room(monkeypatch, [Heard("yes")], speaker=_SilentReply(), barge_in="wake_word")
    room.jalen._awaiting_confirmation = True
    room.run()
    assert room.texts() == ["yes"], "the answer to the brain's own question was never heard"
    assert room.jalen.speaker.stopped == 0


def test_hey_jalen_over_a_silent_reply_opens_the_microphone_and_stops_nothing(monkeypatch):
    room = Room(monkeypatch, [Heard("what's up")], speaker=_SilentReply(),
                wake_first=True, barge_in="wake_word")
    room.run()
    assert room.texts() == ["what's up"]
    assert room.from_him() == [True]
    assert room.jalen.speaker.stopped == 0, "it stopped a reply that was not playing, losing its answer"
    assert room.rows("the wake word stopped playback") == []


def test_hey_jalen_over_a_reply_that_is_playing_still_stops_it(monkeypatch):
    """Unchanged: over his voice, the name still stops him (test_only_his_name_interrupts_him)."""
    class Playing(_SilentReply):
        on_air = True

        def stop(self):
            self.stopped += 1
            self.speaking = self.on_air = False

    room = Room(monkeypatch, [Heard("open chrome")], speaker=Playing(), wake_first=True,
                barge_in="wake_word")
    room.run()
    assert room.jalen.speaker.stopped == 1
    assert room.texts() == ["open chrome"]


def test_the_hotkey_over_a_silent_reply_opens_the_microphone():
    j = Jalen.__new__(Jalen)
    j.speaker = _SilentReply()
    j.muted = j.paused = False
    assert j._apply_signal("toggle") is True, "Ctrl+Alt+J during a long task opened no microphone"
    assert j.speaker.stopped == 0


def test_the_hotkey_over_his_voice_still_stops_him():
    j = Jalen.__new__(Jalen)
    j.speaker = _SilentReply()
    j.speaker.on_air = True
    j.muted = j.paused = False
    assert j._apply_signal("toggle") is False
    assert j.speaker.stopped == 1


def test_the_still_working_lines_are_said_while_the_stream_is_open_and_silent(monkeypatch):
    """Their own tests faked `speaking=False`; the turn's stream is speaking all along."""
    monkeypatch.setattr(Jalen, "STILL_WORKING", ((0.1, "first"), (0.2, "second"), (0.3, "third")))
    j = Jalen.__new__(Jalen)
    j.speaker = _SilentReply()
    j._awaiting_confirmation = j._awaiting_stop = j._awaiting_reply = False
    heard, done = [], threading.Event()
    company = threading.Thread(target=j._keep_him_company,
                               args=({"chars": 0}, heard.append, done, 0.01), daemon=True)
    company.start()
    assert wait_until(lambda: len(heard) == 3, timeout=3)
    done.set()
    company.join(2)
    assert heard == ["first", "second", "third"]


def test_no_company_line_for_a_turn_whose_stream_cannot_speak_yet(monkeypatch):
    """A turn waiting for the brain: its line would be heard after the other turn's answer."""
    monkeypatch.setattr(Jalen, "STILL_WORKING", ((0.05, "first"),))
    j = Jalen.__new__(Jalen)
    j.speaker = _SilentReply()
    j._awaiting_confirmation = j._awaiting_stop = j._awaiting_reply = False
    heard, done = [], threading.Event()
    company = threading.Thread(target=j._keep_him_company,
                               args=({"chars": 0, "live": lambda: False}, heard.append, done, 0.01),
                               daemon=True)
    company.start()
    time.sleep(0.3)
    done.set()
    company.join(2)
    assert heard == []


def test_the_orb_says_thinking_while_a_long_task_works_in_silence(monkeypatch):
    """The speaker tells the orb when a session opens and closes - not when a stream goes quiet."""
    room = Room(monkeypatch, [], speaker=_SilentReply(), barge_in="wake_word")
    j = room.jalen
    j.speaker.on_air = True
    j._active_turns.add(901)
    j._orb_listening = False
    j._refresh_orb = Jalen._refresh_orb.__get__(j)

    class _Mic:
        dropped = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def frames(self):
            for n in range(6):
                if n == 3:
                    j.speaker.on_air = False      # the sentence is over; the tools run on
                yield np.zeros(512, dtype=np.float32)

        def queued_seconds(self):
            return 0.0

        def drain(self):
            pass

    j.mic = _Mic()
    room.run()
    assert "speaking" in j.orb.states
    assert j.orb.states[-1] == "thinking", j.orb.states


def test_the_orb_tells_the_truth_about_a_silent_reply():
    j = Jalen.__new__(Jalen)
    j.speaker = _SilentReply()
    j.muted = j.paused = False
    j._active_turns = {901}
    j._awaiting_reply = j._awaiting_confirmation = False
    j.orb = SimpleNamespace(states=[], set_state=lambda state: j.orb.states.append(state))
    j._refresh_orb()
    assert j.orb.states == ["thinking"]
    j.speaker.on_air = True
    j._refresh_orb()
    assert j.orb.states[-1] == "speaking"


# ===========================================================================
# WHAT THE ROUTER ANSWERS IS ANSWERED AT ONCE, AND NEVER TURNED AWAY
# ===========================================================================
@pytest.fixture(scope="module")
def router():
    return IntentRouter(CONFIG)


@pytest.mark.parametrize("said,kind", [
    ("what's up", "chat"), ("whats up", "chat"), ("sup", "chat"), ("how are you", "chat"),
    ("how are things", "chat"), ("are you there", "here"), ("you there?", "here"),
    ("are you listening", "here"), ("can you hear me", "here"),
])
def test_small_talk_never_goes_to_the_brain(router, said, kind):
    intent = router.route(said)
    assert intent is not None and intent.tool == "small_talk", (said, intent)
    assert intent.args == {"kind": kind}


@pytest.mark.parametrize("said", [
    "what's up with my disk", "how are you doing that", "are you there yet with the report",
])
def test_a_real_question_that_starts_like_small_talk_is_not_small_talk(router, said):
    intent = router.route(said)
    assert intent is None or intent.tool != "small_talk", (said, intent)


@pytest.mark.parametrize("said", [
    "are you done", "are you finished yet", "is it done", "how long will it take",
])
def test_asking_about_the_task_is_answered_here(router, said):
    intent = router.route(said)
    assert intent is not None and intent.tool == "what_are_you_doing", (said, intent)


def test_would_route_counts_nothing_and_writes_no_miss(tmp_path, monkeypatch):
    from jalen.brain import router as router_module

    monkeypatch.setattr(router_module, "DATA_DIR", tmp_path)
    router = IntentRouter(CONFIG)
    router.log_misses = True
    hits, misses = router.hits, router.misses
    assert router.would_route("what's up") is True
    assert router.would_route("tell me a long story about dragons and castles") is False
    assert (router.hits, router.misses) == (hits, misses)
    assert not router._miss_log.exists(), "a question asked of the router was logged as a miss"
    assert router.route("tell me a long story about dragons and castles") is None
    assert router.misses == misses + 1 and router._miss_log.exists()


def _small_talker(monkeypatch, *ages):
    j = Jalen.__new__(Jalen)
    j.address = "Boss"
    now = time.time()
    tasks = [SimpleNamespace(created_at=now - age) for age in ages]
    monkeypatch.setattr(app_module.conversation, "live_tasks", lambda: list(tasks))
    return j


def test_small_talk_says_what_is_still_running(monkeypatch):
    # The turn asking is itself a live task, a moment old.
    j = _small_talker(monkeypatch, 0.2, 95)
    assert j._small_talk("chat") == (
        "I'm here, Boss. Still working on what you asked a minute ago - I'll tell you when it's done.")
    j = _small_talker(monkeypatch, 0.2, 25)
    assert "what you asked a moment ago" in j._small_talk("here")
    j = _small_talker(monkeypatch, 0.2, 600, 30)
    assert "what you asked 10 minutes ago" in j._small_talk("chat"), "the oldest one is what he is waiting on"


def test_small_talk_with_nothing_running(monkeypatch):
    j = _small_talker(monkeypatch, 0.2)
    assert j._small_talk("chat") == "All good, Boss. What do you need?"
    assert j._small_talk("here") == "I'm here, Boss."
    j.address = ""
    assert j._small_talk("here") == "I'm here."


def test_the_small_talk_lines_are_rendered_before_they_are_needed():
    for line in ("I'm here, Boss.", "All good, Boss. What do you need?", Jalen.TAKEN_FOR_LATER,
                 "I'm still working on two things, so I didn't take that one - say it again in a moment."):
        assert line in Speaker._COMMON_PHRASES, line
        assert len(line) <= Speaker.CACHEABLE_MAX_CHARS, line


class _Router:
    def __init__(self, *answered):
        self.answered = set(answered)

    def would_route(self, text):
        return text in self.answered


def _two_turns_already_working(room):
    room.jalen._active_turns.update({901, 902})
    room.jalen.said = []
    room.jalen.say = room.jalen.said.append


def test_a_sentence_the_router_answers_is_never_turned_away_for_being_busy(monkeypatch):
    room = Room(monkeypatch, [Heard("what's up")], wake_first=True, barge_in="wake_word")
    room.jalen.router = _Router("what's up")
    _two_turns_already_working(room)
    room.run()
    assert room.texts() == ["what's up"], "'what's up' was refused while two tasks ran"
    assert room.jalen.said == []


def test_a_third_sentence_for_the_brain_is_still_turned_away_and_told_plainly(monkeypatch):
    room = Room(monkeypatch, [Heard("research the history of rome")], wake_first=True,
                barge_in="wake_word")
    room.jalen.router = _Router("what's up")
    _two_turns_already_working(room)
    room.run()
    assert room.texts() == []
    assert room.jalen.said == [
        "I'm still working on two things, so I didn't take that one - say it again in a moment."]


def test_a_router_that_fails_costs_him_nothing_but_the_exemption(monkeypatch):
    class Broken:
        def would_route(self, text):
            raise RuntimeError("a rule broke")

    room = Room(monkeypatch, [Heard("what's up")], wake_first=True, barge_in="wake_word")
    room.jalen.router = Broken()
    _two_turns_already_working(room)
    room.run()
    assert room.texts() == []
    assert len(room.jalen.said) == 1


# ===========================================================================
# A TURN THAT WAITS FOR THE BRAIN IS TOLD SO; AN ANSWER IS NEVER LOST TO A
# STOPPED FILLER
# ===========================================================================
class _Stream:
    """A reply stream: what was pushed, and whether he stopped it."""

    def __init__(self):
        self.pushed = []
        self.finished = False
        self.live = True
        self.closed = False

    def push(self, piece):
        self.pushed.append(piece)

    def close(self):
        self.closed = True

    def abandon(self):
        pass


class _QuickCfg:
    """CONFIG, with the "Give me a second." filler due after 10 ms instead of 1.4 s."""

    def get_path(self, key, default=None):
        if key == "brain.ack_after_ms":
            return 10
        return CONFIG.get_path(key, default)


def a_brain_turn(*, busy, streams):
    j = Jalen.__new__(Jalen)
    j.cfg = _QuickCfg()
    j.muted = False
    j._voice_so_far = ""
    j._turn_timer = None
    j._awaiting_confirmation = j._awaiting_stop = j._awaiting_reply = False
    j._last_reply_text, j._last_reply_at = "", 0.0
    j.audit = _Audit()
    queue_of_streams = iter(streams)
    j.speaker = SimpleNamespace(speaking=False, on_air=False, max_spoken=700,
                                open_stream=lambda: next(queue_of_streams))
    j._run_coro = lambda value: value
    j.brain = SimpleNamespace(busy=lambda: busy)
    j.said = []
    j.say = j.said.append
    return j


def test_a_turn_that_has_to_wait_for_the_brain_hears_at_once_that_it_is_next():
    stream = _Stream()
    j = a_brain_turn(busy=True, streams=[stream])
    order = []

    def handle_with_brain(text, on_text=None):
        order.append(list(j.said))
        time.sleep(0.1)                      # long past the 10 ms filler
        on_text("Tomorrow will be sunny. ")
        return "Tomorrow will be sunny."

    j.handle_with_brain = handle_with_brain
    j.speak_brain_reply("what's the weather tomorrow")
    assert order == [[Jalen.TAKEN_FOR_LATER]], "he heard nothing until the other task was over"
    assert "Give me a second." not in "".join(stream.pushed), (
        "a waiting turn's filler would be heard after the other turn's answer"
    )
    assert "Tomorrow will be sunny." in "".join(stream.pushed)


def test_a_turn_with_the_brain_to_itself_says_nothing_extra():
    stream = _Stream()
    j = a_brain_turn(busy=False, streams=[stream])

    def handle_with_brain(text, on_text=None):
        time.sleep(0.1)
        on_text("Sunny. ")
        return "Sunny."

    j.handle_with_brain = handle_with_brain
    j.speak_brain_reply("what's the weather")
    assert j.said == []
    assert "Give me a second." in "".join(stream.pushed), "the ordinary filler is unchanged"


def test_an_answer_that_arrives_after_he_stopped_the_filler_is_still_spoken():
    """'I'll tell you when it's done' (Jalen._small_talk) has to be true."""
    first, second = _Stream(), _Stream()
    j = a_brain_turn(busy=False, streams=[first, second])

    def handle_with_brain(text, on_text=None):
        # "Hey Jalen" over "Give me a second." stopped the stream.
        first.finished, first.live = True, False
        on_text("Here is what I found. ")
        on_text("Three papers. ")
        return "Here is what I found. Three papers."

    j.handle_with_brain = handle_with_brain
    j.speak_brain_reply("research it")
    assert second.pushed == ["Here is what I found. ", "Three papers. "]
    assert second.closed


def test_an_answer_he_cut_off_part_way_is_not_started_again():
    first, second = _Stream(), _Stream()
    j = a_brain_turn(busy=False, streams=[first, second])

    def handle_with_brain(text, on_text=None):
        on_text("First part. ")
        first.finished = True                 # he talked over the answer itself
        on_text("Second part. ")
        return "First part. Second part."

    j.handle_with_brain = handle_with_brain
    j.speak_brain_reply("research it")
    assert second.pushed == [], "an answer he stopped was started again in a new stream"


def test_after_a_stop_the_answer_is_not_given_a_stream_of_its_own():
    """A kill phrase is a stop to all of it, as before."""
    first, second = _Stream(), _Stream()
    j = a_brain_turn(busy=False, streams=[first, second])

    def handle_with_brain(text, on_text=None):
        j._killed_at = time.monotonic()
        first.finished = True
        on_text("Here is what I found. ")
        return "Here is what I found."

    j.handle_with_brain = handle_with_brain
    j.speak_brain_reply("research it")
    assert second.pushed == []


def test_a_stop_is_stamped_when_he_says_it():
    import inspect

    source = inspect.getsource(Jalen._take_as_answer)
    kill = source.index("if is_kill_phrase(text, self.kill_phrases):")
    assert source.index("self._killed_at = time.monotonic()", kill) < source.index("self.kill.set()", kill)


def test_the_brain_says_when_a_turn_holds_it():
    from jalen.brain.agent import Brain

    brain = Brain.__new__(Brain)
    brain._lock = asyncio.Lock()
    assert brain.busy() is False
    seen = []

    async def a_turn():
        async with brain._lock:
            seen.append(brain.busy())

    asyncio.run(a_turn())
    assert seen == [True] and brain.busy() is False
    assert Brain.__new__(Brain).busy() is False, "a brain with no lock yet is not busy"


def test_a_brain_that_cannot_say_is_not_busy():
    j = Jalen.__new__(Jalen)
    assert j._brain_is_busy() is False
    j.brain = SimpleNamespace(busy=lambda: (_ for _ in ()).throw(RuntimeError("no")))
    assert j._brain_is_busy() is False
