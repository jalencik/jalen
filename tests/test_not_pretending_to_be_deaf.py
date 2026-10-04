"""
He talks to Jalen and Jalen acts as though nothing was said.

THE SYMPTOM, COUNTED
--------------------
data/audit.jsonl holds 105 sentences logged as "ignored - not addressed to
Jalen". Read by hand against the reply that came before each one, 78 were
plainly for him, 15 were ambiguous and 12 were background (a song's lyrics,
"Thank you." hallucinated out of silence, his own reply coming back through
the speakers). `scripts/measure_address_gate.py` replays them through the
real gate; it is the source of every number below and can be re-run
(`JALEN_AUDIT_LOG=<copy of audit.jsonl>` points the corpus tests at a log).

Replayed through the gate as it stood BEFORE this change, with the state
Jalen was in at the time rebuilt from the log. 97 of the 105 rows are older
than the answer window (20 September, commit 05378c4), so each is given the
window it would have had, and an answer closes it, as it does live:

    would be acted on now ......... 43   (for him 38, ambiguous 3, background 2)
    still ignored ................. 62   (for him 40)
    of those 40, by cause ......... conversation 11, name misheard 8, answer
                                    to a statement 7, polite request 7, name
                                    said last 5, fragment 2

After the first version of this change: 62 acted on (for him 57, ambiguous 3,
background 2). An independent review then found the doors admitted speech
aimed at other people and that the echo test behind them was armed for 3
seconds after a reply BEGAN; tests/test_listening_does_not_obey_other_people_or_itself.py
is that review, answered. Its fixes cost 1 of the 57: the open-vocabulary door
("any word, comma, polite request") is gone, and the nine names that never
appeared in a refused sentence with it (nothing in the corpus used them). Now:
61 acted on (for him 56, ambiguous 3, background 2). Of the 56, 43 are
admitted by the windows alone and 13 by a door: name misheard 5 alone,
"hey Jalen" said last 3, polite request 4 (one row is both); the long answers
that outlived their window are counted among the 43. No ambiguous or
background row changed - the 5 that are acted on are answers admitted by the
open question window, the documented cost of that window.

The same, for the 8 rows written since the answer window shipped (what the
gate really did, not a replay of older ones): for him 3, ambiguous 4,
background 1; acted on after this change: for him 2, ambiguous 1, background 0.

And the other half of the same complaint, the 50 times since the gate shipped
that Jalen said "I didn't catch that." to a microphone window opened by his
wake word: in 11 of them he said something again within 40 seconds. The
reply to the same words, typed or caught by the follow-up window, is "Yes,
Boss?" - the router has a rule for exactly that ("A bare name with nothing
after it").

WHAT THIS CHANGE DOES, AND WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------------
1. His name as speech recognition really writes it. Whisper has produced
   Helen, Ellen, Alain, Dylan, Delic, Jalit, Galen, Janet, Janine, Johnny,
   Jolly, Yellen, Dallin, Jameet, E.J. and "Ejjalin" for his voice - each
   seen in the log immediately before a real command. The list is MEASURED
   and short on purpose; guessing the rest of the alphabet would turn every
   greeting in the room into a command. REVISED after the review: only the
   seven that rescued a sentence the gate had refused (Helen, Ellen, Alain,
   Dylan, Delic, Jalit, E.J.) are a door, and not in barge-in; the other nine
   were only ever seen in sentences the wake word had already carried. The
   door for "any other single word followed by a polite request" is gone.
2. "Hey Jalen" said anywhere in the sentence, not only first: "open it, hey
   Jalen." was dropped for putting the name last.
3. A polite request - "could you please...", "I would like you to..." - that
   arrives in the 12-second follow-up window the MICROPHONE opened. The two
   gates now agree: run() tells the address gate which sound opened the
   window. Everything else without his name is still refused. REVISED: only
   those two forms; "can/will/would you please" and "I'd like you to" were 3
   sightings in 950 sentences and are what people say to each other.
4. A wake word followed by nothing is answered like the bare name, "Yes,
   Boss?", and listens for the command for the same 12 seconds.
5. A long answer is no longer refused for ending after its window did (see
   tests/test_a_long_answer_is_still_an_answer.py): the microphone opened for
   it when it began, the address gate asked again when it was over.

NOT DONE, because no rule survives the measurement: the ten
conversational sentences with no name and no request ("what are you doing",
"you're gonna click log in"). In a follow-up window they are
indistinguishable from a song's lyrics or a podcast, which is what the
background rows are. The nearest rule - a sentence of four words or more that
says "you" - would act on 7 of the ten (and 5 other refused rows meant for
him) and also on 1 of the 12 background rows (a hallucinated "you can see it"
loop) and 1 of the 15 ambiguous ones, in a window that opens after every
reply. That is a small count on a corpus too small to put a rate on, and the
corpus under-represents what the rule would let in (its background rows are
mostly silence hallucinated into "Thank you.", not a song or a podcast). It
also contradicts the rule he gave: the name starts a conversation. Not shipped.
Also not done: an answer to a reply that did not end in a question (4 rows).

THE TWO RULES THIS FILE MUST NOT BREAK (CLAUDE.md): the microphone gate and
the address gate agree - pinned below by reading run(); and the address gate
is the echo defence, so every widening calls _sounds_like_its_own_voice() -
pinned below by having Jalen say each accepted sentence first.
"""
from __future__ import annotations

import importlib.util
import inspect
import time
from pathlib import Path

import pytest

from jalen.app import Jalen
from jalen.brain import router
from jalen.config import CONFIG

ROOT = Path(__file__).resolve().parent.parent


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
        self.cfg = CONFIG
        self.kill_phrases = set(CONFIG.get_path("safety.kill_phrases"))

    should_act_on = Jalen.should_act_on
    _continues_last_utterance = Jalen._continues_last_utterance
    _expectation_open = Jalen._expectation_open
    _rating_is_pending = Jalen._rating_is_pending
    RATING_EXPIRES_S = Jalen.RATING_EXPIRES_S
    _sounds_like_its_own_voice = Jalen._sounds_like_its_own_voice

    def just_said(self, text: str):
        self._last_reply_text = text
        self._last_reply_at = time.monotonic()
        return self

    def acts_on(self, text: str, opened_by: str = "") -> bool:
        return bool(self.should_act_on(text, False, opened_by=opened_by))


def _run_source() -> str:
    return inspect.getsource(Jalen.run)


# ---------------------------------------------------------------------------
# 1. HIS NAME, AS SPEECH RECOGNITION REALLY WRITES IT
# ---------------------------------------------------------------------------
# (heard, what is left once the name is taken off)
MISHEARD_NAME_CALLS = [
    ("Helen.", ""),
    ("Ellen", ""),
    ("E.J.", ""),
    ("Dylan, what do you mean by that?", "what do you mean by that?"),
    ("Hey Delic, play some music", "play some music"),
    ("Hey, Jalit. What time is it?", "What time is it?"),
    ("Alain, could you please send it?", "could you please send it?"),
    ("Hey Dylan could you please play something", "could you please play something"),
    ("Hey, Ellen, please go on.", "please go on."),
]


@pytest.mark.parametrize("heard,rest", MISHEARD_NAME_CALLS)
def test_a_misheard_name_is_recognised_and_taken_off(heard, rest):
    got = router.called_by_a_misheard_name(heard)
    assert got is not None, f"{heard!r} is him calling Jalen and was not recognised"
    assert got == rest, f"{heard!r}: left {got!r}, wanted {rest!r}"


NOT_A_CALL = [
    # A real person's name used as a noun, with no vocative punctuation.
    "Helen is my friend",
    "Dylan Thomas wrote poems",
    "Dylan went home.",
    "so Helen said she would call",
    # A single unlisted word is not enough. It used to be enough with a polite
    # request after it ("Rijal, could you please open it") and the review
    # reproduced "Sarah, could you please send me the report"; see
    # tests/test_listening_does_not_obey_other_people_or_itself.py.
    "John, how are you",
    "Darling, you look great",
    "Honey, the dinner is ready",
    "Rijal, could you please open it?",
    "Darling, could you please go to my window",
    "Hey, child, I would like you to click that",
    # Names that were only ever seen in sentences the wake word had carried.
    "Galen, you are doing poorly",
    "Hey, Johnny, please go on.",
    "Yellen, are you listening to me?",
    "Hey Dallin could you please play something",
    # ... and a bare "please" is not a polite request: every television drama
    # has someone saying it after a name.
    "Mom, please, listen to me",
    "Wait, please don't go",
    "Dad, please",
    "Darling, please go to my window",
    # What recognition hallucinates out of silence and background noise.
    "Thank you.",
    "Thanks for watching!",
    "Okay.",
    "Mm-hmm.",
    "you",
    "Bye.",
    "Let's check what's up next.",
    "We're going to go to the next slide.",
    "",
    "   ",
]


@pytest.mark.parametrize("heard", NOT_A_CALL)
def test_things_that_are_not_a_call_are_not_mistaken_for_one(heard):
    assert router.called_by_a_misheard_name(heard) is None, (
        f"{heard!r} is not him calling Jalen"
    )


def test_the_strict_name_test_is_left_exactly_as_it_was():
    """
    addressed_to_jalen() also decides whether the taint clears (`from_him` in
    run()), what the router strips and what the kill phrase accepts. The
    measured aliases are a SECOND, narrower door into the gate and must not
    leak into any of those.
    """
    for heard, _ in MISHEARD_NAME_CALLS:
        assert not router.addressed_to_jalen(heard), heard


def test_the_alias_list_is_short_and_every_entry_was_seen():
    names = router.MISHEARD_NAMES
    assert "helen" in names and "dylan" in names and "e.j" in names
    # A list that grows by guessing is the always-listening assistant. Raise
    # this number only with a log line to point at: a REFUSED sentence meant
    # for him that began with the name (the corpus test in
    # test_listening_does_not_obey_other_people_or_itself.py checks it).
    assert len(names) <= 7, sorted(names)
    # Words that are ordinary English, or that are other people's names a
    # household is likely to say, are not on it.
    for word in ("darling", "agile", "child", "john", "doris", "mom", "honey", "baby",
                 "janet", "johnny", "galen"):
        assert word not in names, word


# ---------------------------------------------------------------------------
# 2. "HEY JALEN" SAID LAST, OR IN THE MIDDLE
# ---------------------------------------------------------------------------
GREETED_LATER = [
    "Open the file. Hey Jalen.",
    "sign me in to it. Hey Jalen. Hey Jalen.",
    "Play it Jalen Jalen Hey Jalen",
    "Could you please? Hey, Jellin,",
    "Yes of course, hey, Jalen, yes of course",
    "just tell me if... Hey Jelen. Hey Jelen.",
]


@pytest.mark.parametrize("heard", GREETED_LATER)
def test_hey_jalen_anywhere_in_the_sentence_is_him_calling(heard):
    assert router.greets_him_mid_sentence(heard), heard


NOT_A_GREETING = [
    # About him, not to him. tests/test_greeting_punctuation.py pins the same.
    "So Jalen is my assistant",
    "Jalen is fast and Jalen is smart",
    "He told Jalen to wait",
    # Somebody else.
    "Hey Julian, over here",
    "Hey, Jolene is a good song",
    "Hey Google, play some music",
    "OK Google, what is the weather",
    "Thank you.",
    "",
]


@pytest.mark.parametrize("heard", NOT_A_GREETING)
def test_a_name_without_a_greeting_in_front_of_it_is_still_just_a_name(heard):
    assert not router.greets_him_mid_sentence(heard), heard


# ---------------------------------------------------------------------------
# 3. A POLITE REQUEST IN THE WINDOW THE MICROPHONE OPENED
# ---------------------------------------------------------------------------
POLITE_REQUESTS = [
    "Could you please open Chrome",
    "could you please continue",
    "I would like you to send it.",
    "I would like you to",
    "Hey, could you please make a post about it?",
    "Hey man, yeah, could you please do that",
]


@pytest.mark.parametrize("heard", POLITE_REQUESTS)
def test_a_polite_request_is_recognised(heard):
    assert router.opens_with_a_request(heard), heard


NOT_A_REQUEST = [
    # Lyrics and dialogue that begin like one.
    "Could you be loved",
    "Can you hear me?",
    "Can you feel the love tonight",
    "Would you like some tea",
    "please don't go",
    "Please, please, please",
    "I would like to go home",
    "I want you to know",
    "I need you to understand",
    # Forms of asking he does not use (3 sightings in 950 sentences) and that
    # people say to each other.
    "Would you please go to the site and play it",
    "Can you please repeat that",
    "Will you please stop doing that",
    "I'd like you to read it out",
    # Silence and noise.
    "Thank you.",
    "you",
    "Mm-hmm.",
    "",
]


@pytest.mark.parametrize("heard", NOT_A_REQUEST)
def test_speech_that_only_resembles_a_request_is_not_one(heard):
    assert not router.opens_with_a_request(heard), heard


# ---------------------------------------------------------------------------
# THE GATE ITSELF
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("heard", [h for h, _ in MISHEARD_NAME_CALLS])
def test_the_gate_acts_on_a_misheard_name_in_the_window_a_sound_vouches_for(heard):
    assert _Gate().acts_on(heard, "follow_up"), heard
    # Not barge-in (it fires on his own voice and on a cough), not the answer
    # window (it vouches for answers, which need no door) and not a caller that
    # does not say - the same rule as the polite request. It was "any window"
    # until the review reproduced "Helen, dinner is ready" over barge-in.
    for opened_by in ("barge", "answer", ""):
        assert not _Gate().acts_on(heard, opened_by), (heard, opened_by)


@pytest.mark.parametrize("heard", GREETED_LATER)
def test_the_gate_acts_on_a_name_said_last(heard):
    assert _Gate().acts_on(heard, "follow_up"), heard


@pytest.mark.parametrize("heard", POLITE_REQUESTS)
def test_the_gate_acts_on_a_polite_request_in_the_follow_up_window(heard):
    assert _Gate().acts_on(heard, "follow_up"), heard


@pytest.mark.parametrize("heard", POLITE_REQUESTS)
def test_the_same_request_is_still_refused_when_no_window_vouches_for_it(heard):
    """
    THE MICROPHONE GATE AND THE ADDRESS GATE AGREE. The request is accepted
    only because the sound-gate opened the follow-up window for it. Barge-in
    fires on Jalen's own voice and on a cough, so it does not vouch; and a
    sentence the gate is handed with no window named gets no exemption.
    """
    for opened_by in ("", "barge"):
        assert not _Gate().acts_on(heard, opened_by), (heard, opened_by)


@pytest.mark.parametrize("heard", [
    "so anyway the weather is nice",
    "no I told him that already",
    "Thank you.",
    "you",
    "Mm-hmm.",
    "Okay.",
    "Yeah.",
    "Let's check what's up next.",
    "We're going to go to the next slide.",
    "what are you doing",
    "Can you hear me?",
    "Could you be loved",
    "John, how are you",
])
def test_everything_else_without_his_name_is_still_ignored_in_the_window(heard):
    """The line that keeps this from becoming an always-listening assistant."""
    assert not _Gate().acts_on(heard, "follow_up"), heard


# ---------------------------------------------------------------------------
# THE ECHO DEFENCE. Every widening must refuse Jalen's own voice.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("heard", [h for h, _ in MISHEARD_NAME_CALLS if len(h.split()) >= 3])
def test_a_misheard_name_that_is_his_own_voice_is_refused(heard):
    # He is READING it out - an email, a transcript - and it comes back.
    # (Three words and up: under that the echo test refuses nothing by design,
    # so that "ChatGPT" can answer "ChatGPT or Gemini?". Jalen saying the bare
    # word "Helen." aloud is not a case.)
    gate = _Gate().just_said(heard)
    assert not gate.acts_on(heard, "follow_up"), heard


@pytest.mark.parametrize("heard", GREETED_LATER)
def test_a_greeting_that_is_his_own_voice_is_refused(heard):
    gate = _Gate().just_said(heard)
    assert not gate.acts_on(heard, "follow_up"), heard


@pytest.mark.parametrize("heard", POLITE_REQUESTS)
def test_a_request_that_is_his_own_voice_is_refused(heard):
    gate = _Gate().just_said(heard)
    assert not gate.acts_on(heard, "follow_up"), heard


def test_his_own_tail_is_refused_too_not_only_the_whole_sentence():
    said = "Okay. Dylan, I would like you to send the file when it is ready."
    gate = _Gate().just_said(said)
    # No punctuation, as speech recognition writes it back, and from the middle.
    assert not gate.acts_on("Dylan I would like you to send the file when it is ready", "follow_up")
    assert not gate.acts_on("I would like you to send the file when it is ready", "follow_up")
    # ... and a different sentence in the same window is still his.
    assert gate.acts_on("I would like you to open the notes instead", "follow_up")


def test_the_widening_never_changes_the_wake_or_kill_paths():
    gate = _Gate()
    assert gate.should_act_on("open chrome", True)
    assert gate.should_act_on("stop", False)
    assert not gate.should_act_on("open chrome", False)


# ---------------------------------------------------------------------------
# THE NAME IS TAKEN OFF BEFORE THE ROUTER SEES IT
# ---------------------------------------------------------------------------
def test_a_misheard_name_does_not_cost_him_the_free_router_path():
    """
    "Dylan, what time is it" used to reach the brain whole, a model call for
    a question the router answers in 90ms. With the name taken off it is the
    same sentence he would have said with the right one.
    """
    routing = router.IntentRouter(CONFIG)
    rest = router.called_by_a_misheard_name("Dylan, what time is it")
    assert rest == "what time is it"
    assert routing.route(rest) is not None
    assert routing.route("Dylan, what time is it") is None


def test_a_bare_misheard_name_is_answered_like_the_bare_name():
    routing = router.IntentRouter(CONFIG)
    assert router.called_by_a_misheard_name("Helen.") == ""
    assert router.without_a_misheard_name("Helen.") == "Jalen"
    intent = routing.route(router.without_a_misheard_name("Helen."))
    assert intent is not None and intent.tool == "jalen_ack"


def test_the_name_is_taken_off_only_when_it_was_a_mishearing():
    take = router.without_a_misheard_name
    assert take("Dylan, what time is it") == "what time is it"
    assert take("Hey Delic, play some music") == "play some music"
    # His real name, in any pronunciation: left exactly as it was, because the
    # router has always stripped that itself and the taint rule reads it.
    for same in ("Jalen, what time is it", "Hey Jaylen open chrome", "Jarvis, stop"):
        assert take(same) == same
    # Not a call at all: untouched.
    for same in ("Helen is my friend", "what time is it", "Thank you.", "", "Dylan Thomas wrote poems"):
        assert take(same) == same


# ---------------------------------------------------------------------------
# WIRING: the loop has to hand the gate what it needs, and say what it did
# ---------------------------------------------------------------------------
def test_run_tells_the_address_gate_which_sound_opened_the_window():
    source = _run_source()
    assert "opened_by=opened_by" in source, (
        "run() calls should_act_on without saying which gate opened the "
        "window - the address gate cannot agree with a gate it cannot see"
    )
    for kind in ('opened_by = "barge"', 'opened_by = "follow_up" if in_follow_up else "answer"',
                 'opened_by = "wake"'):
        assert kind in source, f"nothing in run() ever sets {kind}"


def test_every_window_that_opens_sets_who_opened_it():
    """
    A window the loop opens without saying why inherits whatever the previous
    one left in the variable - which is how a barge-in on a cough would
    inherit the follow-up window's exemption.
    """
    source = _run_source()
    sites = [i for i in range(len(source)) if source.startswith("listening = True", i)]
    assert len(sites) >= 4, "the window-opening sites have moved - re-check this guard"
    for at in sites:
        after = source[at:at + 700]
        assert "opened_by" in after or "collector.resume" in source[max(0, at - 300):at], (
            "a listening window is opened without recording who opened it:\n"
            + source[max(0, at - 200):at + 200]
        )


def test_the_name_that_cleared_the_taint_is_still_only_the_strict_one():
    source = _run_source()
    assert "from_him = wake_initiated or addressed_to_jalen(text)" in source, (
        "a misheard name or a polite request must never certify that it was "
        "him - see the taint clear in process()"
    )
    # ... and it is computed before the name is taken off the text, or the
    # rewritten "Jalen" would pass it.
    assert source.index("from_him = wake_initiated or addressed_to_jalen(text)") < source.index(
        "without_a_misheard_name("), "the misheard name is rewritten before from_him is decided"


def test_an_answer_is_never_rewritten():
    """
    "Who should I email?" - "Dylan, Helen and Sam." The first word is on the
    list and the whole thing is an ANSWER; taking "Dylan" off would hand the
    question half of it. The gate closes the answer window as it accepts, so
    whether this was an answer has to be decided before it runs.
    """
    source = _run_source()
    decided = source.index("was_an_answer = (")
    gate = source.index("if not self.should_act_on(")
    rewrite = source.index("without_a_misheard_name(")
    assert decided < gate < rewrite
    assert "if not was_an_answer:" in source[rewrite - 120:rewrite]
    for state in ("_awaiting_confirmation", "_awaiting_reply", "_expectation_open()",
                  "_rating_is_pending()", "_continues_last_utterance(text", "outlived"):
        assert state in source[decided:gate], f"{state} no longer counts as answering"
    # The name for it is not `answering`: that is the backlog guard's, further
    # down, and means something narrower. Two variables of one name was how a
    # reader would have taken the second assignment for the first.
    assert source.count("answering = (") == 0 or source.index("answering = (") > rewrite


def test_an_ignored_sentence_still_costs_nothing_to_say_and_now_says_why():
    source = _run_source()
    start = source.index("if not self.should_act_on(")
    after = source[start:start + 900]
    assert "self.say(" not in after            # still silent
    assert "not addressed to Jalen" in after   # the line the history is read by
    assert '"opened_by"' in after              # and the next measurement can split it


# ---------------------------------------------------------------------------
# 4. A WAKE WORD FOLLOWED BY NOTHING
# ---------------------------------------------------------------------------
class _Speaker:
    def __init__(self, muted=False):
        self.said = []
        self.muted = muted
        self.played = 0


class _Waker:
    """The few things _say_yes_and_listen touches."""

    def __init__(self, muted=False):
        self.muted = muted
        self.said = []
        self.window = None
        self.refreshed = 0
        self._turn_seq = 7
        self._last_reply_text = ""
        self._last_reply_at = 0.0
        self.cfg = CONFIG
        self._follow_up_s = float(CONFIG.get_path("conversation.follow_up_timeout_s", 12))

    def say(self, text, **_):
        if self.muted:
            return
        self.said.append(text)
        self._last_reply_text = text
        self._last_reply_at = time.monotonic()

    def _await_playback(self, *a, **k):
        pass

    def _expect_an_answer(self, question, turn_id, window_s=None, kind=""):
        self.window = (question, turn_id, window_s)
        self.window_kind = kind

    def _refresh_orb(self, *a, **k):
        self.refreshed += 1

    _say_yes_and_listen = getattr(Jalen, "_say_yes_and_listen", None)


def test_a_wake_word_with_nothing_after_it_is_answered_like_the_bare_name():
    assert Jalen.__dict__.get("_say_yes_and_listen") is not None, "no such method"
    waker = _Waker()
    waker._say_yes_and_listen()
    assert waker.said == ["Yes, Boss?"], waker.said
    assert "didn't catch" not in " ".join(waker.said)


def test_the_answer_to_a_bare_wake_listens_for_the_follow_up_length_not_longer():
    """
    A false wake - a song, the television - opens this window too. Thirty
    seconds is the answer window for a question he was really asked; this one
    gets the ordinary follow-up length. Measured: 0 of the 105 ignored rows
    arrive within 12 seconds of the 50 logged empty wake windows.
    """
    waker = _Waker()
    waker._say_yes_and_listen()
    question, turn_id, window_s = waker.window
    assert question == "Yes, Boss?"
    assert window_s == pytest.approx(float(CONFIG.get_path("conversation.follow_up_timeout_s", 12)))
    # Named, so what comes through it is counted on its own (the review).
    assert waker.window_kind == "bare-wake"


class _Asker(_Waker):
    """A confirmation, stop-window or ask_user is already waiting on him."""

    def __init__(self, **flags):
        super().__init__()
        self._awaiting_confirmation = flags.get("confirmation", False)
        self._awaiting_stop = flags.get("stop", False)
        self._awaiting_reply = flags.get("reply", False)
        self.threads = 0

    _acknowledge_a_bare_wake = getattr(Jalen, "_acknowledge_a_bare_wake", None)


@pytest.mark.parametrize("flag", ["confirmation", "stop", "reply"])
def test_a_wake_while_a_question_is_waiting_on_him_does_not_talk_over_it(flag, monkeypatch):
    """
    The old announcement was caught in the log talking over a RED delete
    confirmation and then cancelling it for "no answer". His next sentence is
    already spoken for; "Yes, Boss?" on top of "Confirm?" is the same bug.
    """
    import threading

    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))
    asker = _Asker(**{flag: True})
    assert Jalen.__dict__.get("_acknowledge_a_bare_wake") is not None, "no such method"
    asker._acknowledge_a_bare_wake()
    assert started == [], "a bare wake started an announcement over a waiting question"
    free = _Asker()
    free._say_yes_and_listen = lambda: None
    free._acknowledge_a_bare_wake()
    assert len(started) == 1


def test_a_muted_wake_says_nothing_and_opens_no_window():
    waker = _Waker(muted=True)
    waker._say_yes_and_listen()
    assert waker.said == []
    assert waker.window is None, (
        "nothing was said, so there is no question to answer - re-stamping a "
        "window here is the unbounded exemption dispatch_turn already guards"
    )


def test_the_empty_wake_branch_no_longer_says_it_did_not_catch_it():
    source = _run_source()
    assert 'self.say("I didn\'t catch that.")' not in source
    start = source.index("if len(utterance) == 0:")
    branch = source[start:start + 3200]
    assert "if wake_initiated:" in branch
    assert "_acknowledge_a_bare_wake" in branch
    # A window a NOISE opened is still silent - the rule the old branch was for.
    assert branch.index("if wake_initiated:") < branch.index("_acknowledge_a_bare_wake")


# ---------------------------------------------------------------------------
# 5. A WINDOW THAT HEARD NOTHING LEAVES A ROW, SO IT CAN BE COUNTED
# ---------------------------------------------------------------------------
class _Audit:
    def __init__(self):
        self.rows = []

    def write(self, kind, **fields):
        self.rows.append((kind, fields))


class _Noter:
    def __init__(self):
        self.audit = _Audit()

    _note_nothing_heard = getattr(Jalen, "_note_nothing_heard", None)


def test_a_window_that_heard_nothing_is_written_down_with_who_opened_it():
    assert Jalen.__dict__.get("_note_nothing_heard") is not None, "no such method"
    noter = _Noter()
    noter._note_nothing_heard("no-speech", "wake")
    noter._note_nothing_heard("empty-transcript", "follow_up", seconds=2.4)
    (kind1, row1), (kind2, row2) = noter.audit.rows
    assert kind1 == kind2 == "system"
    assert row1["summary"].startswith("heard nothing")
    assert row1["detail"] == {"stage": "no-speech", "opened_by": "wake"}
    assert row2["detail"]["stage"] == "empty-transcript"
    assert row2["detail"]["opened_by"] == "follow_up"
    assert row2["detail"]["seconds"] == 2.4


def test_both_silent_exits_of_the_loop_now_leave_that_row():
    source = _run_source()
    assert source.count("_note_nothing_heard(") >= 2
    assert '"no-speech"' in source and '"empty-transcript"' in source


# ---------------------------------------------------------------------------
# THE CORPUS. Skipped on a machine without the real log.
# ---------------------------------------------------------------------------
def _measure():
    path = ROOT / "scripts" / "measure_address_gate.py"
    spec = importlib.util.spec_from_file_location("measure_address_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_log():
    module = _measure()
    # data/audit.jsonl, or $JALEN_AUDIT_LOG: a worktree has no data/, and the
    # corpus is the one thing here that a fresh checkout cannot supply.
    log = module.audit_log_path()
    if not log.exists():
        pytest.skip("no data/audit.jsonl on this machine")
    rows = module.load(log)
    results = module.replay(rows)
    if len(results) < len(module.LABELS):
        pytest.skip("the log has been trimmed below the 105 labelled rows")
    return module, rows, results[: len(module.LABELS)]


def test_the_real_log_more_of_what_he_meant_is_acted_on_and_no_more_background():
    module, rows, results = _real_log()
    for_him = sum(r["accepted"] for r in results if r["label"] == "F")
    ambiguous = sum(r["accepted"] for r in results if r["label"] == "A")
    background = sum(r["accepted"] for r in results if r["label"] == "B")
    # BEFORE (914b22d, same replay): 38 / 3 / 2. The 5 ambiguous and
    # background rows that were already acted on are answers accepted inside
    # an open question window - the documented cost of that window, unchanged
    # here. The widening adds none of either. 56 after the review's fixes (57
    # before them: the open-vocabulary door's one row), of which 43 need no
    # door at all.
    assert for_him >= 56, f"only {for_him} of the 78 sentences meant for him are acted on (was 38)"
    assert ambiguous <= 3, f"{ambiguous} ambiguous rows now acted on (was 3)"
    assert background <= 2, f"{background} background rows now acted on (was 2)"


def test_the_real_log_the_outlived_window_rule_admits_only_what_he_meant():
    module, rows, results = _real_log()
    rescued = [r for r in results if r["rescued"]]
    # Answers, and a request, that began inside a window and ended after it.
    assert len(rescued) >= 5, [r["n"] for r in rescued]
    assert {r["label"] for r in rescued} == {"F"}, [(r["n"], r["label"]) for r in rescued]


def test_the_real_log_no_door_admits_a_background_or_ambiguous_row():
    module, rows, results = _real_log()
    other = [r["text"] for r in results if r["label"] in ("A", "B")]
    assert len(other) == 27
    for text in other:
        assert router.widened_door(text, "follow_up") is None, text


def test_the_real_log_jalen_still_cannot_answer_himself():
    module, rows, results = _real_log()
    replies, tried, accepted = module.echo_corpus(rows)
    # BEFORE: 11 of 2031, every one a two-word tail ("notepad. Confirm?"),
    # below the documented three-word floor under which nothing is echo.
    assert accepted <= 11, f"{accepted} of {tried} replayed replies were acted on"


# ---------------------------------------------------------------------------
# THE REPLAY ITSELF, on rows made up here - so it is tested on a machine that
# has no log, and so a change to the script cannot quietly change the numbers.
# ---------------------------------------------------------------------------
def _row(at, kind, summary, session="s1", **detail):
    import json
    return {"ts": at.isoformat(), "kind": kind, "tier": None, "tool": None,
            "summary": summary, "outcome": None, "origin": "user",
            "detail": json.dumps(detail), "session_id": session}


def _synthetic_session(seconds_after_question, heard):
    from datetime import datetime, timezone

    t0 = datetime(2026, 10, 1, 6, 0, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    return [
        _row(t0, "utterance", "Which one did you mean, the first or the second?", who="jarvis"),
        _row(t0 + timedelta(seconds=seconds_after_question), "system",
             "ignored - not addressed to Jalen", heard=heard),
    ]


LONG_DICTATION = " ".join(["so I would take the first one because it has the table"] * 4)   # 44 words


def test_the_replay_credits_a_long_answer_that_began_inside_the_window():
    module = _measure()
    (result,) = module.replay(_synthetic_session(37, LONG_DICTATION))
    assert result["vouched"] and result["accepted"] and result["rescued"]


def test_the_replay_does_not_credit_a_short_sentence_that_began_after_the_window():
    module = _measure()
    (result,) = module.replay(_synthetic_session(37, "the first one then"))
    assert not result["vouched"] and not result["accepted"]


def test_the_replay_does_not_credit_a_sentence_that_began_after_the_window_closed():
    module = _measure()
    # 44 words at 2.6/s plus 3.2s is 20s long: written at 70s, it began at 50s.
    (result,) = module.replay(_synthetic_session(70, LONG_DICTATION))
    assert not result["accepted"], result


def test_the_replay_closes_the_window_after_one_answer():
    from datetime import timedelta
    module = _measure()
    rows = _synthetic_session(8, "the first one")
    extra = dict(rows[1])
    later = dict(rows[1])
    from datetime import datetime
    later["ts"] = (datetime.fromisoformat(rows[1]["ts"]) + timedelta(seconds=3)).isoformat()
    first, second = module.replay([rows[0], extra, later])
    assert first["accepted"] and not second["accepted"], (
        "the log's second sentence would have found the window already closed"
    )


def test_the_corpus_can_be_pointed_at_a_log(monkeypatch, tmp_path):
    module = _measure()
    monkeypatch.setenv("JALEN_AUDIT_LOG", str(tmp_path / "copy.jsonl"))
    assert module.audit_log_path() == tmp_path / "copy.jsonl"
    assert module.audit_log_path(Path("explicit.jsonl")) == Path("explicit.jsonl")
    monkeypatch.delenv("JALEN_AUDIT_LOG")
    assert module.audit_log_path() == ROOT / "data" / "audit.jsonl"
