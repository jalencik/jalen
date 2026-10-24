"""
"... Confirm?" - and he answered 👍.

On a phone the answer to a question is as often an emoji as a word. The yes/no
parser scrubs everything that is not a letter or a digit, so a bare 👍 became
an empty string: "not an answer". Jalen asked "was that a yes or a no?", and a
second 👍 let the question run out and cancel. Reproduced before the fix.

Only a message that is NOTHING BUT the emoji counts. "👍 but send it to Rodion
instead" still goes through the words, where it is a correction, and a mix of
yes and no is neither. Negatives first, as everywhere in that function.
"""
from __future__ import annotations

import pytest

from jalen.app import Jalen

parse = Jalen._parse_yes_no

# The emoji variation selector, U+FE0F: phones send it after many emoji, and
# it is invisible, so it is spelled out rather than typed into the strings.
VS16 = chr(0xFE0F)


@pytest.mark.parametrize("text", ["👍", "👍🏽", "👍🏻", "👍👍", "👍 👍", "👌", "✅", "✔", chr(0x2714) + VS16, "🆗",
                                  " 👍 ", "✅✅"])
def test_a_thumbs_up_on_its_own_is_yes(text):
    assert parse(text) is True, text


@pytest.mark.parametrize("text", ["👎", "👎🏿", "❌", "✖", chr(0x2716) + VS16, "🛑", "🚫", "✋", "✋🏼", "👎👎"])
def test_a_thumbs_down_on_its_own_is_no(text):
    assert parse(text) is False, text


@pytest.mark.parametrize("text", ["👍👎", "✅❌", "👍 ✋"])
def test_a_mix_of_yes_and_no_is_neither(text):
    assert parse(text) is None, text


@pytest.mark.parametrize("text,expected", [
    ("👍 but send it to Rodion instead", None),     # a correction, read from the words
    ("ok 👍", True),                                # the words decide, as before
    ("no 👍", False),
    ("👍 not yet", False),
])
def test_emoji_beside_words_leave_the_words_in_charge(text, expected):
    assert parse(text) is expected, text


@pytest.mark.parametrize("text", ["😂", "🙂", "🤔", chr(0x2764) + VS16, "🔥"])
def test_any_other_emoji_is_still_not_an_answer(text):
    assert parse(text) is None, text


def test_nothing_is_still_not_an_answer():
    assert parse("") is None and parse("   ") is None
