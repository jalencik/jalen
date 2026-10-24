"""
THE REVIEW OF PULL REQUEST #1, ANSWERED ONE FINDING AT A TIME.

A code review of the branch (23 commits, 77 files) reported fifteen findings.
Each was checked against the code before anything was changed; all fifteen
were real. Five had their tests added beside the code they are about:

   3  "what's wrong with you?" always said the last run crashed
                                               tests/test_it_does_not_go_deaf.py
   6  the microphone said "It's back now" when it opened, not when it heard
                                               tests/test_it_does_not_go_deaf.py
   7  the model that reads an unclear answer was shown text Jalen did not write
                                         tests/test_he_answers_like_a_person.py
   9  a forward into his channel was announced without its words
                                          tests/test_any_action_in_telegram.py
  14  a new Groq connection for every unclear answer
                                         tests/test_he_answers_like_a_person.py

The other ten are pinned here, each test RED against the code before its fix.
Finding 15 (config read mid-call) was taken where the read was new to this
branch and in a class: the question windows, and the kill key's standby
setting in the hotkey listener. Brain._hook_timeout_s reads its config in
start(), once per client, and messaging.py's tools read CONFIG when called
throughout, a pattern older than this branch. Those two stay as they are.
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jalen import runtime
from jalen.app import ECHO_TAIL_S, Jalen
from jalen.config import CONFIG
from jalen.tools import messaging

from test_a_message_is_heard_before_it_is_sent import _asked, question
from test_scheduled_posts import tg  # noqa: F401 - fixture
from test_stop_window_takes_answers import hear, stop_window_open

ROOT = Path(__file__).resolve().parent.parent
HIS_CHANNEL = "AI engineering & Machine learning"


# ===========================================================================
# 1. "OKAY, DON'T WORRY ABOUT IT" APPROVED A RED ACTION
#    Beside a yes, "don't worry" was taken out as a courtesy - which took the
#    refusal out with it, and the "okay" approved the send.
# ===========================================================================
@pytest.mark.parametrize("said", [
    "Okay, don't worry about it.", "Sure, don't worry about it",
    "Yeah no worries, forget about it", "ok, don't worry about it, I'll send it myself",
    "okay no problem, I'll do it myself", "yeah, forget that",
])
def test_never_mind_said_politely_is_never_a_yes(said):
    assert Jalen._parse_yes_no(said) is False, said


@pytest.mark.parametrize("said", ["yes, no problem", "sure, no worries", "okay, not a problem",
                                  # "...it myself" refuses; "myself" alone does not
                                  "yes, and send a copy to myself"])
def test_a_yes_said_twice_is_still_a_yes(said):
    assert Jalen._parse_yes_no(said) is True, said


# ===========================================================================
# 2. THE ECHO OF A FORWARD'S OR AN EMAIL'S WORDS APPROVED IT
# ===========================================================================
FORWARD = question("forward_telegram_message", chat="Ali", to="Rodion",
                   message="sure see you at six")
EMAIL = question("send_email", to="prof@uni.edu", subject="Sure, see you at six")


@pytest.mark.parametrize("asked", [FORWARD, EMAIL])
@pytest.mark.parametrize("heard", ["sure see ya", "sure see you"])
def test_a_garbled_echo_of_the_quoted_words_is_not_his_answer(asked, heard):
    assert _asked(asked, ECHO_TAIL_S - 0.5)._echoes_the_confirmation(heard), (
        f"{heard!r} would approve: {asked!r}")


@pytest.mark.parametrize("asked", [FORWARD, EMAIL])
def test_his_own_yes_to_them_still_counts(asked):
    assert not _asked(asked, 1.0)._echoes_the_confirmation("yes")


# ===========================================================================
# 4. THE WINDOW RAN OUT WHILE THE QUESTION WAS BEING ASKED AGAIN
# ===========================================================================
def test_the_window_is_held_while_the_question_is_asked_again():
    from test_a_late_yes_is_still_a_yes import run_confirm
    from test_saying_stop_stops_it import a_listening_jalen, he_says

    j = a_listening_jalen()
    j._answer_hold_s = 0.3              # the unclear sentence's own hold: shorter than asking again
    asking_again = threading.Event()

    def say_blocking(text):
        j.said.append(text)
        if text.startswith("Sorry, I missed that."):
            asking_again.set()
            time.sleep(0.8)             # a message question takes seconds to say

    j.say_blocking = say_blocking
    worker, result = run_confirm(j)                         # a 0.3 s window
    he_says(j, "hmm the blue one", arrives=0.1)
    assert asking_again.wait(3), "the unclear answer was not asked about again"
    time.sleep(0.6)
    assert "answer" not in result, (
        f"the wait gave up while the question was being asked again: {result.get('answer')!r}")
    time.sleep(0.4)                                          # asked; the window starts again
    he_says(j, "yes", arrives=0.05)
    worker.join(5)
    assert result["answer"].outcome == "yes"
    assert "No answer, so I've cancelled it." not in j.said


def test_the_hold_is_set_before_the_question_is_said_again():
    j = Jalen.__new__(Jalen)
    j.cfg = CONFIG
    j._confirm_question = "send telegram message to Ali, saying \"see you at six\". Confirm?"
    seen = {}

    def say_blocking(text):
        seen["held"] = getattr(j, "_reask_until", 0.0) - time.monotonic()

    j.say_blocking = say_blocking
    j._ask_again()
    assert seen["held"] >= j._confirm_timeout_s(), "nothing held the window while it was asked"


# ===========================================================================
# 5. JALEN SAYING HIS OWN NAME COULD WAKE HIM
# ===========================================================================
class _Saying:
    """A speaker on air with a sentence; the wake model will fire on the next frame."""

    speaking = True
    on_air = True

    def __init__(self, sentence):
        self.sentence = sentence
        self.stopped = 0

    def speaking_for(self):
        return 3.0

    def stop(self):
        self.stopped += 1
        self.speaking = self.on_air = False

    def quiet_for(self):
        return 0.0 if self.on_air else 1.0

    def spoken_since(self, since):
        return self.sentence


def _a_room_while_he_says(monkeypatch, sentence, heard):
    from test_listening_does_not_obey_other_people_or_itself import Heard, Room
    from test_only_his_name_interrupts_him import _WakeAt

    room = Room(monkeypatch, [Heard(heard)], speaker=_Saying(sentence), barge_in="wake_word")
    room.jalen.wake = _WakeAt(1)
    return room


def test_his_name_in_jalens_own_sentence_does_not_stop_him_or_open_a_window(monkeypatch):
    room = _a_room_while_he_says(
        monkeypatch, 'I\'m in lockdown until you say "Jalen, end the lockdown".', "end the lockdown")
    room.run()
    assert room.jalen.speaker.stopped == 0, "Jalen cut himself off on his own name"
    assert room.dispatched == [], "what followed his own name was taken as him"
    assert len(room.rows("the wake word fired while I was saying my own name")) == 1


def test_the_wake_word_over_an_ordinary_sentence_still_stops_him(monkeypatch):
    room = _a_room_while_he_says(monkeypatch, "Here is the weather for tomorrow.", "open chrome")
    room.run()
    assert room.jalen.speaker.stopped == 1
    assert room.texts() == ["open chrome"] and room.from_him() == [True]


def test_a_window_the_name_opened_over_his_voice_refuses_that_voice():
    from test_listening_does_not_obey_other_people_or_itself import _Gate

    gate = _Gate()
    gate._last_reply_text = "You have four unread emails, two of them look like opportunities."
    gate._last_reply_at = time.monotonic()
    gate.speaker.speaking = True
    own = "two of them look like opportunities"
    assert not gate.should_act_on(own, True, opened_by="wake-over-voice",
                                  began_at=time.monotonic()), "its own voice was taken as him"
    assert gate.should_act_on("stop", True, opened_by="wake-over-voice", began_at=time.monotonic())
    assert gate.should_act_on(own, True, opened_by="wake"), "the plain wake word is unchanged"


def test_the_names_are_read_from_what_is_on_air():
    from jalen.brain.router import says_the_name

    assert says_the_name('until you say "Jalen, end the lockdown"')
    assert says_the_name("Hey Jarvis") and says_the_name("JALEN")
    assert not says_the_name("jalapeno poppers") and not says_the_name("Yes, Boss?")


# ===========================================================================
# 8. AN INSTRUCTION TYPED IN THE "SAY STOP" WINDOW WAS SWALLOWED
# ===========================================================================
@pytest.mark.parametrize("said", ["remind me tomorrow to call mom", "open the right folder too"])
def test_an_instruction_in_the_window_is_an_instruction(said):
    j = stop_window_open()
    hear(j, said)
    assert j._answer_q.empty(), f"{said!r} was taken as the answer to the announcement"
    assert j.routed == [said], f"{said!r} was swallowed by the window"


@pytest.mark.parametrize("said", ["no, the other chat", "wait, not that one", "no I meant Rodion"])
def test_a_refusal_that_says_more_still_stops_it(said):
    j = stop_window_open()
    hear(j, said)
    assert j._answer_q.get_nowait() is False, f"{said!r} did not stop it"


# ===========================================================================
# 10. A STANDBY JALEN BLOCKED EVERY OTHER WAY OF STARTING ONE
# ===========================================================================
def _start(monkeypatch, argv, holder):
    spec = importlib.util.spec_from_file_location("run_probe", ROOT / "run.py")
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    held = {"mode": holder}
    did = []

    def acquire(mode):
        did.append(("acquire", mode))
        if held["mode"] is None:
            return None
        return runtime.InstanceInfo(pid=4242, started_at=0.0, mode=held["mode"])

    def stop(*a, **k):
        did.append(("stop",))
        held["mode"] = None
        return "Stopped Jalen (pid 4242) cleanly."

    from jalen import crashlog

    monkeypatch.setattr(crashlog, "install", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "acquire", acquire)
    monkeypatch.setattr(runtime, "release", lambda: None)
    monkeypatch.setattr(runtime, "stop_running_instance", stop)
    monkeypatch.setattr(runtime, "send_signal", lambda name: did.append(("signal", name)))
    monkeypatch.setattr(run, "_serve", lambda args: did.append(("serve",)) or 0)
    monkeypatch.setattr(sys, "argv", ["run.py", *argv])
    return run.main(), did


def test_starting_jalen_wakes_the_one_waiting_in_standby(monkeypatch):
    code, did = _start(monkeypatch, [], "standby")
    assert code == 0 and ("signal", "wake") in did
    assert ("serve",) not in did and ("stop",) not in did


@pytest.mark.parametrize("flag", ["--text", "--telegram"])
def test_text_and_telegram_mode_take_the_place_of_a_standby_one(monkeypatch, flag):
    code, did = _start(monkeypatch, [flag], "standby")
    assert code == 0 and ("stop",) in did and did[-1] == ("serve",)


def test_a_jalen_that_is_really_running_is_still_left_alone(monkeypatch):
    code, did = _start(monkeypatch, ["--text"], "voice")
    assert code == 1 and ("stop",) not in did and ("serve",) not in did


def test_a_second_standby_is_still_refused(monkeypatch):
    code, did = _start(monkeypatch, ["--standby"], "standby")
    assert code == 1 and ("stop",) not in did and ("signal", "wake") not in did


# ===========================================================================
# 11. THE CHAT PANEL GAVE A RUNNING TURN A FRESH LEASH
# ===========================================================================
def _a_panel(monkeypatch, *, active=(), brain_busy=False):
    from jalen.bridge import server

    j = Jalen.__new__(Jalen)
    j.resets = []
    j.safety = SimpleNamespace(governor=SimpleNamespace(new_instruction=lambda: j.resets.append(1)))
    j._take_as_answer = lambda text, heard: ""
    j._run_coro = lambda value: value
    j.handle_with_brain = lambda text: "done"
    j.audit = SimpleNamespace(error=lambda *a, **k: None)
    j._turn_lock, j._active_turns = threading.Lock(), set(active)
    j.brain = SimpleNamespace(busy=lambda: brain_busy)
    monkeypatch.setattr(server, "get_server",
                        lambda: SimpleNamespace(send_command=lambda *a, **k: None))
    return j


def test_a_panel_message_with_nothing_running_is_a_new_instruction(monkeypatch):
    j = _a_panel(monkeypatch)
    j._answer_panel("what's on my calendar")
    assert j.resets == [1]


@pytest.mark.parametrize("busy", [{"active": (7,)}, {"brain_busy": True}])
def test_a_panel_message_while_a_turn_works_does_not_reset_its_limits(monkeypatch, busy):
    j = _a_panel(monkeypatch, **busy)
    j._answer_panel("what's on my calendar")
    assert j.resets == [], "the running turn got forty more actions"


# ===========================================================================
# 12. THE CHANNEL'S FORMAT WAS ENFORCED ON A FRIEND, AND AFTER THE QUESTION
# ===========================================================================
from test_channel_posts_follow_his_format import BROKEN, LEE_LAB  # noqa: E402


def test_a_message_to_a_friend_is_never_held_to_the_channel_format():
    note = ("Requirements: bring a laptop.\nInterested? dm me and I'll send the link.\n"
            "- first bullet")
    assert messaging._in_house_format(note, False, to="Ali") == (note, "", "")
    assert messaging.refused_before_asking(
        "send_telegram_message", {"to": "Ali", "text": note}) == ""


def test_a_broken_channel_post_is_refused_before_he_is_asked():
    broken = BROKEN["no requirements"][0](LEE_LAB)
    for tool, where, nothing in (("send_telegram_message", "to", "Nothing sent"),
                                 ("save_telegram_draft", "to", "Nothing saved"),
                                 ("edit_telegram_message", "chat", "Nothing changed")):
        said = messaging.refused_before_asking(tool, {where: HIS_CHANNEL, "text": broken})
        assert said.startswith(f"{nothing} — the post doesn't follow your channel's format"), said
    assert messaging.refused_before_asking(
        "send_telegram_message", {"to": HIS_CHANNEL, "text": broken, "as_written": True}) == ""
    assert messaging.refused_before_asking(
        "send_telegram_message", {"to": HIS_CHANNEL, "text": LEE_LAB}) == ""
    assert messaging.refused_before_asking("delete_message", {"chat": HIS_CHANNEL}) == ""


def test_both_paths_ask_before_any_question_or_limit():
    import inspect

    from jalen.brain.agent import Brain

    hook = inspect.getsource(Brain._make_hook)
    assert hook.index("refused_before_asking") < hook.index("governor.admit(")
    assert hook.index("self.safety.classify(") < hook.index("refused_before_asking")
    local = inspect.getsource(Jalen.handle_local)
    assert local.index("refused_before_asking") < local.index("self._admit(verdict)")
    assert local.index("self.safety.classify(") < local.index("refused_before_asking")


def test_the_router_path_refuses_a_broken_post_without_asking_or_counting(monkeypatch):
    from jalen.brain.router import Intent
    from jalen.safety import SafetyEngine

    j = Jalen.__new__(Jalen)
    j.cfg = CONFIG
    j.safety = SafetyEngine(CONFIG)
    j.asked, j.counted = [], []
    j.audit = SimpleNamespace(action=lambda *a, **k: None, write=lambda *a, **k: None,
                              error=lambda *a, **k: None, utterance=lambda *a, **k: None)
    j._admit = lambda verdict: j.counted.append(verdict) or SimpleNamespace(allowed=True)
    j.confirm = lambda *a, **k: j.asked.append(a)
    broken = BROKEN["no requirements"][0](LEE_LAB)
    said = j.handle_local(Intent(tool="send_telegram_message",
                                 args={"to": HIS_CHANNEL, "text": broken}),
                          his_own_words=True)
    assert said.startswith("Nothing sent — the post doesn't follow your channel's format")
    assert j.asked == [] and j.counted == [], "he was asked, or the limit counted it"


# ===========================================================================
# 13. "NOW" WAS SAID IN THE QUESTION AND REFUSED BY THE SEND
# ===========================================================================
def test_a_send_scheduled_for_now_goes_now(tg):  # noqa: F811 - the fixture
    reply = messaging.send_telegram_message(to="Ali", text="hi", schedule_at="now")
    assert not reply.startswith("Nothing sent"), reply
    assert not reply.startswith("Scheduled for"), reply


def test_the_question_and_the_tools_share_one_list_of_words():
    from jalen import safety

    assert messaging._RIGHT_NOW is safety.RIGHT_NOW_WORDS
    assert messaging._THE_NEXT_ONE is safety.NEXT_SCHEDULED_WORDS
    assert "now" in question("send_telegram_message", to="Ali", text="hi", schedule_at="now")


# ===========================================================================
# 15. CONFIG READ ON EVERY QUESTION
# ===========================================================================
def test_the_question_windows_are_read_once_in_init():
    import inspect

    for method in (Jalen._confirm_timeout_s, Jalen._undo_window_s):
        assert "get_path" not in inspect.getsource(method), method.__name__
    assert "self._question_windows = self._read_question_windows(cfg)" in inspect.getsource(
        Jalen.__init__)


def test_a_jalen_built_without_init_still_has_its_windows():
    j = Jalen.__new__(Jalen)
    j.cfg = CONFIG
    assert j._confirm_timeout_s() == float(CONFIG.get_path("safety.confirm_timeout_s"))
    j.mode = "telegram"
    assert j._undo_window_s() == max(float(CONFIG.get_path("safety.undo_window_s")),
                                     float(CONFIG.get_path("safety.undo_window_typed_s")))
