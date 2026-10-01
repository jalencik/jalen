"""
What an independent review of the premium-emoji and sticker work found, and
the decision that a sticker is only sent when he asks for one.

Each test below failed against the code as first written (f62426d) before the
fix; the finding it belongs to is named in the section heading.

1. "One emoji" was not one emoji. The check that lets find_premium_emoji skip
   the untrusted fence accepted a run of invisible Unicode TAG characters
   after any emoji (an invisible copy of ASCII text) and runs of circled
   letters or digits joined with zero-width joiners, so text a pack author or
   a stranger chose reached the brain unfenced, on a turn that stayed marked
   as his own words.
2. A sticker was sent after every channel post unless he said not to. He
   decided: a sticker only when he asks for one. Premium emoji inside the
   post text stay automatic.
3. A premium emoji drawn as a different picture was offered for the emoji he
   asked for, because the pack author had also filed it under that emoji.
4. A search that ran out of time was reported as "may already be there", so a
   safe retry was refused; and one dead emoji set made every lookup re-read all
   of the others.
5. Custom emoji with text stored where the emoji should be were left out of
   every answer without a word.

No network: tests/_telegram_fakes.py.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis import taint
from jarvis.config import CONFIG
from jarvis.safety import SafetyEngine
from jarvis.tools import messaging, stickers
from tests._telegram_fakes import (
    FakeAccount, FakeChannel, FakeSent, emoji_doc, set_info, sticker_doc,
)
from telethon import types

ROCKET, FIRE, UFO = "\U0001f680", "\U0001f525", "\U0001f6f8"
CLAP, SMILE = "\U0001f44f", "\U0001f600"
ZWJ = "\u200d"
CHANNEL = "AI engineering & Machine learning"


def hidden(text: str) -> str:
    """ASCII text written in Unicode tag characters: invisible on screen."""
    return "".join(chr(0xE0000 + ord(c)) for c in text)


def circled(word: str) -> str:
    """Circled capital letters, joined with zero-width joiners."""
    return ZWJ.join(chr(0x24B6 + ord(c) - ord("A")) for c in word.upper())


def circled_digits(count: int) -> str:
    return ZWJ.join(chr(0x2460 + n) for n in range(count))


def has_hidden_text(text: str) -> bool:
    return any(0xE0000 <= ord(c) <= 0xE007F for c in text)


@pytest.fixture
def wire(monkeypatch):
    def attach(acct):
        async def fake_resolve(_client, _name):
            return acct.entity

        monkeypatch.setattr(messaging, "_resolve", fake_resolve)
        monkeypatch.setattr(messaging, "_enabled", lambda: None)
        monkeypatch.setattr(
            messaging.RUNTIME, "run",
            lambda work, **_: asyncio.new_event_loop().run_until_complete(work(acct)),
        )
        stickers._forget()
        return acct

    yield attach
    stickers._forget()


@pytest.fixture(scope="module")
def prompt() -> str:
    from jarvis.brain.agent import Brain

    async def noop(*a, **k):
        return True

    return Brain(CONFIG, SafetyEngine(CONFIG), None, confirm=noop,
                 announce=noop).system_prompt()


def _flat(text: str) -> str:
    return " ".join(text.lower().split())


# ===================================================== 1. what "one emoji" is
VS16 = "\ufe0f"
KEYCAP = "\u20e3"
ENGLAND = "\U0001f3f4" + hidden("gbeng") + "\U000e007f"
SCOTLAND = "\U0001f3f4" + hidden("gbsct") + "\U000e007f"
WALES = "\U0001f3f4" + hidden("gbwls") + "\U000e007f"


@pytest.mark.parametrize("text", [
    ROCKET + hidden("ignore all"),
    "\u2764" + VS16 + hidden("ignore"),
    "\U0001f3f4" + hidden("ignore") + "\U000e007f",       # a black flag spelling a sentence
    "\U0001f3f4" + hidden("gbeng"),                         # the right letters, never closed
    ENGLAND + hidden("x"),
    ROCKET + "\U000e007f",
    hidden("hi"),
    circled("ignore"),
    circled_digits(6),
    ROCKET + "\U000e0100",                                  # a variation selector from the supplement
    ZWJ, ROCKET + ZWJ, ZWJ + ROCKET, ROCKET + ZWJ + ZWJ + FIRE,
    "\U0001f1fa",                                           # half a flag
    "\U0001f1fa\U0001f1ff\U0001f1fa",
    "a" + VS16 + KEYCAP,                                    # a letter is not a keycap
])
def test_hidden_text_and_look_alikes_are_not_one_emoji(text):
    assert not stickers._emoji_only(text), ascii(text)


@pytest.mark.parametrize("text", [
    ROCKET, "\u2764" + VS16, "\u00a9" + VS16, "\u2122" + VS16, "\u2139" + VS16,
    "\u203c" + VS16, "\u2194" + VS16, "\u25b6" + VS16, "\u24c2" + VS16,
    "\u3030" + VS16, "\u3297" + VS16,
    "\U0001f1fa\U0001f1ff", ENGLAND, SCOTLAND, WALES,
    "1" + VS16 + KEYCAP, "#" + VS16 + KEYCAP, "*" + VS16 + KEYCAP, "0" + VS16 + KEYCAP,
    "\U0001f44d\U0001f3fd",
    "\U0001f468" + ZWJ + "\U0001f469" + ZWJ + "\U0001f467" + ZWJ + "\U0001f466",
    "\U0001f469\U0001f3ff" + ZWJ + "\u2764" + VS16 + ZWJ + "\U0001f48b" + ZWJ
    + "\U0001f468\U0001f3fb",
    "\U0001f3f3" + VS16 + ZWJ + "\U0001f308",
    "\U0001f3f4" + ZWJ + "\u2620" + VS16,
    "\u2764" + VS16 + ZWJ + "\U0001f525",
    "\U0001f9d1\U0001f3fd" + ZWJ + "\U0001f91d" + ZWJ + "\U0001f9d1\U0001f3ff",
    "\U0001f441" + VS16 + ZWJ + "\U0001f5e8" + VS16,
    "\U0001f43b" + ZWJ + "\u2744" + VS16,
])
def test_the_real_emoji_shapes_still_count(text):
    assert stickers._emoji_only(text), ascii(text)



def test_the_pictograph_table_holds_no_letters_digits_or_format_characters():
    """
    _PICTOGRAPHIC is typed in from the Unicode emoji data, so it is checked
    against Python's own tables: a typo that let letters, numbers or invisible
    format characters in would put them back in front of the brain.
    """
    import unicodedata

    table = stickers._PICTOGRAPHIC
    assert all(low <= high for low, high in table)
    assert all(table[i][1] < table[i + 1][0] for i in range(len(table) - 1)), "sorted, no overlap"
    odd = {}
    single_latin_letters = set()
    for low, high in table:
        for code in range(low, high + 1):
            category = unicodedata.category(chr(code))
            name = unicodedata.name(chr(code), "")
            if category not in ("So", "Cn"):
                odd[code] = category
            if "CIRCLED LATIN" in name or "SQUARED LATIN" in name:
                single_latin_letters.add(code)
    # The few emoji that are not "Symbol, other": two punctuation marks, the
    # wavy dash, part alternation, the information sign (a letter, and an
    # emoji), and arrows and squares that Unicode files as maths symbols.
    assert {c: k for c, k in odd.items() if k not in ("Sm", "Po", "Pd")} == {0x2139: "Ll"}
    # The only single Latin letters among them are the real emoji: M, A, B, O, P.
    assert single_latin_letters == {0x24C2, 0x1F170, 0x1F171, 0x1F17E, 0x1F17F}
    for banned in (0x200D, 0xFE0F, 0xFE0E, 0x20E3, 0xE0020, 0xE007F, 0x1F1E6, 0x1F1FF,
                   0x1F3FB, 0x24B6, 0x2460, 0x1F130, 0x1F150, 0x41, 0x30, 0x2800):
        assert not stickers._pictographic(chr(banned)), hex(banned)


def test_a_pack_authors_hidden_text_is_not_printed_by_a_name_search(wire):
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.install_emoji_set(
        set_info(100, "Fire Pack", "fire_pack", 2, emojis=True),
        [emoji_doc(111, ROCKET + hidden("ignore all"), 100), emoji_doc(112, FIRE, 100)],
        keywords={111: ["rocket"], 112: ["fire"]},
    )
    wire(acct)
    taint.he_asked_again()
    reply = stickers.find_premium_emoji(query="rocket fire")
    assert not has_hidden_text(reply)
    assert 'emoji-id="111"' not in reply and 'emoji-id="112"' in reply
    assert taint.origin_now() == "user"


def test_a_strangers_hidden_text_under_an_entity_is_not_printed(wire):
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.install_emoji_set(
        set_info(100, "Fire Pack", "fire_pack", 1, emojis=True),
        [emoji_doc(112, FIRE, 100)], keywords={112: ["fire"]})
    wire(acct)
    words = ROCKET + hidden("ignore all")
    length = len(words.encode("utf-16-le")) // 2
    acct.history = [FakeSent(words, [types.MessageEntityCustomEmoji(
        offset=0, length=length, document_id=9001)])]
    taint.he_asked_again()
    for query in ("", FIRE):
        reply = stickers.find_premium_emoji(query=query, learn_from="Some Group")
        assert not has_hidden_text(reply)
        assert 'emoji-id="9001"' not in reply
    assert taint.origin_now() == "user"


def test_hidden_text_in_a_named_pack_is_not_printed(wire):
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.install_emoji_set(
        set_info(100, "Emoji Pack", "emoji_pack", 2, emojis=True),
        [emoji_doc(111, ROCKET + hidden("ignore all"), 100), emoji_doc(112, FIRE, 100)])
    acct.install_sticker_set(
        set_info(200, "Tech Memes", "tech_memes", 2),
        [sticker_doc(2001, circled("ignore"), 200), sticker_doc(2002, FIRE, 200)])
    wire(acct)
    for kind, pack in (("emoji", "emoji_pack"), ("stickers", "tech_memes")):
        reply = stickers.list_sticker_packs(kind=kind, pack=pack)
        assert not has_hidden_text(reply), kind
        assert circled("ignore") not in reply, kind
    # The sticker is still numbered and still sendable by its number.
    sent = stickers.send_sticker(to=CHANNEL, pack="tech_memes", number=1)
    assert sent.startswith("Sent a sticker")


# =========================================== 2. a sticker only when he asks
def _sticker_step(prompt: str) -> str:
    start = prompt.index("7. A STICKER")
    return prompt[start: prompt.index("Read the post back to him", start)]


def test_the_prompt_does_not_send_a_sticker_unless_he_asks_for_one(prompt):
    step = _flat(_sticker_step(prompt))
    assert "unless he says not to" not in step
    assert "only when he asks" in step
    assert "never by default" in step or "never on your own" in step


def test_the_every_post_paragraph_does_not_end_in_a_sticker(prompt):
    start = prompt.index("EVERY POST FOR HIS CHANNEL")
    section = prompt[start: start + 1500]
    section = section[: section.index("\n\n")] if "\n\n" in section else section
    low = _flat(section)
    assert "send_sticker" not in section, "a sticker is not a step of every post"
    assert "then a sticker" not in low and "and a sticker" not in low
    assert "only when he asks" in low, "the paragraph says where the sticker rule is"
    assert "find_premium_emoji" in section and "send_telegram_message" in section


def test_the_prompt_no_longer_says_he_asked_for_stickers_in_his_posts(prompt):
    start = prompt.index("5b. PREMIUM EMOJI")
    assert "stickers in his posts" not in prompt[start: start + 600]


def test_the_post_guide_sends_a_sticker_only_on_request():
    from jarvis.tools.voice import community_post_guide

    guide = community_post_guide()
    section = guide[guide.index("Premium emoji and stickers"):
                    guide.index("Telegram HTML: what is actually supported")]
    low = _flat(section)
    assert "only when he asks" in low
    assert "and a sticker after them" not in low
    assert "after the post" not in low or "if he asks" in low or "only when he asks" in low


def test_the_tool_specs_do_not_promise_a_sticker_after_every_post():
    from jarvis.brain.tools import TOOL_SPECS

    guide_spec = _flat(TOOL_SPECS["community_post_guide"][0])
    assert "a sticker after it" not in guide_spec
    send_spec = _flat(TOOL_SPECS["send_sticker"][0])
    assert "only when he asks" in send_spec
    assert "then the sticker" not in send_spec


def test_the_one_post_with_a_sticker_request_still_works_end_to_end(wire):
    """'Post it with a sticker' is the trigger; nothing about the tool changed."""
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.faved = [sticker_doc(3001, ROCKET, 300)]
    wire(acct)
    posted = messaging.send_telegram_message(to=CHANNEL, text="<b>Hello</b>")
    sticker = stickers.send_sticker(to=CHANNEL, emoji=ROCKET)
    assert posted.startswith("Sent to") and sticker.startswith("Sent a " + ROCKET + " sticker")
    assert [w[0] for w in acct.wire] == ["message", "file"]


# =============================== 3. a different picture is not the emoji asked
def test_an_emoji_filed_under_another_one_is_not_offered_for_it(wire):
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.install_emoji_set(
        set_info(100, "UFO Pack", "ufo_pack", 1, emojis=True),
        [emoji_doc(111, UFO, 100)], keywords={111: ["ufo"]})
    # The author also filed the flying saucer under the rocket, which is how
    # Telegram's own emoji search finds it. It still draws a flying saucer.
    acct.emoji_sets[100][1].packs.append(types.StickerPack(emoticon=ROCKET, documents=[111]))
    wire(acct)
    reply = stickers.find_premium_emoji(query=ROCKET)
    assert 'emoji-id="111"' not in reply
    assert "no premium version" in reply


def test_the_exact_emoji_still_wins_when_another_is_filed_under_it_too(wire):
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.install_emoji_set(
        set_info(100, "UFO Pack", "ufo_pack", 2, emojis=True),
        [emoji_doc(111, UFO, 100), emoji_doc(112, ROCKET, 100)])
    acct.emoji_sets[100][1].packs.append(types.StickerPack(emoticon=ROCKET, documents=[111]))
    wire(acct)
    reply = stickers.find_premium_emoji(query=ROCKET)
    assert f'<tg-emoji emoji-id="112">{ROCKET}</tg-emoji>' in reply
    assert 'emoji-id="111"' not in reply


# ======================== 4. a slow search, a dead set, and what was read
def _tech_memes():
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.install_sticker_set(
        set_info(200, "Tech Memes", "tech_memes", 2),
        [sticker_doc(2001, SMILE, 200), sticker_doc(2002, FIRE, 200)])
    return acct


@pytest.mark.parametrize("kwargs", [
    {"pack": "Tech Memes", "emoji": FIRE},
    {"emoji": FIRE},
])
def test_a_search_that_runs_out_of_time_has_sent_nothing_and_says_so(wire, monkeypatch, kwargs):
    """
    Looking for the sticker only reads. If Telegram does not answer while it
    is looking, nothing can have been sent, so the answer is "Nothing sent" and
    sending again is safe - not "may already be there", which sends him to the
    chat to check and makes the brain hesitate to retry.
    """
    wire(_tech_memes())

    def run(_work, **_):
        raise TimeoutError()

    monkeypatch.setattr(messaging.RUNTIME, "run", run)
    reply = stickers.send_sticker(to=CHANNEL, **kwargs)
    assert reply.startswith("Nothing sent")
    assert "may already be there" not in reply and "Not confirmed" not in reply


def test_nothing_is_sent_by_a_search_that_ran_out_of_time(wire, monkeypatch):
    """The search never sends, so nothing can reach the chat after the wait gives up."""
    acct = wire(_tech_memes())
    waited = []

    def run(work, **_):
        waited.append(work)
        raise TimeoutError()

    monkeypatch.setattr(messaging.RUNTIME, "run", run)
    stickers.send_sticker(to=CHANNEL, emoji=FIRE)
    assert len(waited) == 1, "only the search was started; the send is a second step"
    assert acct.wire == [] and acct.attempts == 0


def _three_sets():
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    for n, (char, word) in enumerate(((FIRE, "fire"), (CLAP, "clap"), (ROCKET, "rocket"))):
        acct.install_emoji_set(
            set_info(100 + n, f"Set {n}", f"set_{n}", 1, emojis=True),
            [emoji_doc(500 + n, char, 100 + n)], keywords={500 + n: [word]})
    return acct


def _reads_of(acct, set_id):
    return [r for r in acct.requests
            if type(r).__name__ == "GetStickerSetRequest" and r.stickerset.id == set_id]


def test_one_dead_set_does_not_make_every_lookup_read_the_others_again(wire):
    acct = wire(_three_sets())
    acct.broken_sets.add(101)
    for _ in range(3):
        stickers.find_premium_emoji(query=FIRE)
    assert len(_reads_of(acct, 100)) == 1 and len(_reads_of(acct, 102)) == 1
    assert len(_reads_of(acct, 101)) == 3, "the dead one is the only one tried again"


def test_a_set_that_comes_back_is_found_on_the_next_lookup(wire):
    acct = wire(_three_sets())
    acct.broken_sets.add(101)
    first = stickers.find_premium_emoji(query=CLAP)
    assert "emoji-id" not in first and "couldn't be read" in first
    acct.broken_sets.clear()
    second = stickers.find_premium_emoji(query=CLAP)
    assert 'emoji-id="501"' in second and "couldn't be read" not in second


def test_a_set_he_installs_after_a_complete_lookup_is_still_found_in_the_window(wire):
    """The whole-index cache must not hide a set that was added later."""
    acct = wire(_three_sets())
    stickers.find_premium_emoji(query=FIRE)
    before = len(acct.requests)
    stickers.find_premium_emoji(query=CLAP)
    assert len(acct.requests) == before, "a complete index is reused with no requests"


def test_packs_that_could_not_be_read_are_not_reported_as_having_no_sticker(wire):
    acct = _tech_memes()
    acct.broken_sets.add(200)
    wire(acct)
    reply = stickers.send_sticker(to=CHANNEL, emoji=FIRE)
    assert reply.startswith("Nothing sent") and acct.wire == []
    assert "couldn't be read" in reply


# ================================ 5. text where the emoji should be is counted
def test_emoji_stored_with_text_are_counted_when_something_is_not_found(wire):
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.install_emoji_set(
        set_info(100, "Letters", "letters", 3, emojis=True),
        [emoji_doc(111, "A", 100), emoji_doc(112, "7", 100), emoji_doc(113, FIRE, 100)],
        keywords={111: ["letter"], 112: ["number"], 113: ["fire"]})
    wire(acct)
    reply = stickers.find_premium_emoji(query="letter")
    assert 'emoji-id="111"' not in reply, "text is never offered as an emoji"
    assert "2 custom emoji" in reply and "stored" in reply
    # Nothing a pack author wrote is repeated to say so.
    assert "Letters" not in reply and "letters" not in reply.split("stored")[-1]


def test_nothing_is_added_when_everything_asked_for_was_found(wire):
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.install_emoji_set(
        set_info(100, "Letters", "letters", 2, emojis=True),
        [emoji_doc(111, "A", 100), emoji_doc(113, FIRE, 100)],
        keywords={113: ["fire"]})
    wire(acct)
    reply = stickers.find_premium_emoji(query="fire")
    assert 'emoji-id="113"' in reply and "custom emoji in his sets" not in reply


def test_a_named_emoji_set_says_how_many_it_left_out(wire):
    acct = FakeAccount(entity=FakeChannel(CHANNEL))
    acct.install_emoji_set(
        set_info(100, "Letters", "letters", 2, emojis=True),
        [emoji_doc(111, "A", 100), emoji_doc(113, FIRE, 100)])
    wire(acct)
    reply = stickers.list_sticker_packs(kind="emoji", pack="letters")
    assert "1 custom emoji" in reply and "stored" in reply
    assert "113" in reply and "111" not in reply


# ============================== what a sticker taken from his packs discloses
def test_a_sticker_nobody_chose_for_the_emoji_says_so(wire):
    """No favourite matched, so it is only the first one in his packs: say that."""
    acct = _tech_memes()
    wire(acct)
    reply = stickers.send_sticker(to=CHANNEL, emoji=FIRE)
    assert reply.startswith("Sent a " + FIRE + " sticker")
    assert "first one" in reply and "Name a pack" in reply

    # A favourite is his own choice, and is not described that way.
    acct.faved = [sticker_doc(3001, FIRE, 300)]
    reply = stickers.send_sticker(to=CHANNEL, emoji=FIRE)
    assert "favourite" in reply and "first one" not in reply


def test_a_search_cut_short_by_the_limit_says_so_instead_of_saying_none(wire, monkeypatch):
    acct = _tech_memes()
    acct.install_sticker_set(
        set_info(201, "Later Pack", "later_pack", 1), [sticker_doc(2101, CLAP, 201)])
    wire(acct)
    monkeypatch.setattr(stickers, "_MAX_SETS", 1)
    reply = stickers.send_sticker(to=CHANNEL, emoji=CLAP)
    assert reply.startswith("Nothing sent") and acct.wire == []
    assert "only searched the first 1 of his 2 packs" in reply
