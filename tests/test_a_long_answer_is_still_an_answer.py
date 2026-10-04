"""
A LONG ANSWER OUTLIVES THE WINDOW IT WAS GIVEN IN, AND IS THROWN AWAY FOR IT.

THE SECOND HALF OF "TWO GATES MUST AGREE" (CLAUDE.md)
-----------------------------------------------------
The microphone gate opens a listening window at the moment a SOUND begins:
`self._expectation_open()` is asked when he starts to speak. The address gate
(`Jalen.should_act_on`) asks the same question again when the sentence has
ENDED and been transcribed - the utterance's own length, the 1.4s endpoint
and the speech-to-text time later. A window that was open when he began and
has closed by the time he finished is therefore open to the first gate and
shut to the second, and the sentence is refused for arriving too late when it
did not.

Measured in data/audit.jsonl (scripts/measure_address_gate.py replays it,
and says which rows this rule alone admits): the answer window is 30s from
the end of Jalen's question, and 5 sentences meant for him started inside it
and were refused for finishing outside it. All five are dictation, 36 words
and up (the log keeps 200 characters, so they were longer). The beginning is
estimated from the length of the sentence at 2.6 words a second, which is NOT
MEASURED (the audio is not kept); the script re-runs at 2.0 and 3.4 and the
count does not move.

    replayed with the window judged at the gate (before)   5 refused
    replayed with the window judged when the sound began   0 refused
    background or ambiguous rows that this admits .......  0 of 27

THE RULE
--------
run() remembers WHICH question was open when the sound opened the window
(`vouched_by`, the Expectation object itself) and hands it to the address
gate. The gate honours it only while it is still the newest thing Jalen asked
(identity, not time: a later turn that asks or forgets replaces it) and only
for as long as one utterance can physically run: `vad.max_utterance_s` plus
the speech-to-text time (p95 4.4s over 349 timing rows; ANSWER_STT_SLACK_S).
The echo test is applied to it like every other way in.
"""
from __future__ import annotations

import inspect
import time

import pytest

from jalen import app as app_module
from jalen.app import ECHO_TAIL_S, Expectation, Jalen
from jalen.brain import router
from jalen.config import CONFIG

QUESTION = "Which one did you mean, the first draft or the one you saved last night?"
LONG_ANSWER = (
    "I want the first draft because the second one lost the paragraph about "
    "my results, and then I would like it sent to the committee with the "
    "table attached so that nobody has to ask for it again"
)


def _overrun() -> float:
    return float(CONFIG.get_path("vad.max_utterance_s", 30)) + app_module.ANSWER_STT_SLACK_S


class _Gate:
    """Just enough Jalen to ask 'would you act on this?'. The real methods."""

    def __init__(self):
        self._awaiting_confirmation = False
        self._awaiting_stop = False
        self._awaiting_reply = False
        self._pending_rating = None
        self._last_user_text = ""
        self._last_user_at = 0.0
        self._last_reply_text = ""
        self._last_reply_at = 0.0
        self._expecting = None
        self._answer_window_s = float(CONFIG.get_path("conversation.answer_window_s", 30))
        self._answer_overrun_s = _overrun()
        self.cfg = CONFIG
        self.kill_phrases = set(CONFIG.get_path("safety.kill_phrases"))

    should_act_on = Jalen.should_act_on
    _continues_last_utterance = Jalen._continues_last_utterance
    _expectation_open = Jalen._expectation_open
    _rating_is_pending = Jalen._rating_is_pending
    RATING_EXPIRES_S = Jalen.RATING_EXPIRES_S
    _expect_an_answer = Jalen._expect_an_answer
    _forget_expectation = Jalen._forget_expectation
    _sounds_like_its_own_voice = Jalen._sounds_like_its_own_voice
    _open_expectation = getattr(Jalen, "_open_expectation", None)
    _window_outlived_by_the_sentence = getattr(Jalen, "_window_outlived_by_the_sentence", None)

    def asked(self, question: str, *, closed_s_ago: float | None = None, window_s: float = 30.0):
        """
        Jalen asked `question`. With no `closed_s_ago` the window is still open
        (it has 5s left); with one, it shut that many seconds ago. Returns the
        Expectation, which is what run() holds.
        """
        now = time.monotonic()
        self._last_reply_text = question
        expires_at = now + 5.0 if closed_s_ago is None else now - closed_s_ago
        opened_at = expires_at - window_s
        # Said long enough ago that it cannot be arriving through the speakers.
        self._last_reply_at = opened_at - ECHO_TAIL_S - 1
        self._expecting = Expectation(question=question, turn_id=1,
                                      opened_at=opened_at, expires_at=expires_at)
        return self._expecting


# ---------------------------------------------------------------------------
# THE GATE
# ---------------------------------------------------------------------------
def test_the_two_new_pieces_exist():
    assert Jalen.__dict__.get("_open_expectation") is not None, "no Jalen._open_expectation"
    assert Jalen.__dict__.get("_window_outlived_by_the_sentence") is not None
    assert hasattr(app_module, "ANSWER_STT_SLACK_S")
    params = inspect.signature(Jalen.should_act_on).parameters
    assert "vouched_by" in params, "the address gate cannot be told which window opened the microphone"
    assert params["vouched_by"].default is None, "callers that do not know must get no exemption"


def test_the_open_expectation_is_the_object_not_a_yes_or_no():
    gate = _Gate()
    assert gate._open_expectation() is None
    asked = gate.asked(QUESTION)
    assert gate._open_expectation() is asked
    gate.asked(QUESTION, closed_s_ago=1)
    assert gate._open_expectation() is None, "an expired window must read as no window"


def test_an_answer_that_began_inside_the_window_is_acted_on_after_it_closed():
    gate = _Gate()
    vouched = gate.asked(QUESTION, closed_s_ago=8)       # shut 8s ago
    assert not gate._expectation_open()
    assert gate.should_act_on(LONG_ANSWER, False, opened_by="answer", vouched_by=vouched)


def test_the_same_sentence_is_still_refused_when_no_window_vouches_for_it():
    """The refusal everything else relies on: nothing is vouched, nothing is free."""
    gate = _Gate()
    gate.asked(QUESTION, closed_s_ago=8)
    assert not gate.should_act_on(LONG_ANSWER, False)
    assert not gate.should_act_on(LONG_ANSWER, False, opened_by="answer")
    assert not gate.should_act_on(LONG_ANSWER, False, opened_by="follow_up", vouched_by=None)


def test_it_is_the_newest_question_or_nothing():
    """Identity, not time: once a later turn has asked, forgotten or replaced it, it is not his answer."""
    gate = _Gate()
    vouched = gate.asked(QUESTION, closed_s_ago=8)
    gate._forget_expectation()            # a turn that asked nothing closed it
    assert not gate.should_act_on(LONG_ANSWER, False, opened_by="answer", vouched_by=vouched)

    gate2 = _Gate()
    old = gate2.asked(QUESTION, closed_s_ago=8)
    gate2.asked("And the committee address, or the secretary's?", closed_s_ago=9)
    assert gate2._expecting is not old
    assert not gate2.should_act_on(LONG_ANSWER, False, opened_by="answer", vouched_by=old)


def test_it_lasts_only_as_long_as_one_utterance_can_run():
    gate = _Gate()
    vouched = gate.asked(QUESTION, closed_s_ago=_overrun() - 1)
    assert gate.should_act_on(LONG_ANSWER, False, opened_by="answer", vouched_by=vouched)
    gate = _Gate()
    vouched = gate.asked(QUESTION, closed_s_ago=_overrun() + 1)
    assert not gate.should_act_on(LONG_ANSWER, False, opened_by="answer", vouched_by=vouched), (
        "no utterance is longer than vad.max_utterance_s, so a sentence this late "
        "did not begin inside the window"
    )


def test_it_answers_one_question_once():
    gate = _Gate()
    vouched = gate.asked(QUESTION, closed_s_ago=8)
    assert gate.should_act_on(LONG_ANSWER, False, opened_by="answer", vouched_by=vouched)
    assert gate._expecting is None, "an accepted answer must close the window, as the open path does"
    assert not gate.should_act_on(LONG_ANSWER, False, opened_by="answer", vouched_by=vouched)


@pytest.mark.parametrize("echo", [
    "which one did you mean the first draft or the one you saved",
    "the first draft or the one you saved last night",
    "Which one did you mean, the first draft or the one you saved last night?",
])
def test_it_is_never_jalens_own_voice(echo):
    """THE ECHO DEFENCE: this is a way in without his name, so it asks."""
    gate = _Gate()
    vouched = gate.asked(QUESTION, closed_s_ago=2)
    gate._last_reply_at = time.monotonic() - 1.0          # he finished a second ago
    assert not gate.should_act_on(echo, False, opened_by="answer", vouched_by=vouched), echo


def test_the_echo_test_is_called_by_the_new_branch():
    source = inspect.getsource(Jalen.should_act_on)
    at = source.index("vouched_by is not None")
    assert "_sounds_like_its_own_voice" in source[at:at + 700]


def test_nothing_else_about_the_gate_changed():
    gate = _Gate()
    vouched = gate.asked(QUESTION, closed_s_ago=8)
    assert gate.should_act_on("open chrome", True, vouched_by=vouched)               # wake word
    gate = _Gate()
    vouched = gate.asked(QUESTION, closed_s_ago=8)
    assert gate.should_act_on("stop", False, vouched_by=vouched)                     # kill phrase
    assert gate.should_act_on("Jalen, open chrome", False, vouched_by=None)          # his name
    # A fresh, still-open window behaves exactly as before.
    fresh = _Gate()
    fresh.asked(QUESTION)
    assert fresh.should_act_on("the first draft", False)
    assert not _Gate().should_act_on("the first draft", False)


# ---------------------------------------------------------------------------
# THE LOOP HAS TO TAKE THE SNAPSHOT, AT THE MOMENT THE SOUND OPENS THE WINDOW
# ---------------------------------------------------------------------------
def _run_source() -> str:
    return inspect.getsource(Jalen.run)


def test_run_remembers_which_question_was_open_when_the_sound_began():
    source = _run_source()
    branch = source[source.index("in_follow_up = "):source.index("elif self.wake.feed")]
    assert "vouched_by = self._open_expectation()" in branch, (
        "the sound-opens-the-window branch does not record which question vouched for it"
    )
    # Taken once, in the same breath as the decision to listen - not after.
    assert branch.index("listening = True") < branch.index("vouched_by = self._open_expectation()")


def test_run_hands_it_to_the_address_gate():
    source = _run_source()
    assert "vouched_by=vouched_by" in source[source.index("if not self.should_act_on("):][:200]


def test_every_other_window_forgets_it():
    """
    A window opened by the wake word, the hotkey or a barge-in must not inherit
    the previous window's question - a barge-in on a cough would walk in on a
    free pass. Same trap as `opened_by`, same guard.
    """
    source = _run_source()
    sites = [i for i in range(len(source)) if source.startswith("listening = True", i)]
    assert len(sites) >= 4
    for at in sites:
        before = source[max(0, at - 300):at]
        if "collector.resume" in before:
            continue                      # the rest of the SAME sentence: same window
        assert "vouched_by" in source[at:at + 900], (
            "a listening window is opened without saying which question vouched "
            "for it:\n" + source[max(0, at - 150):at + 250]
        )


def test_the_slack_is_a_measured_number():
    assert app_module.ANSWER_STT_SLACK_S == pytest.approx(5.0)
    src = inspect.getsource(app_module)
    at = src.index("ANSWER_STT_SLACK_S =")
    assert "349" in src[max(0, at - 900):at], "a tuned constant carries its measurement"


def test_the_overrun_is_read_from_config_once_in_init():
    source = inspect.getsource(Jalen.__init__)
    assert "_answer_overrun_s" in source and "vad.max_utterance_s" in source


# ---------------------------------------------------------------------------
# A ROW FOR EVERY SENTENCE ACTED ON WITHOUT HIS NAME, SO THE FALSE ACCEPTS CAN
# BE COUNTED IN PRODUCTION AND NOT ONLY IN THE LOG'S 105 ROWS
# ---------------------------------------------------------------------------
class _Audit:
    def __init__(self):
        self.rows = []

    def write(self, kind, **fields):
        self.rows.append((kind, fields))


class _Noter:
    def __init__(self):
        self.audit = _Audit()

    _note_how_it_got_in = getattr(Jalen, "_note_how_it_got_in", None)


def test_a_sentence_acted_on_without_his_name_says_which_door_it_came_through():
    assert Jalen.__dict__.get("_note_how_it_got_in") is not None, "no such method"
    noter = _Noter()
    noter._note_how_it_got_in("misheard-name", "Dylan, what time is it", "follow_up")
    (kind, row), = noter.audit.rows
    assert kind == "system"
    assert row["summary"] == "acted on without his name - misheard-name"
    assert row["detail"] == {"heard": "Dylan, what time is it", "opened_by": "follow_up"}


def test_the_text_in_that_row_is_cut_like_the_ignored_row():
    noter = _Noter()
    noter._note_how_it_got_in("polite-request", "x" * 500, "follow_up")
    assert len(noter.audit.rows[0][1]["detail"]["heard"]) == 200


@pytest.mark.parametrize("heard,door", [
    ("Dylan, what time is it", "misheard-name"),
    ("Helen.", "misheard-name"),
    ("Hey Delic, could you please play it", "misheard-name"),
    ("Open the file. Hey Jalen.", "greeting-last"),
    ("Could you please open Chrome", "polite-request"),
    ("I would like you to send it", "polite-request"),
])
def test_each_door_has_a_name(heard, door):
    assert router.widened_door(heard, "follow_up") == door


@pytest.mark.parametrize("heard", [
    "Thank you.", "what are you doing", "Jalen, open chrome", "Helen is my friend", "",
])
def test_no_door_means_none(heard):
    assert router.widened_door(heard, "follow_up") is None


def test_a_polite_request_is_a_door_only_in_the_follow_up_window():
    assert router.widened_door("Could you please open Chrome", "follow_up") == "polite-request"
    for opened_by in ("", "barge", "wake"):
        assert router.widened_door("Could you please open Chrome", opened_by) is None


def test_widened_address_is_exactly_whether_a_door_exists():
    for heard in ("Dylan, what time is it", "Open the file. Hey Jalen.", "Could you please open it",
                  "Thank you.", "what are you doing", ""):
        for opened_by in ("", "follow_up", "barge", "answer", "wake"):
            assert router.widened_address(heard, opened_by) == (
                router.widened_door(heard, opened_by) is not None
            ), (heard, opened_by)


def test_run_writes_the_row_after_the_gate_and_never_speaks():
    source = _run_source()
    gate = source.index("if not self.should_act_on(")
    decided = source.index("door = None")
    note = source.index("_note_how_it_got_in(")
    dispatch = source.index("dispatch_turn, args=(text")
    assert gate < decided < note < dispatch
    assert "self.say(" not in source[gate:note]
    # An answer, a continuation or the wake word is not "without his name by a door".
    rule = source[decided:decided + 700]
    assert "was_an_answer" in rule and "wake_initiated" in rule and "from_him" in rule
    # Decided on the words as heard, before the misheard name is taken off them
    # (afterwards no door would recognise it) ...
    assert decided < source.index("without_a_misheard_name(")
    assert "_note_how_it_got_in(door, heard, opened_by, vouched=vouching)" in source


def test_a_sentence_put_back_for_more_audio_leaves_one_row_not_two():
    """
    A sentence that looks unfinished comes through the gate twice - once as the
    fragment, once as the whole. The row is written only when it is really
    going on to a turn.
    """
    source = _run_source()
    resume = source.index("self.collector.resume(utterance)")
    note = source.index("_note_how_it_got_in(")
    assert resume < note
