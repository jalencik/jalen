r"""
For eight seconds after a dangling word, the address gate admitted anything.

_continues_last_utterance ends in:

    return is_continuation(text) or looks_unfinished(self._last_user_text)

The second disjunct does not look at `text` AT ALL. So whenever the previous
utterance ended on a word in _UNFINISHED_TAIL -- "send it to", "open the" --
every sentence spoken in the next conversation.stitch_window_s (ships as 8)
passed the gate, whatever it said.

REPRODUCED against the real bound Jalen.should_act_on with
_last_user_text='send it to', wake_initiated=False and nothing pending. All of
these returned True at HEAD:

    'Deleting every file in that folder now.'
    'transfer the funds'
    'empty the recycle bin'
    'jalapeno poppers'
    "Julian said he'd call back"
    'asdf qwerty zxcv'
    ''                                  <- the empty string
    'Sure, I can open that for you.'    <- and this one is the problem

That last string is one of the five this repo pins in
tests/test_address_gate.py as JALEN'S OWN VOICE coming back through the
speakers -- the case the whole gate exists to refuse. So this clause is not
merely permissive, it is a live self-triggering path INSIDE the address gate,
and closing it is address-gate work rather than tidying.

The comment at app.py:1825-1828 already claims the correct behaviour:

    Narrow on purpose: it is not "anything within N seconds". Either the
    fragment OPENS like a continuation ("and also...", "to my channel") or
    what he said last ENDED like one ("...send it to"). Noise satisfies
    neither.

"Noise satisfies neither" is false today. This change is what makes it true.

THE FIX IS NOT TO DELETE THE CLAUSE. app.py:1826-1827 names "to my channel" as
the fragment it exists for, and is_continuation() does not match that -- it
matches leading conjunctions ('and also my channel' -> True) but not bare
determiners or prepositions. So looks_unfinished must WIDEN the opener test
rather than bypass it: a dangling tail admits a fragment that OPENS like the
rest of a sentence, and nothing else.

Deliberately NOT used: "a bare noun phrase when the previous text ended on a
preposition". That rejects 'to my channel' -- precisely the case the clause was
written for.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.app import Jalen, is_continuation  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402


class _Gate:
    """Mirrors tests/test_address_gate.py's stub -- the real unbound predicate
    over the six attributes it reads. If Jalen grows a seventh, BOTH stubs must
    gain it, or the real path breaks while these stay green."""

    def __init__(self, *, last_text="", stale_s=0.0):
        self._awaiting_confirmation = False
        self._awaiting_stop = False
        self._awaiting_reply = False
        self._pending_rating = None
        # Added when Jalen grew a question window; this stub's own
        # docstring says both stubs must gain any new attribute, or the
        # real path breaks while these stay green.
        self._expecting = None
        self._last_reply_text = ""
        self._last_user_text = last_text
        self._last_user_at = time.monotonic() - stale_s
        self.cfg = CONFIG
        self.kill_phrases = set(CONFIG.get_path("safety.kill_phrases"))

    should_act_on = Jalen.should_act_on
    _continues_last_utterance = Jalen._continues_last_utterance
    _expectation_open = Jalen._expectation_open
    _sounds_like_its_own_voice = Jalen._sounds_like_its_own_voice


# "send it to" ends on a word in _UNFINISHED_TAIL, so looks_unfinished is True
# and the window is open. Verified directly before writing these.
DANGLING = "send it to"


NOT_A_CONTINUATION = [
    # Destructive-sounding, to make the stakes explicit. None of these is a
    # fragment of "send it to ...".
    "Deleting every file in that folder now.",
    "transfer the funds",
    "empty the recycle bin",
    # Ordinary room speech.
    "jalapeno poppers",
    "Julian said he'd call back",
    "asdf qwerty zxcv",
    # The empty string, which reached the gate as True.
    "",
    # JALEN'S OWN VOICE. tests/test_address_gate.py pins this exact string in
    # NOT_ADDRESSED as the echo that was interrupting him mid-answer.
    "Sure, I can open that for you.",
]


@pytest.mark.parametrize("text", NOT_A_CONTINUATION)
def test_a_dangling_tail_does_not_admit_arbitrary_speech(text):
    """All True at HEAD. The last entry is the self-triggering case."""
    gate = _Gate(last_text=DANGLING, stale_s=1.0)

    assert not gate.should_act_on(text, False), (
        f"{text!r} was admitted purely because the PREVIOUS utterance ended on "
        "a dangling word -- the gate never looked at this text"
    )


def test_jalens_own_voice_is_still_refused_inside_the_window():
    """Called out separately from the parametrised case above because it is a
    different claim: not 'too permissive' but 'the gate can be made to act on
    its own output', which is the property the whole predicate exists for."""
    gate = _Gate(last_text=DANGLING, stale_s=1.0)

    for own in ("Sure, I can open that for you.",
                "I didn't catch that.",
                "You have four unread emails.",
                "I'm still on the last one, give me a second.",
                "Done. I verified the result against your requirements."):
        assert not gate.should_act_on(own, False), f"self-echo admitted: {own!r}"


REAL_CONTINUATIONS = [
    # The cases the clause exists for. app.py:1826-1827 names "to my channel"
    # explicitly, so a fix that drops it is the wrong fix.
    "my channel",
    "to my channel",
    "in the morning",
    "the notes one",
    # This one is already handled by is_continuation (leading conjunction), so
    # it must keep working through the FIRST branch, not the new one.
    "and also my channel",
]


@pytest.mark.parametrize("text", REAL_CONTINUATIONS)
def test_a_real_continuation_still_stitches(text):
    gate = _Gate(last_text=DANGLING, stale_s=1.0)

    assert gate.should_act_on(text, False), (
        f"{text!r} is the rest of {DANGLING!r} and must still be heard"
    )


def test_the_conjunction_case_goes_through_the_first_branch():
    """Pins WHICH branch carries 'and also my channel', so a later edit to the
    new opener cannot silently become the only thing keeping it alive."""
    assert is_continuation("and also my channel")
    assert not is_continuation("my channel")
    assert not is_continuation("to my channel")


def test_the_window_still_closes_on_real_time():
    """Real time.monotonic(), not a patched clock: the shipped window is 8s."""
    fresh = _Gate(last_text=DANGLING, stale_s=7.0)
    stale = _Gate(last_text=DANGLING, stale_s=9.0)

    assert fresh.should_act_on("to my channel", False), "7s is inside the window"
    assert not stale.should_act_on("to my channel", False), "9s is outside it"


def test_an_admitted_fragment_cannot_chain_the_window_open():
    """A fragment admitted through the window and itself ending on a dangling
    word must not re-open the window for arbitrary speech after it. Otherwise
    one dangling utterance grants an indefinite lease."""
    gate = _Gate(last_text=DANGLING, stale_s=1.0)

    assert gate.should_act_on("to my", False), "a dangling fragment is admitted"

    # The mic loop is what advances _last_user_text; do it as the loop would,
    # then check the NEXT arbitrary sentence is still refused.
    gate._last_user_text = "to my"
    gate._last_user_at = time.monotonic()

    assert not gate.should_act_on("Deleting every file in that folder now.", False)


def test_an_empty_last_utterance_opens_nothing():
    """The guard that was already correct -- pinned so a refactor keeps it."""
    gate = _Gate(last_text="", stale_s=0.5)

    assert not gate.should_act_on("to my channel", False)
