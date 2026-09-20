"""
He answers the question Jalen just asked him, and Jalen throws it away.

THE BUG, AND WHY EVERY BEHAVIOURAL TEST STILL PASSED
----------------------------------------------------
There are two gates, and only one of them had ever heard of the follow-up
window.

    the MICROPHONE gate   `follow_up_until` in Jalen.run() decides whether a
                          sound opens a listening window at all. It is a
                          LOCAL VARIABLE inside the loop.
    the ADDRESS gate      should_act_on() decides whether the words that
                          came out of that window are acted on. It is a
                          method, and it could not see the local.

So the window opened, the microphone recorded, Whisper transcribed, and
then the address gate dropped the sentence for not starting with his name.
Its own docstring says the opposite -

    Jalen just asked    "which file did you mean?" - requiring the name
                        to answer a question he was this moment asked
                        would be absurd, and it is the only reason the
                        follow-up window still exists

- but the only thing implementing that was `_awaiting_reply`, which is set
by the ask_user TOOL. An ordinary reply that happens to end in a question
sets no flag at all, and 31.5% of what Jalen says ends in a question mark.

MEASURED IN data/audit.jsonl
----------------------------
97 utterances were logged as "ignored - not addressed to Jalen". 59 of them
arrived after a Jalen line ending in "?"; 45 of those were four words or
more. Read a few and there is no ambiguity about what they are:

    Jalen:  "Which one did you mean, boss - ChatGPT or Gemini?"
    He:     "and sign me in to chat GPG using my authentication."   DROPPED

    Jalen:  "I see two matches on your Desktop, Boss - Changes.md and Cha..."
    He:     "in changes.pdf"                                        DROPPED

THE TIMER WAS NOT THE PROBLEM
-----------------------------
An earlier reading of this evidence concluded that 35 of 43 answers arrived
after the 12-second window and that the window therefore had to grow. That
was a measurement artefact. The "ignored" line is written when the
utterance ENDS, and the window is tested when it BEGINS - so a twenty-second
answer that started three seconds into the window logs as a twenty-three
second gap. Every one of those 97 utterances reached speech recognition,
and the only paths that open the microphone are the wake word (which is
accepted), barge-in, and the follow-up window. The window was open for all
of them. Lengthening it would have fixed nothing.

WHAT OPENS THE EXEMPTION, AND WHAT DOES NOT
-------------------------------------------
Not "Jalen spoke" - that is an always-listening assistant, and a television
in the room becomes a user. The exemption opens only when the reply
SOLICITED an answer, which for model-written text means its final sentence
ends in a question mark. Measured over the 804 real replies in the log that
is 31.5% of them, and it caught 100% of the genuine dropped answers.

The echo defence has to be rebuilt at the same time, because the address
gate WAS the echo defence: Jalen's own sentences do not start with his
name. See test_jalen_still_cannot_talk_itself_into_a_new_turn below.
"""
from __future__ import annotations

import time

import pytest

from jarvis.app import Jalen
from jarvis.brain.router import solicits_an_answer
from jarvis.config import CONFIG


class _Gate:
    """
    Just enough Jalen to answer "would you act on this?".

    Constructing a real one opens a microphone, a speaker and a Tk window.
    The predicate reads a few booleans, a deadline and two strings.
    """

    def __init__(self, *, confirmation=False, stop=False, reply=False):
        self._awaiting_confirmation = confirmation
        self._awaiting_stop = stop
        self._awaiting_reply = reply
        self._pending_rating = None
        self._last_user_text = ""
        self._last_user_at = 0.0
        self._last_reply_text = ""
        self._last_reply_at = 0.0
        self._answer_window_s = float(
            CONFIG.get_path("conversation.answer_window_s", 30))
        self._expecting = None
        self.cfg = CONFIG
        self.kill_phrases = set(CONFIG.get_path("safety.kill_phrases"))

    # the real implementations, not stand-ins
    should_act_on = Jalen.should_act_on
    _continues_last_utterance = Jalen._continues_last_utterance
    _expectation_open = Jalen._expectation_open
    _rating_is_pending = Jalen._rating_is_pending
    RATING_EXPIRES_S = Jalen.RATING_EXPIRES_S
    _expect_an_answer = Jalen._expect_an_answer
    _forget_expectation = Jalen._forget_expectation
    _sounds_like_its_own_voice = Jalen._sounds_like_its_own_voice

    def asked(self, question: str, *, window_s: float = 30.0, turn_id: int = 1):
        """Jalen finished saying `question` just now."""
        self._last_reply_text = question
        self._expect_an_answer(question, turn_id=turn_id, window_s=window_s)
        return self


# ---------------------------------------------------------------------------
# WHAT COUNTS AS ASKING. A pure predicate over the text Jalen produced.
# ---------------------------------------------------------------------------
SOLICITS = [
    # Verbatim from data/audit.jsonl.
    "Which one did you mean, boss - ChatGPT or Gemini?",
    "Yes, Boss?",
    "Not much, boss - just sitting here ready. What do you need?",
    "I see two matches on your Desktop, Boss - Changes.md and Changes.pdf. "
    "Which one did you mean?",
    "close app: notepad. Confirm?",
    "Hey boss - how do you rate my work out of ten?",
    # The question is the last sentence of a longer answer.
    "Gmail's live now, Boss. Pulling up your full thread with Andres. "
    "Do you want the whole thread read out, or just the last message?",
]

DOES_NOT_SOLICIT = [
    # Ordinary statements. The overwhelming majority of what it says.
    "Sure, I can open that for you.",
    "You have four unread emails.",
    "Done. I verified the result against your requirements.",
    "YouTube's open, Boss.",
    "Any time.",
    "Playing that DJ Vismay VRz mashup on YouTube now, Boss.",
    # A RHETORICAL question, answered in the same breath. This is the case a
    # naive `"?" in text` would get wrong, and it is why the test is on the
    # FINAL sentence rather than on the string.
    "Why does that matter? Because the drive is nearly full, Boss.",
    "What happened? Groq timed out, so I used the local model instead.",
    # A question mark buried mid-answer, with the answer continuing past it.
    "You asked what's eating the disk? Temp files, 197MB of them, and the "
    "recycle bin. I've cleared the temp files.",
    # Ends on an invitation, but is not a question and must not open the
    # gate - measured on the real log this phrasing buys three marginal
    # cases and costs 53 extra open windows.
    "Alright, leaving it alone then, Boss. Let me know if you want it "
    "opened another way.",
    "I'm only reporting sizes; nothing gets deleted unless you tell me to.",
    "",
    "   ",
]


@pytest.mark.parametrize("text", SOLICITS)
def test_a_reply_that_asks_him_something_is_recognised(text):
    assert solicits_an_answer(text), f"{text!r} is Jalen asking for an answer"


@pytest.mark.parametrize("text", DOES_NOT_SOLICIT)
def test_a_reply_that_asks_nothing_opens_no_window(text):
    assert not solicits_an_answer(text), (
        f"{text!r} would open a free-speech window after an ordinary "
        "statement - that is the always-listening assistant"
    )


def test_trailing_punctuation_after_the_question_mark_still_counts():
    """
    Speech synthesis and the model both produce these. A closing quote or
    bracket after the "?" must not hide the question.
    """
    for text in ('Did you mean "Changes.pdf"?', "Which one (of the two)?",
                 'He asked "which file?"', "Which one? "):
        assert solicits_an_answer(text), text


# ---------------------------------------------------------------------------
# THE BUG ITSELF
# ---------------------------------------------------------------------------
# Verbatim pairs from data/audit.jsonl. Every one of these was recorded as
# "ignored - not addressed to Jalen" while the microphone window Jalen
# itself had opened was still running.
DROPPED_FOR_REAL = [
    ("Which one did you mean, boss - ChatGPT or Gemini?",
     "and sign me in to chat GPG using my authentication."),
    ("I see two matches on your Desktop, Boss - Changes.md and Changes.pdf. "
     "Which one did you mean?",
     "in changes.pdf"),
    ("Storage Settings is open on screen so you can see the full breakdown "
     "yourself. What did you want me to clear?",
     "what do you mean by old Windows cleanups?"),
    ("I'm here, Boss - just waiting on one thing from you. Do you want this "
     "posted to the channel or to Saved Messages?",
     "Could you please repeat?"),
    ("I want to make sure I send GPT a sensible brief, boss - "
     '"top 10 most powerful men" by what measure?',
     "Yeah, the most powerful man on the earth. I mean, 10 of them."),
    ("Not much, boss - just sitting here ready. What do you need?",
     "Could you please research the top 10 richest men on earth and their "
     "net worth currently by September 20."),
]


@pytest.mark.parametrize("question,answer", DROPPED_FOR_REAL)
def test_the_answer_to_its_own_question_is_acted_on(question, answer):
    gate = _Gate().asked(question)
    assert gate.should_act_on(answer, wake_initiated=False), (
        f"Jalen asked {question!r} and then discarded {answer!r} - this is "
        "the bug, replayed from the audit log"
    )


@pytest.mark.parametrize("answer", [
    "yes", "no", "yeah", "correct", "the second one", "that one",
    "continue", "do it", "changes.pdf", "ChatGPT",
])
def test_a_one_word_answer_counts_when_a_question_is_open(answer):
    """
    Short answers were the worst-affected: "the second one" carries no name,
    no verb and nothing the router matches, so without the question open it
    is indistinguishable from noise - and correctly rejected.
    """
    gate = _Gate().asked("I see two matches, Boss. Which one did you mean?")
    assert gate.should_act_on(answer, wake_initiated=False)


def test_after_an_ordinary_statement_the_name_is_still_required():
    """
    The line that keeps this from becoming an always-listening assistant.
    Jalen said something; it did not ask anything; the room is not a user.
    """
    gate = _Gate()
    gate._last_reply_text = "YouTube's open, Boss."
    assert not gate.should_act_on("the second one", wake_initiated=False)
    assert not gate.should_act_on("so anyway the weather is nice", wake_initiated=False)
    assert gate.should_act_on("Jalen, the second one", wake_initiated=False)


def test_unrelated_room_speech_during_an_open_question_is_the_known_cost():
    """
    HONEST ABOUT THE TRADE, rather than pretending there isn't one.

    While a question is open, a sentence from the room IS acted on. That is
    the same trade the follow-up window already made acoustically, now made
    where it has consequences, and it is bounded three ways: it opens only
    after the 31.5% of replies that end in a question, it expires, and
    anything that sounds like Jalen's own voice is still refused.

    If this test ever has to be deleted, the exemption has grown too wide.
    """
    gate = _Gate().asked("Which one did you mean, boss?")
    assert gate.should_act_on("no I told him that already", wake_initiated=False)
    gate._forget_expectation()
    assert not gate.should_act_on("no I told him that already", wake_initiated=False)


# ---------------------------------------------------------------------------
# THE ECHO DEFENCE, REBUILT
#
# The address gate WAS the echo defence - Jalen's own sentences do not start
# with his name. Opening a free window after a question removes that defence
# exactly when the microphone is most likely to be hearing the tail of the
# question it just asked.
# ---------------------------------------------------------------------------
def test_jalen_still_cannot_talk_itself_into_a_new_turn():
    """
    THE REGRESSION THIS FIX COULD CAUSE, stated directly.

    Playback ends, the window opens, and what arrives at the microphone is
    the last second of the question Jalen just asked. Before the echo check
    that is now a free, un-named, accepted turn - a strictly worse bug than
    the one being fixed, because it is a loop.
    """
    question = ("I see two matches on your Desktop, Boss - Changes.md and "
                "Changes.pdf. Which one did you mean?")
    gate = _Gate().asked(question)
    for echo in (
        question,                                   # the whole thing
        "Changes.md and Changes.pdf. Which one did you mean?",
        "which one did you mean",
        "I see two matches on your Desktop",
        "two matches on your desktop boss",         # no punctuation, as STT writes it
    ):
        assert not gate.should_act_on(echo, wake_initiated=False), (
            f"Jalen answered its own question: {echo!r}"
        )


def test_a_short_answer_that_appears_in_the_question_is_not_echo():
    """
    "ChatGPT or Gemini?" - and he says "ChatGPT". The word IS in the
    question, and an over-eager echo filter would reject the one answer the
    question invited. Two words or fewer are never treated as echo.
    """
    question = "Which one did you mean, boss - ChatGPT or Gemini?"
    for answer in ("ChatGPT", "Gemini", "the second"):
        # A FRESH GATE EACH. The window is one-shot now: the first accepted
        # utterance closes it, so reusing one gate would test the close
        # rather than the echo floor.
        assert _Gate().asked(question).should_act_on(answer, wake_initiated=False), answer


def test_real_user_answers_are_not_mistaken_for_echo():
    """
    Measured: zero false positives across the 853 real user utterances in
    data/audit.jsonl paired against real Jalen replies. A sample of them
    lives here so a change to the detector has to face it.
    """
    question = ("I'm here, Boss - just waiting on one thing from you. Do you "
                "want this posted to the channel or to Saved Messages?")
    for answer in (
        "just make the post and send it to my saved messages.",
        "post it to the channel please",
        "neither, leave it as a draft for now",
        "I would like you to send it.",
    ):
        assert _Gate().asked(question).should_act_on(answer, wake_initiated=False), answer


# ---------------------------------------------------------------------------
# IT HAS TO END
# ---------------------------------------------------------------------------
def test_the_window_expires():
    gate = _Gate().asked("Which one did you mean, boss?", window_s=0.05)
    assert gate.should_act_on("the second one", wake_initiated=False)
    time.sleep(0.08)
    assert not gate.should_act_on("the second one", wake_initiated=False)


def test_checking_the_window_never_writes_to_it():
    """
    THE RACE, and why _expectation_open no longer clears an expired window.

    It runs on the microphone loop; _expect_an_answer writes the same
    attribute from a turn thread. Clearing here is a read-then-write with a
    time.monotonic() and a comparison in between, so a turn thread
    installing a fresh window in that gap had it destroyed by an
    unconditional `= None`. And the interleaving is likeliest exactly when
    it hurts: the turn thread reaches the tail the instant _await_playback
    returns, which is the instant the mic loop starts seeing frames again.
    The symptom would be indistinguishable from the original bug.
    """
    gate = _Gate().asked("Which one did you mean, boss?", window_s=0.01)
    time.sleep(0.03)
    assert not gate._expectation_open()
    assert gate._expecting is not None, (
        "the microphone loop wrote to state a turn thread owns"
    )
    # Expired is expired, however many times it is asked.
    assert not gate._expectation_open()
    assert not gate.should_act_on("the second one", wake_initiated=False)


def test_a_new_wake_word_turn_works_regardless_of_the_window():
    gate = _Gate().asked("Which one did you mean, boss?")
    assert gate.should_act_on("open chrome", wake_initiated=True)
    gate._forget_expectation()
    assert gate.should_act_on("open chrome", wake_initiated=True)


def test_the_kill_phrase_is_still_exempt_either_way():
    for gate in (_Gate(), _Gate().asked("Which one, boss?")):
        assert gate.should_act_on("stop", wake_initiated=False)
        assert gate.should_act_on("cancel", wake_initiated=False)


def test_the_existing_pending_states_are_untouched():
    """
    ask_user, the RED confirmation and the AMBER stop-window each already
    had their own exemption, and each is answered by a queue a coroutine is
    sitting on. The new window must not replace, weaken or shadow them.
    """
    for pending in ("confirmation", "stop", "reply"):
        gate = _Gate(**{pending: True})
        assert gate._expecting is None, "no free window is needed - the flag is the exemption"
        assert gate.should_act_on("the notes one", wake_initiated=False)


# ---------------------------------------------------------------------------
# SAFETY. A conversational window must not become a way to approve things.
# ---------------------------------------------------------------------------
def test_an_open_question_does_not_approve_anything_by_itself():
    """
    The window decides ONE thing: whether a sentence is heard as addressed
    to Jalen. It is not consulted when a RED action asks for confirmation -
    that is `_awaiting_confirmation` plus the answer queue, and this window
    neither sets nor satisfies it.
    """
    gate = _Gate().asked("Which file did you mean, boss?")
    assert gate._expecting is not None
    assert gate._awaiting_confirmation is False
    assert gate._awaiting_stop is False
    assert gate._awaiting_reply is False


def test_the_window_records_which_turn_opened_it():
    """
    Bound to a turn, not floating. A stale window from three turns ago must
    be identifiable as stale rather than silently answering for the newest
    question.
    """
    gate = _Gate().asked("Which one, boss?", turn_id=7)
    assert gate._expecting.turn_id == 7
    assert gate._expecting.question.startswith("Which one")


def test_a_second_question_replaces_the_first():
    gate = _Gate().asked("Which file, boss?", turn_id=1)
    gate.asked("Actually - which folder, boss?", turn_id=2)
    assert gate._expecting.turn_id == 2
    # The OLD question's text is no longer what echo is measured against.
    assert "folder" in gate._expecting.question


# ---------------------------------------------------------------------------
# WIRING. The predicate being right is worth nothing if the loop never asks.
# ---------------------------------------------------------------------------
def test_the_microphone_opens_for_the_whole_window_not_just_the_follow_up():
    """
    The window is longer than the ordinary follow-up, and the microphone
    branch is a SEPARATE test from the address gate. If run() still only
    consults `follow_up_until`, an answer given after 12 seconds never
    reaches the gate at all and the fix is invisible.
    """
    import inspect

    source = inspect.getsource(Jalen.run)
    branch = source[source.index("in_follow_up = "):source.index("elif self.wake.feed")]
    assert "_expectation_open" in branch, (
        "the microphone branch does not know about the open question - the "
        "address gate will never see the answer"
    )


def test_the_window_opens_only_after_playback_has_finished():
    """
    THE ANCHOR, and the reason 12 seconds looked too short.

    The clock starts when Jalen STOPS TALKING, not when the model finished
    generating. dispatch_turn already blocks on _await_playback() before it
    touches the follow-up window; the question window is opened in the same
    place for the same reason. Anchored at generation time instead, a
    forty-second answer would spend its whole window being spoken.
    """
    import inspect

    source = inspect.getsource(Jalen.run)
    playback = source.index("self._await_playback()")
    opened = source.index("_expect_an_answer")
    assert playback < opened, (
        "the question window starts before the reply has finished playing - "
        "it is counting Jalen's own speech as the user's thinking time"
    )


def test_a_reply_that_asks_nothing_closes_the_previous_window():
    """
    Otherwise the last question ever asked stays open behind every
    subsequent statement, and the bound becomes "thirty seconds after
    anything" instead of "thirty seconds after a question".
    """
    import inspect

    source = inspect.getsource(Jalen.run)
    tail = source[source.index("follow_up_until = time.monotonic()"):]
    assert "_forget_expectation" in tail[:2600], (
        "a turn that asked nothing leaves the previous question's window "
        "open"
    )


def test_rejection_is_still_silent():
    """
    Unchanged and load-bearing. Announcing "I didn't catch that" at a room
    that was not talking to him is the self-inflicted interruption the
    address gate exists to end, and the announcement is itself speech the
    microphone can hear.
    """
    import inspect

    source = inspect.getsource(Jalen.run)
    start = source.index("if not self.should_act_on(")
    after = source[start:start + 700]
    assert "self.say(" not in after
    assert "not addressed to Jalen" in after


# ---------------------------------------------------------------------------
# WHAT AN INDEPENDENT REVIEW OF THE FIRST VERSION FOUND
#
# The feature shipped, five fresh agents attacked the final state, and the
# conversation reviewer found a self-triggering loop on the ORDINARY path -
# no race, no second speaker, no unusual timing. Everything below is a
# reproduction of something that was really there.
# ---------------------------------------------------------------------------
def test_jalens_reply_to_the_answer_is_not_taken_as_a_second_answer():
    """
    THE CRITICAL ONE.

        turn 7   Jalen: "...Changes.md and Changes.pdf. Which one did you
                 mean?"      -> window opens on THAT question
        turn 7   he:    "changes dot pdf"     -> accepted, correctly
        turn 8   Jalen: "Opening Changes.pdf on your desktop now, Boss."

    Turn 8's reply comes back through the microphone into a window that is
    still open and still comparing against turn 7's QUESTION - which turn
    8's reply does not match. So Jalen answered itself, dispatched a real
    turn on its own sentence, and spoke again into the same open window.

    Two things fix it and both are kept: the window is ONE-SHOT, so
    accepting his answer closes it; and the echo test compares against
    whatever Jalen last said, not only against the question.
    """
    question = ("I see two matches on your Desktop, Boss - Changes.md and "
                "Changes.pdf. Which one did you mean?")
    gate = _Gate().asked(question, turn_id=7)
    assert gate.should_act_on("changes dot pdf", wake_initiated=False)
    assert gate._expecting is None, "the window stayed open after his answer"

    for own in ("Opening Changes.pdf on your desktop now, Boss.",
                "opening changes pdf on your desktop now boss",
                "Opening Changes.pdf on your desktop now"):
        assert not gate.should_act_on(own, wake_initiated=False), (
            f"Jalen answered its own reply: {own!r}"
        )


def test_the_window_closes_on_the_first_answer():
    gate = _Gate().asked("Which one did you mean, boss?")
    assert gate.should_act_on("the second one", wake_initiated=False)
    assert gate._expecting is None
    # The cost, stated: a two-part answer needs his name for the second half.
    assert not gate.should_act_on("actually the first one", wake_initiated=False)


def test_a_pending_rating_does_not_switch_the_echo_defence_off():
    """
    _rating_is_pending returned True with no echo test at all, for five
    minutes, starting from a question Jalen had just said out loud - so the
    moment most likely to produce an echo was the moment the defence was
    off. Unlike the three _awaiting_* flags, nothing is blocked on a queue
    here: a non-numeric utterance falls through to the router and the brain
    as a command.
    """
    import time as _time

    rating_question = "Hey boss - how do you rate my work out of ten?"
    gate = _Gate().asked(rating_question)
    gate._pending_rating = {"about": "x", "did": "", "asked_at": _time.time()}

    for own in (rating_question, "how do you rate my work out of ten"):
        assert not gate.should_act_on(own, wake_initiated=False), (
            f"Jalen's own rating question came back and was acted on: {own!r}"
        )
    # A real rating still gets through.
    assert gate.should_act_on("eight out of ten", wake_initiated=False)


@pytest.mark.parametrize("echo", [
    "which one did you mean",
    "um which one did you mean",
    "so which one did you mean",
    "witch one did you mean",          # as speech recognition writes it
    "which one did you main",
])
def test_one_stray_word_no_longer_defeats_the_echo_detector(echo):
    """
    The leaky rule was "85% of the words appear", which at four, five and
    six words rounds to ALL of them - so a single "um" from the transcriber
    got through, and the tail of the question is the single most likely
    thing the microphone hears. One word of slack, not a percentage.
    """
    gate = _Gate().asked(
        "I see two matches on your Desktop, Boss - Changes.md and "
        "Changes.pdf. Which one did you mean?"
    )
    assert not gate.should_act_on(echo, wake_initiated=False), echo


def test_naming_one_of_the_options_is_an_answer_not_an_echo():
    """
    THE OTHER HALF, and the one a text-only test cannot get right.

    "Do you want the whole thread read out, or just the last message?" -
    the natural answer is one of those phrases, so the answer IS a
    contiguous run inside the question by construction, and the first
    version dropped all of them in silence. That is the bug this whole
    feature exists to fix, reintroduced for the answers most worth having.

    Echo is acoustic and cannot arrive more than a moment after Jalen
    stopped, so time is the gate and text is only the test.
    """
    from jarvis.app import ECHO_TAIL_S, Expectation

    question = ("Gmail's live now, Boss. Do you want the whole thread read "
                "out, or just the last message?")
    for answer in ("just the last message", "the whole thread",
                   "read out the whole thread"):
        gate = _Gate()
        gate._last_reply_text = question
        # He answered after thinking about it, which is what people do.
        long_ago = time.monotonic() - (ECHO_TAIL_S + 1)
        gate._last_reply_at = long_ago
        gate._expecting = Expectation(question=question, turn_id=1,
                                      opened_at=long_ago,
                                      expires_at=time.monotonic() + 30)
        assert gate.should_act_on(answer, wake_initiated=False), (
            f"{answer!r} is the answer the question asked for"
        )


def test_the_same_words_a_moment_after_jalen_stopped_are_still_echo():
    """The other side of the same clock, so the bound is a bound."""
    gate = _Gate().asked(
        "Gmail's live now, Boss. Do you want the whole thread read out, "
        "or just the last message?"
    )
    assert not gate.should_act_on("just the last message", wake_initiated=False)


def test_a_cyrillic_reply_is_still_defended():
    """
    _SPOKEN_WORD was [a-z0-9']+, so for a Russian reply the word list came
    back empty and the detector returned False for a perfect verbatim echo -
    while the window itself still opened, because the question mark is
    ASCII. He speaks Russian and Uzbek, so the defence has to survive the
    alphabet. Written as escapes rather than typed, per CLAUDE.md.
    """
    question = ("Шеф, какой "
                "из двух "
                "файлов вы "
                "имели в виду?")
    gate = _Gate().asked(question)
    assert not gate.should_act_on(question, wake_initiated=False), (
        "a verbatim Cyrillic echo was not detected at all"
    )


def test_a_turn_that_said_nothing_does_not_re_arm_the_last_question():
    """
    The tail decides from _last_reply_text, which say() only sets AFTER its
    muted early-return - so a muted turn, or an intent that returns no
    line, left the previous question's text in place and stamped a fresh
    thirty seconds onto a question minutes old. Muted, that never stopped.
    """
    import inspect

    source = inspect.getsource(Jalen.run)
    tail = source[source.index("follow_up_until = time.monotonic()"):]
    assert "spoke_this_turn" in tail[:2600], (
        "a turn that spoke nothing still re-arms the previous question"
    )


def test_the_window_is_settled_even_when_the_turn_raised():
    """
    dispatch_turn re-raises, so anything after its finally is unreachable on
    a brain failure, a tool failure or a cancellation - and
    _forget_expectation has exactly one call site in the loop. The previous
    question's window was left open for the rest of its thirty seconds with
    no reply ever spoken, which is the unbounded exemption arriving exactly
    when Jalen is least able to notice.
    """
    import inspect

    source = inspect.getsource(Jalen.run)
    body = source[source.index("def dispatch_turn"):source.index("self.prewarm()")]
    finally_at = body.index("finally:")
    assert body.index("_expect_an_answer") > finally_at
    assert body.index("_forget_expectation") > finally_at, (
        "the window is settled outside the finally, so an exception skips it"
    )


def test_the_echo_guard_in_the_continuation_branch_is_load_bearing():
    """
    DEAD TO THE WHOLE SUITE UNTIL NOW. deb01ef put
    _sounds_like_its_own_voice at the top of _continues_last_utterance and
    called it the fix for Jalen self-triggering through the dangling-tail
    branch. But every _Gate stub leaves _last_reply_text empty, so the
    detector returned False at the `not said_words` line on every call from
    that branch - and a reviewer deleted the two lines and ran the entire
    suite: 3174 passed, nothing failed.

    Here the stub says what Jalen actually just said, which is the only
    state in which those lines can do anything.
    """
    own_words = "I have opened Chrome and sent it to your saved messages."
    gate = _Gate()
    gate._last_user_text = "send it to"          # a dangling tail
    gate._last_user_at = time.monotonic()
    gate._last_reply_text = own_words
    gate._last_reply_at = time.monotonic()

    # "to your saved messages" opens like a fragment, so the dangling-tail
    # branch would admit it on the words alone. The echo guard is the only
    # thing that can refuse it.
    assert not gate.should_act_on("to your saved messages", wake_initiated=False), (
        "Jalen's own words came back through the microphone during the "
        "stitch window and were acted on"
    )
    # A real continuation, which is what the branch exists for, still lands.
    gate2 = _Gate()
    gate2._last_user_text = "send it to"
    gate2._last_user_at = time.monotonic()
    assert gate2.should_act_on("to my channel", wake_initiated=False)
