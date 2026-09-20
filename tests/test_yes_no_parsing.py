"""
"Of course not" was a yes, and it would have sent the email.

WHAT THIS FUNCTION DECIDES
--------------------------
`Jalen._parse_yes_no` is the last thing between a RED action and the machine
doing it. confirm() says "send email: antonis@gmu.edu. Confirm?", blocks, and
whatever this function returns for the next sentence is the answer. Until this
file existed it had no test of any kind.

THE BUG
-------
The NO list is a list of PHRASES - " no ", " nope ", " don't ", " not yet " -
with no rule for a bare negation. The YES list is then searched as a substring
anywhere in the sentence. So a refusal built out of "not" plus any agreement
word skips the NO list entirely and lands on the YES list:

    "of course not"        -> True     (" of course " matched)
    "I'm not sure"         -> True     (" sure " matched)
    "not right now"        -> True     (" right " matched)
    "that's not right"     -> True     (" that's right " matched)
    "certainly not"        -> True     (" certainly " matched)
    "not okay with that"   -> True     (" okay " matched)

Every one of those is a person saying no to a RED action and getting it done
anyway. Reproduced against the real function before this file was written.

AND THE CURLY APOSTROPHE
------------------------
The normaliser is `re.sub(r"[^a-z0-9' ]", " ", ...)` - a STRAIGHT apostrophe.
Speech recognition and every phone keyboard emit the curly one, so "don't"
arrives as "don" + "t", the NO list's " don't " never matches, and what is
left of "don't send it" is " send it " - which is in the YES list.

WHICH DIRECTION TO FAIL IN
--------------------------
Not symmetric, and the asymmetry is the design. A false NO costs a sentence:
the action does not happen and he says it again. A false YES sends the email,
deletes the file, or posts to the channel - and "as long as I am showing any
kind of agreement, it should confirm" was never a request to treat a refusal
as agreement. So: when a sentence contains both a negation and an agreement,
the negation wins, and an unreadable answer returns None rather than guessing.

WHAT MUST NOT REGRESS
---------------------
The reason the YES list is a substring search at all is in the docstring on
the function: "Of course." was answered to a real confirmation, was not in the
old exact-match list, and became a timeout. Broad agreement still has to work.
And "why not" is an AGREEMENT, not a negation, which is exactly the kind of
idiom a blunt "any 'not' means no" rule destroys.
"""
from __future__ import annotations

import pytest

from jarvis.app import Jalen

parse = Jalen._parse_yes_no


# ---------------------------------------------------------------------------
# THE FALSE-YES CLASS. Every one of these executed a RED action.
# ---------------------------------------------------------------------------
REFUSALS = [
    # The reproduced set, verbatim.
    "of course not",
    "I'm not sure",
    "not right now",
    "that's not right",
    "certainly not",
    "not okay with that",
    # The same shape, other agreement words.
    "definitely not",
    "absolutely not",
    "not good",
    "that is not correct",
    "I'm not okay with that",
    "not yet, hold on",
    # Contractions the NO list never listed.
    "I won't",
    "that won't work",
    "I can't approve that",
    "I wouldn't",
    "you shouldn't",
    "that isn't right",
    "I didn't say that",
    # The plain ones, which already worked and must keep working.
    "no",
    "nope",
    "nah",
    "don't",
    "do not",
    "no thanks",
    "never mind",
    "cancel",
    "stop",
    "abort",
    "not yet",
    "negative",
    "forget it",
    "leave it",
    "skip it",
]


@pytest.mark.parametrize("text", REFUSALS)
def test_a_refusal_is_never_read_as_agreement(text):
    assert parse(text) is not True, (
        f"{text!r} was read as YES - a RED action he just refused would run"
    )


@pytest.mark.parametrize("text", [
    "of course not", "I'm not sure", "not right now", "that's not right",
    "certainly not", "not okay with that", "I won't", "don't", "no",
])
def test_a_clear_refusal_is_answered_immediately_rather_than_timing_out(text):
    """
    None is safe - confirm() times out and treats it as no - but it costs him
    twenty seconds of a blocked assistant first. A recognisable refusal should
    come back as a refusal.
    """
    assert parse(text) is False, f"{text!r} should be an immediate no"


# ---------------------------------------------------------------------------
# THE CURLY APOSTROPHE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "don’t",
    "don’t send it",
    "I don’t want that",
    "no, don’t",
    "that won’t work",
    "I can’t approve that",
])
def test_the_apostrophe_speech_recognition_actually_emits(text):
    """
    U+2019, not U+0027. "don't send it" became " send it " - which is in the
    YES list - so the refusal sent it.
    """
    assert parse(text) is False, f"{text!r} was not read as a refusal"


# ---------------------------------------------------------------------------
# A PARTIAL REFUSAL IS NOT A BLANKET YES
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "yes, but not the second one",
    "yes to the first, no to the second",
    "sure, but not that one",
    "okay but not now",
])
def test_half_an_agreement_does_not_approve_the_whole_thing(text):
    """
    confirm() is a single yes/no about a single action. A sentence that
    agrees to part of something is not an answer to it, and reading it as
    one performs the half he excluded.
    """
    assert parse(text) is not True, f"{text!r} approved everything"


# ---------------------------------------------------------------------------
# AGREEMENT STILL HAS TO WORK. This is why the YES list is a substring
# search, and the regression this fix could cause.
# ---------------------------------------------------------------------------
AGREEMENTS = [
    # THE ORIGINAL BUG this function was written for: "Of course." was not in
    # the old exact-match list and an unmistakable yes became a timeout.
    "of course",
    "Of course.",
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
    "correct", "that's right", "exactly", "affirmative",
    "certainly", "definitely", "absolutely",
    "please do", "do it", "go ahead", "go for it",
    "send it", "confirm", "confirmed",
    "carry on", "continue", "proceed",
    "fine", "alright", "all right",
    "i agree", "agreed", "approve", "approved",
    "sounds good", "perfect", "great",
    "let's do it", "make it happen", "yes please",
    "yes, do it",
    "I said yes",
    # AN IDIOM THAT CONTAINS A NEGATION AND MEANS YES. The one a blunt
    # "any 'not' is a no" rule destroys, and it is in the YES list today.
    "why not",
]


# ---------------------------------------------------------------------------
# THE KNOWN FALSE NOs, written down rather than left to be rediscovered.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "yes, no problem",
    "sure, no worries",
    "yes, no rush",
    "no problem",
    "no worries",
])
def test_agreement_wrapped_around_a_negative_word_is_refused(text):
    """
    NOT A YES, and deliberately so. A first version of this fix neutralised
    eight "negative-sounding but affirmative" idioms before the NO list ran.
    An independent review measured the result against HEAD: seven of the
    eight flipped False (or None) to True, including "no rush", "no doubt"
    and "no objection" - none of which is permission to send an email, on
    the last gate before a RED action runs.

    So they went back to refusing. The colloquial reading of "no problem" as
    agreement is real and this loses it; that costs him a sentence, and the
    alternative cost six other phrases becoming approvals. "why not" is the
    single exception, because it was already True before any of this and
    taking it away would have been its own regression.
    """
    assert parse(text) is not True, (
        f"{text!r} approves a RED action on the strength of an idiom"
    )


@pytest.mark.parametrize("text", AGREEMENTS)
def test_agreement_in_any_phrasing_still_confirms(text):
    assert parse(text) is True, (
        f"{text!r} is agreement - reading it as anything else is the "
        "timeout bug the substring search was written to fix"
    )


# ---------------------------------------------------------------------------
# NOT AN ANSWER. None means "he said something, but not yes or no", and
# confirm() lets it time out rather than guessing.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "", "   ", "maybe", "what?", "hang on let me check", "open chrome",
    "what's the weather", "asdf qwerty",
])
def test_something_that_is_not_an_answer_is_reported_as_not_an_answer(text):
    assert parse(text) is None, f"{text!r} was read as an answer"


# ---------------------------------------------------------------------------
# ORDER. The reason the NO list is searched first is in the function's own
# docstring: "no, don't send it" CONTAINS "send it".
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "no, don't send it",
    "no, go ahead and cancel that",
    "don't confirm",
    "cancel, do not proceed",
])
def test_a_refusal_that_names_the_action_is_still_a_refusal(text):
    assert parse(text) is False, f"{text!r} performed the action it refused"
