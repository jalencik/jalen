"""
However he says it, and however Whisper spells it, the command must work.

His words: "I am not being able to speak jalen properly and it is not
actually properly hearing that from me, man what am I going to do now? many
people speak it different could you please create 1000 versions of it...
and alongside it jarvis should work as well, Jarvis should work everywhere."

An enumerated list only covers the spellings somebody thought of, so the
name is matched by SHAPE — j + vowel + l + vowel + n. This file is the proof
that the shape is wide enough to be useful and tight enough to be safe.

The tight half matters as much as the wide half. A pattern that also
swallowed "Alan" or "Julian" would strip a real person out of a sentence and
silently send a message to the wrong human being.
"""
from __future__ import annotations

import pytest

from jalen.brain.router import IntentRouter
from jalen.config import CONFIG


@pytest.fixture(scope="module")
def router() -> IntentRouter:
    return IntentRouter(CONFIG)


# Every spelling Whisper plausibly produces for "Jalen", across accents.
JALEN_SPELLINGS = [
    "jalen", "jaylen", "jaylin", "jalin", "jaelen", "jailen", "jalon",
    "jaleen", "jalene", "jayleen", "jaylan", "jhalen", "jaylene", "jalyn",
    "jalan", "jalun", "jaylon", "jeylen", "jailin", "jayline", "jalens",
    "jelen", "jilen", "jolen", "julen", "jaalen", "jaylenn", "jallen",
]

# And for "Jarvis", which he still says constantly and must keep working.
JARVIS_SPELLINGS = [
    "jarvis", "jarvus", "jervis", "jarvez", "jaros", "jarvers",
    "jarves", "jorvis", "jarwis", "jarris", "jaris",
]

ALL_SPELLINGS = JALEN_SPELLINGS + JARVIS_SPELLINGS


@pytest.mark.parametrize("spelling", ALL_SPELLINGS)
def test_the_name_is_stripped_before_a_command(router, spelling):
    """"<name> quit" must reach the quit rule, not the LLM."""
    hit = router.route(f"{spelling} quit")
    assert hit is not None, f"{spelling!r} is not recognised as his name"
    assert hit.tool == "jalen_quit"


@pytest.mark.parametrize("spelling", ALL_SPELLINGS)
def test_the_name_works_with_every_lead_in(router, spelling):
    """
    "Hey", "Hi" and "OK" all precede it in real speech, and the endpointer
    punctuates with full stops — "Jalen. Hey Jalen. Quit." is a real
    transcript from his session that used to match nothing at all.
    """
    for phrase in (
        f"hey {spelling} quit",
        f"hi {spelling} quit",
        f"ok {spelling}, quit",
        f"{spelling}. quit.",
        f"{spelling}! quit",
    ):
        hit = router.route(phrase)
        assert hit is not None and hit.tool == "jalen_quit", f"{phrase!r} missed"


@pytest.mark.parametrize("spelling", ALL_SPELLINGS)
def test_the_bare_name_is_still_answered(router, spelling):
    """
    Calling his name and nothing else is a summons. It broke once already:
    the "?" in "Jalen?" counted as a separator and the whole utterance was
    stripped to nothing.
    """
    for phrase in (spelling, f"{spelling}?", f"hey {spelling}"):
        hit = router.route(phrase)
        assert hit is not None and hit.tool == "jalen_ack", f"{phrase!r} missed"


# --------------------------------------------------------------- the guard
# Real words and real names that must NEVER be treated as his name. A false
# positive here is worse than a miss: it deletes a word out of the middle of
# a real instruction.
NOT_HIS_NAME = [
    "alan", "galen", "helen", "ellen", "allen", "alien",
    "julian", "jolene", "jason", "james", "jack", "john",
    "salon", "talon", "melon", "felon", "gallon", "golden",
    "silent", "solution", "million", "italian", "javelin",
]


@pytest.mark.parametrize("word", NOT_HIS_NAME)
def test_ordinary_words_are_not_mistaken_for_his_name(word, router):
    """
    "tell Alan I'm late" must not become "tell I'm late" — that sends a
    message about being late to whoever Jalen guesses at instead.
    """
    normalised = router._normalise(f"{word} quit")
    assert normalised.startswith(word), (
        f"{word!r} was stripped as if it were his name (left {normalised!r})"
    )


@pytest.mark.parametrize("name", ["Alan", "Julian", "Helen", "Jolene"])
def test_a_real_person_survives_a_message_command(router, name):
    """The end-to-end version: the recipient must reach the tool intact."""
    hit = router.route(f"telegram {name} saying I'm running late")
    assert hit is not None and hit.tool == "send_telegram_message"
    assert hit.args["to"].lower() == name.lower(), (
        f"the recipient became {hit.args['to']!r} — the message would go to the wrong person"
    )


# ------------------------------------------------------- heard, not spoken
def test_the_old_name_is_understood_but_never_said(router):
    """
    Both halves of what he asked, which are not in conflict: "Jarvis should
    work everywhere" (input) and "I do not wanna hear about Jarvis" (output).
    """
    assert router.route("jarvis quit").tool == "jalen_quit", "the old name stopped working"

    stale = [
        reply for _p, _t, _b, reply in router._rules
        if reply and "jarvis" in reply.lower()
    ]
    assert not stale, f"a reply still says the old name out loud: {stale}"

    from jalen.config import CONFIG as cfg

    assert "jarvis" not in str(cfg.get_path("identity.name", "")).lower()
    assert "jarvis" not in str(cfg.get_path("persona.style", "")).lower()


@pytest.mark.parametrize(
    "phrase, tool",
    [
        ("hey jarvis what time is it", "get_time"),
        ("jarvis, open chrome", "open_target"),
        ("hey jaylen mute", "jalen_mute"),
        ("jalon, screenshot", "screenshot"),
        ("jervis restart", "jalen_restart"),
    ],
)
def test_mixed_names_reach_the_same_commands(router, phrase, tool):
    hit = router.route(phrase)
    assert hit is not None and hit.tool == tool, f"{phrase!r} -> {hit.tool if hit else None}"


# ===========================================================================
# STRESS. His words: "many people give udareniya... not to a in jalen, but
# they give it to e".
#
# English speakers say JA-len. He, and most Russian and Uzbek speakers, say
# ja-LEN. That is not a small difference for a transcriber: under
# second-syllable stress the FIRST vowel reduces to a schwa and Whisper
# writes a schwa as almost anything, while the stressed second vowel comes
# out long. Both halves move at once, which is why a list of spellings never
# kept up and the pattern is a shape.
# ===========================================================================
STRESS_ON_E = [
    # First vowel reduced to a schwa, written every way Whisper writes one.
    "jalen", "jelen", "jilen", "jolen", "julen", "jylen",
    "jellen", "jullen", "jallen",
    # Second vowel lengthened because it carries the stress.
    "jaleen", "jalene", "jaline", "jaleyn", "jaleyne", "jalein",
    "jeleen", "juleen", "jalean",
    # Both at once.
    "jeleyn", "juleyne", "jilene",
]

# Zhe / Dzhe. He writes and speaks Russian and Uzbek, and a J at the start of
# a name is routinely transliterated dzh- or zh-. Whisper produces these when
# the speaker voices the J the Slavic way.
CYRILLIC_J = ["dzhalen", "dzalen", "zhalen", "jhalen", "dzhaleen", "zhalene"]

# A final nasal in an unstressed syllable is heard as n, m or ng.
FINAL_NASAL = ["jalem", "jaleng", "jalenn", "jalenng"]


@pytest.mark.parametrize("spelling", STRESS_ON_E + CYRILLIC_J + FINAL_NASAL)
def test_second_syllable_stress_still_reaches_the_command(router, spelling):
    """The exact thing he reported. ja-LEN must work as well as JA-len."""
    hit = router.route(f"{spelling}, open chrome")
    assert hit is not None, f"{spelling!r} did not route at all"
    assert hit.tool == "open_target", f"{spelling!r} -> {hit.tool}"


@pytest.mark.parametrize("spelling", STRESS_ON_E + CYRILLIC_J + FINAL_NASAL)
def test_second_syllable_stress_can_quit(router, spelling):
    """
    Quitting is the one command that must never fail to match. If "quit" does
    not work, the only remaining exit is killing the process by hand.
    """
    for phrase in (f"hey {spelling}, quit", f"{spelling} quit", f"quit {spelling}"):
        hit = router.route(phrase)
        assert hit is not None, f"{phrase!r} did not route"
        assert hit.tool == "jalen_quit", f"{phrase!r} -> {hit.tool}"


@pytest.mark.parametrize("spelling", STRESS_ON_E + CYRILLIC_J)
def test_second_syllable_stress_answers_a_bare_summons(router, spelling):
    hit = router.route(spelling)
    assert hit is not None and hit.tool == "jalen_ack", f"{spelling!r} -> {hit}"


# ===========================================================================
# THE FULL CROSS-PRODUCT: every self-command, every lead-in, several
# spellings. The "test every possible keyword or quit word" sweep.
#
# Self-commands get the sweep rather than every tool because they are the
# ones with no fallback: a mis-routed "open chrome" reaches the brain and
# still works, a mis-routed "quit" leaves an assistant you cannot stop.
# ===========================================================================
SELF_COMMANDS = [
    ("quit", "jalen_quit"), ("exit", "jalen_quit"), ("shutdown", "jalen_quit"),
    ("shut down", "jalen_quit"), ("close", "jalen_quit"), ("kill", "jalen_quit"),
    ("turn off", "jalen_quit"),
    ("mute", "jalen_mute"), ("be quiet", "jalen_mute"), ("shut up", "jalen_mute"),
    ("silence", "jalen_mute"),
    ("unmute", "jalen_unmute"), ("speak", "jalen_unmute"),
    ("go to sleep", "jalen_sleep"), ("sleep", "jalen_sleep"),
    ("stand by", "jalen_sleep"), ("stop listening", "jalen_sleep"),
    ("pause", "jalen_pause"), ("hold on", "jalen_pause"),
    ("take a break", "jalen_pause"),
    ("resume", "jalen_resume"), ("carry on", "jalen_resume"),
    ("start listening", "jalen_resume"), ("wake up", "jalen_resume"),
    ("restart", "jalen_restart"), ("reboot", "jalen_restart"),
    ("reload", "jalen_restart"),
]

LEAD_INS = ["", "hey ", "hi ", "ok ", "okay "]

# A representative slice rather than all of them: the full product would be
# thousands of cases and the shapes are what differ, not the count.
SWEEP_SPELLINGS = ["jalen", "jaleen", "jelen", "dzhalen", "jaylen", "jarvis", "jaris"]


@pytest.mark.parametrize("command, tool", SELF_COMMANDS)
@pytest.mark.parametrize("lead", LEAD_INS)
def test_every_self_command_survives_being_addressed(router, lead, command, tool):
    """
    "<lead-in> <name>, <command>" - the shape people actually speak, for
    every self-command there is.
    """
    for spelling in SWEEP_SPELLINGS:
        phrase = f"{lead}{spelling}, {command}"
        hit = router.route(phrase)
        assert hit is not None, f"{phrase!r} did not route"
        assert hit.tool == tool, f"{phrase!r} -> {hit.tool}, expected {tool}"


@pytest.mark.parametrize("command, tool", SELF_COMMANDS)
def test_every_self_command_works_with_a_trailing_address(router, command, tool):
    """"quit, Jalen" - the name after the command, which is just as natural."""
    for spelling in ("jalen", "jaleen", "dzhalen", "jarvis"):
        phrase = f"{command}, {spelling}"
        hit = router.route(phrase)
        assert hit is not None, f"{phrase!r} did not route"
        assert hit.tool == tool, f"{phrase!r} -> {hit.tool}, expected {tool}"


@pytest.mark.parametrize("command, tool", SELF_COMMANDS)
def test_every_self_command_works_bare(router, command, tool):
    """
    No name at all. Inside the follow-up window, or right after the wake
    word, this is what arrives.
    """
    hit = router.route(command)
    assert hit is not None, f"bare {command!r} did not route"
    assert hit.tool == tool, f"bare {command!r} -> {hit.tool}"


@pytest.mark.parametrize("spelling", ["jalen", "jaleen", "dzhalen", "jarvis"])
def test_speech_recognition_full_stops_do_not_break_a_command(router, spelling):
    """
    STT punctuates with full stops, not commas. "Jalen. Quit." is what
    arrives, and it used to strip nothing and match nothing.
    """
    for phrase in (f"{spelling}. quit.", f"hey {spelling}. quit",
                   f"{spelling}! quit", f"{spelling}? quit"):
        hit = router.route(phrase)
        assert hit is not None, f"{phrase!r} did not route"
        assert hit.tool == "jalen_quit", f"{phrase!r} -> {hit.tool}"


@pytest.mark.parametrize("spelling", ["jalen", "jaleen", "dzhalen"])
def test_the_name_said_twice_still_works(router, spelling):
    """
    "Jalen. Hey Jalen. Quit." - real speech, and stripping only once leaves
    a leading "hey jalen." that matches nothing.
    """
    phrase = f"{spelling}. hey {spelling}. quit"
    hit = router.route(phrase)
    assert hit is not None and hit.tool == "jalen_quit", f"{phrase!r} -> {hit}"


# ===========================================================================
# The tight half. Widening the shape must not have swallowed anyone.
# ===========================================================================
# From the real audit log. These are OTHER PEOPLE, and several are HIS OWN
# NAME being dictated into a message - "write my name is Jaluddin to SAT
# Talk". Matching any of them would strip a real name out of a message going
# to a real person.
REAL_PEOPLE_FROM_THE_LOG = [
    "jalud", "jaluddin", "jaloliddin", "jalol", "jalartan",
    "jari", "jarry", "yagwan", "jaguan",
]


@pytest.mark.parametrize("word", REAL_PEOPLE_FROM_THE_LOG)
def test_the_wider_shape_did_not_swallow_a_real_person(word):
    """
    His own name is Jaloliddin, and Whisper writes it jalud / jalartan /
    jaloliddin. It opens exactly like the assistant's name. Stripping it
    would silently rewrite the message he is sending.
    """
    import re

    from jalen.brain.router import _NAME

    assert not re.match("^(?:" + _NAME + ")$", word, re.I), (
        f"{word!r} is now being treated as the assistant's name"
    )


def test_his_own_name_survives_a_message(router):
    """The end-to-end version of the above, through the real router."""
    hit = router.route("telegram sat talk saying my name is Jaluddin")
    if hit is not None and "text" in (hit.args or {}):
        assert "jaluddin" in hit.args["text"].lower(), (
            f"his own name was stripped from the message: {hit.args['text']!r}"
        )
