"""
He is called Jalen, and calling him by name must not break the command.

The bug this file exists to prevent, in full, because it is subtle and it
shipped: the router's normaliser stripped a leading name so that "jarvis,
open chrome" reached the same rule as "open chrome". That name was written
literally, as the string "jarvis", in two separate places. Renaming the
assistant therefore did not rename it here — and every single name-prefixed
command silently stopped routing at once.

Silently is the important word. Nothing errored. "jalen quit" simply became
a sentence for the LLM to think about instead of a command, so it cost a
round-trip and came back as conversation, and the assistant did not quit.
The same was true of every other rule: "jalen open chrome", "jalen mute",
"jalen what time is it". The one command surface a user reaches for when
everything else has gone wrong was the surface that broke.

So these tests assert the property, not the instances: prefixing or
suffixing the name must never change which intent you get.
"""
from __future__ import annotations

import pytest

from jarvis.brain.router import NAME_ALIASES, IntentRouter
from jarvis.config import CONFIG


@pytest.fixture(scope="module")
def router() -> IntentRouter:
    return IntentRouter(CONFIG)


# A spread of real commands, one per family, taken from the rule table and
# from phrases in data/router_misses.log. These are the BARE forms; every
# test below derives the decorated forms from them.
COMMANDS = [
    "quit",
    "exit",
    "shut down",
    "restart",
    "mute",
    "be quiet",
    "unmute",
    "resume",
    "go to sleep",
    "open chrome",
    "open telegram",
    "open youtube",
    "what time is it",
    "whats the time",
    "what day is it",
    "battery",
    "screenshot",
    "lock the computer",
    "play music",
    "pause the music",
    "next track",
    "volume up",
    "what did you do today",
    "brief me",
]


def intent_of(router: IntentRouter, text: str):
    hit = router.route(text)
    return (hit.tool, tuple(sorted(hit.args.items()))) if hit else None


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize(
    "decorate",
    [
        pytest.param(lambda c, n: f"{n} {c}", id="name-first"),
        pytest.param(lambda c, n: f"{n}, {c}", id="name-comma"),
        pytest.param(lambda c, n: f"hey {n} {c}", id="hey-name"),
        pytest.param(lambda c, n: f"hey {n}, {c}", id="hey-name-comma"),
        pytest.param(lambda c, n: f"{c}, {n}", id="trailing-address"),
        pytest.param(lambda c, n: f"{c} {n}", id="trailing-bare"),
    ],
)
# The canonical spelling plus two transcription variants. Not the whole
# alias list: crossed with 24 commands and 6 decorations that is fourteen
# hundred cases proving the same property, and a suite people stop running
# protects nothing. Every alias IS checked individually, cheaply, below.
@pytest.mark.parametrize("name", ["jalen", "jaylen", "jalin"])
def test_name_never_changes_the_intent(router, command, decorate, name):
    """
    Saying his name is addressing, not instructing. Whatever "open chrome"
    does, "Jalen, open chrome" must do identically — same tool, same args.
    """
    bare = intent_of(router, command)
    assert bare is not None, f"fixture is stale: {command!r} no longer routes at all"

    decorated = intent_of(router, decorate(command, name))
    assert decorated == bare, (
        f"{decorate(command, name)!r} routed to {decorated} but "
        f"{command!r} routes to {bare} — addressing him by name changed the command"
    )


@pytest.mark.parametrize("alias", NAME_ALIASES)
def test_every_transcription_variant_reaches_the_same_command(router, alias):
    """
    Whisper does not spell an uncommon name consistently — it writes Jaylen,
    Jalin, Jaelen, Jalon — and which one it picks varies with accent. He
    raised exactly this: different people pronounce it differently. A command
    lost to a spelling the speaker cannot see or control is indistinguishable
    from the assistant ignoring them.
    """
    assert intent_of(router, f"{alias} quit") == intent_of(router, "jalen quit")


def test_the_old_name_is_gone(router):
    """
    At his explicit request: "only Jalen is acceptable, I do not wanna hear
    about Jarvis." It must not remain as a quiet fallback either — that is
    how a rename stays half-done for months.
    """
    assert "jarvis" not in NAME_ALIASES
    assert router.route("jarvis quit") is None, "the old name still commands him"


@pytest.mark.parametrize(
    "real_name",
    ["alan", "galen", "helen", "jason", "dylan"],
)
def test_similar_real_names_are_not_swallowed(router, real_name):
    """
    The variant list must not grow into ordinary names. Matching "Alan"
    would strip a real person out of a sentence — "tell Alan I'm late"
    becoming "tell I'm late" — which is a worse failure than not matching.
    """
    assert real_name not in NAME_ALIASES


@pytest.mark.parametrize(
    "phrase",
    ["quit", "jalen quit", "quit jalen", "jalen, quit", "hey jalen quit",
     "exit", "jalen exit", "shut down", "jalen shut down", "turn off"],
)
def test_every_way_of_leaving_says_see_you_boss(router, phrase):
    """
    The exact words matter to him, and the exit path has no fallback: if
    this rule misses, the only remaining way out is killing the process.
    """
    hit = router.route(phrase)
    assert hit is not None, f"{phrase!r} does not route — there is no way to quit"
    assert hit.tool == "jalen_quit"
    assert hit.reply == "See you, Boss."


@pytest.mark.parametrize("phrase", ["jalen", "hey jalen", "jalen?", "Jalen", "jaylen"])
def test_bare_name_is_answered_not_reasoned_about(router, phrase):
    """
    The endpointer closes on just the name constantly — wake fires, he draws
    breath, the utterance ends. Answering a summons is the cheapest turn
    there is and must not cost an LLM round-trip.
    """
    hit = router.route(phrase)
    assert hit is not None and hit.tool == "jalen_ack"
    assert hit.reply == "Yes, Boss?"


def test_name_alone_is_not_stripped_into_nothing(router):
    """
    Guards the obvious over-correction: making the name optional everywhere
    would let a bare "jalen" fall through the strip and match whatever rule
    happens to accept an empty string.
    """
    assert router._normalise("jalen") == "jalen"


@pytest.mark.parametrize("phrase", ["close jalen", "kill jalen", "turn off jalen"])
def test_name_as_object_of_a_complete_verb_means_quit(router, phrase):
    """"quit"/"close"/"kill" stand alone, so the name after them is the target."""
    hit = router.route(phrase)
    assert hit is not None and hit.tool == "jalen_quit"


@pytest.mark.parametrize("name", NAME_ALIASES)
def test_name_inside_a_query_is_content_and_survives(router, name):
    """
    The dangerous half of name-stripping. "search reddit for jarvis" once
    stripped to "search reddit for" and searched Reddit for the word "for".
    The name is the QUERY here and must reach the tool intact.
    """
    hit = router.route(f"search reddit for {name}")
    assert hit is not None, "the search rule stopped routing"
    assert hit.args.get("query") == name, (
        f"the query was mangled to {hit.args.get('query')!r} — "
        "his name was eaten as if it were an address"
    )


@pytest.mark.parametrize(
    "phrase",
    ["google jalen", "search for jalen", "open jalen", "play jalen", "find jalen"],
)
def test_transitive_verbs_keep_their_object(router, phrase):
    """
    A verb that cannot stand alone must not be left standing alone. Before
    the guard, "google jalen" stripped to "google" — a bare verb matching no
    rule, sent to the LLM, asking it to google nothing at all.
    """
    assert router._normalise(phrase).endswith("jalen"), (
        f"{phrase!r} normalised to {router._normalise(phrase)!r}, losing its object"
    )


def test_no_user_visible_string_still_says_jarvis(router):
    """
    Every spoken reply the router can produce is read aloud by TTS. One
    stale "Jarvis" in that set is the user hearing the old name from the
    thing he just renamed.
    """
    stale = [
        reply for _pattern, _tool, _build, reply in router._rules
        if reply and "jarvis" in reply.lower()
    ]
    assert not stale, f"router replies still say Jarvis out loud: {stale}"
