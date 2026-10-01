"""
"Ed" was a pre-approved destination, and so was "a".

WHAT PRE-APPROVAL IS FOR
------------------------
He asked for posts to his Saved Messages and to his community channel to go
out "without asking me, without taking my permission". So those two
destinations skip the spoken "Confirm?" that every other send gets, because
every other chat send_telegram_message can reach is another human being.

THE HOLE
--------
safety.py matched the destination by substring IN BOTH DIRECTIONS:

    if target == allowed or allowed in target or target in allowed

with allowed being "saved messages" and "ai engineering and machine
learning". Reproduced by an independent audit with the real CONFIG, every one
of these was GREEN - no confirmation - with origin=user:

    "Ed"  "Ai"  "Mac"  "Sa"  "Mes"  "Eng"  "a"  "Sage"
    "AI engineering & Machine learning chat"
    "Saved Messages backup group"

Any contact whose name is a fragment of either string, and any group whose
title merely CONTAINS one. Then the Telegram resolver picks the chat by its
OWN rule - exact title, or exactly one partial match - so the gate approved
a WORD and the resolver sent to an ENTITY, and the two were not the same.

THE RULE NOW
------------
Approve exactly what the resolver will send to:

  - his own Saved Messages: exactly the spellings messaging._resolve turns
    into get_me() - me, myself, saved messages, saved, my notes
  - a configured chat: its exact title, case and punctuation aside

and nothing that merely resembles either.

AND ONE THING THIS MAKES SAFE TO ALLOW
--------------------------------------
While Jalen is tainted - it has just read an email, a web page, a chat -
every RED or AMBER action is refused outright. That was right for other
people and too much for his own Saved Messages: "read Andres's email and put
a summary in my Saved Messages" was refused inside one turn, and the log has
it (2026-08-26T14:32:27, a BLACK on his own post). Saved Messages is visible
to nobody but him. The worst injected text can do there is write into his own
notebook; it cannot send anything anywhere else. So sends to his own Saved
Messages go through under taint - if, and only if, he pre-approved Saved
Messages - and the channel, which his community reads, stays refused.
"""
from __future__ import annotations

import asyncio
import copy

import pytest

from jarvis.config import CONFIG
from jarvis.safety import SELF_CHAT_ALIASES, SafetyEngine, Tier

CHANNEL = "AI engineering & Machine learning"


@pytest.fixture
def engine():
    return SafetyEngine(CONFIG)


def _tier(engine, to, tool="send_telegram_message", origin="user", named_by_him=""):
    args = {"to": to, "text": "a post"} if tool != "send_posts" else {"to": to, "posts": ["x"]}
    if tool == "send_telegram_file":
        args = {"to": to, "file": "C:/Users/user/Desktop/report.pdf"}
    return engine.classify(tool, args, origin=origin, named_by_him=named_by_him).tier


# ---------------------------------------------------------------------------
# THE HOLE, every spelling the audit reproduced
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("to", [
    "Ed", "Ai", "Mac", "Sa", "Mes", "Eng", "a", "Sage",
    "AI engineering & Machine learning chat",
    "Saved Messages backup group",
    "Machine learning",
    "engineering",
])
def test_a_fragment_or_a_longer_name_is_not_pre_approved(engine, to):
    assert _tier(engine, to) is Tier.RED, (
        f"{to!r} would be sent to with no confirmation - it merely resembles "
        "a pre-approved destination"
    )


@pytest.mark.parametrize("to", [
    "Saved Messages", "saved messages", "Saved Messages ",
    "me", "Me", "myself", "saved", "my notes",
    CHANNEL, "ai engineering & machine learning",
    "AI Engineering &amp; Machine Learning",
])
def test_what_he_pre_approved_still_goes_without_asking(engine, to):
    # Without a QUESTION. His channel is announced first - AMBER, his decision of
    # 2026-10-01 (tests/test_channel_posts_are_read_aloud_first.py); his own notebook
    # reaches nobody and stays GREEN.
    channel = "machine learning" in to.lower()
    assert _tier(engine, to) is (Tier.AMBER if channel else Tier.GREEN), to


@pytest.mark.parametrize("to", ["Uluhbek", "Rodion", "Mom", "@durov", "+998901234567"])
def test_other_people_still_need_a_yes(engine, to):
    assert _tier(engine, to) is Tier.RED


# The same rule, for a voice message: exactly what the resolver sends to, and
# nothing that merely resembles it.
@pytest.mark.parametrize("to", [
    "Ed", "Ai", "Mac", "Sa", "Mes", "Eng", "a", "Sage",
    "AI engineering & Machine learning chat",
    "Saved Messages backup group",
    "Machine learning", "engineering",
])
def test_a_fragment_is_not_pre_approved_for_a_voice_message_either(engine, to):
    assert _tier(engine, to, tool="send_voice_message") is Tier.RED, to


@pytest.mark.parametrize("to", [
    "Saved Messages", "saved messages", "Saved Messages ", "me", "Me", "myself",
    "saved", "my notes", CHANNEL, "ai engineering & machine learning",
    "AI Engineering &amp; Machine Learning",
])
def test_what_he_pre_approved_goes_without_asking_as_a_voice_message_too(engine, to):
    # Without a QUESTION. His channel is announced first - AMBER, his decision of
    # 2026-10-01 (tests/test_channel_posts_are_read_aloud_first.py); his own notebook
    # reaches nobody and stays GREEN.
    channel = "machine learning" in to.lower()
    assert _tier(engine, to, tool="send_voice_message") is (Tier.AMBER if channel else Tier.GREEN), to


@pytest.mark.parametrize("to", ["Uluhbek", "Rodion", "Mom", "@durov", "+998901234567"])
def test_other_people_still_need_a_yes_to_hear_his_voice_message(engine, to):
    assert _tier(engine, to, tool="send_voice_message") is Tier.RED


@pytest.mark.parametrize("to", ["Saved Messages", "me", "my notes", CHANNEL, "Ed", "a"])
def test_a_voice_message_is_refused_under_taint_even_to_saved_messages(engine, to):
    """
    The Saved Messages exception below covers TEXT he asked to have put in his
    own notebook. It does not cover speech: a voice message a stranger's words
    asked for is refused everywhere, named or not.
    """
    assert _tier(engine, to, tool="send_voice_message", origin="content") is Tier.BLACK
    assert _tier(engine, to, tool="send_voice_message", origin="content",
                 named_by_him="Saved Messages") is Tier.BLACK


# ---------------------------------------------------------------------------
# THE ALIASES ARE THE RESOLVER'S ALIASES
# ---------------------------------------------------------------------------
class _FakeClient:
    """Enough of Telethon for messaging._resolve."""

    ME = object()

    def __init__(self):
        self.asked_for_me = 0

    async def get_me(self):
        self.asked_for_me += 1
        return self.ME

    async def get_entity(self, name):
        return None

    def iter_dialogs(self, limit=200):
        async def gen():
            if False:
                yield None
        return gen()


@pytest.mark.parametrize("alias", SELF_CHAT_ALIASES)
def test_every_self_alias_is_one_the_resolver_sends_to_him(alias):
    """
    If the gate approved a spelling the resolver did NOT map to his own
    account, the approval and the send would be about two different chats -
    the exact split this fix exists to close. Checked by running the real
    resolver, so a change to either side fails here.
    """
    from jarvis.tools import messaging

    client = _FakeClient()
    got = asyncio.run(messaging._resolve(client, alias))
    assert got is _FakeClient.ME, f"{alias!r} is pre-approved but resolves elsewhere"


@pytest.mark.parametrize("not_self", ["mes", "saved messages backup", "notes", "my"])
def test_near_misses_do_not_resolve_to_him_either(not_self):
    from jarvis.tools import messaging

    client = _FakeClient()
    asyncio.run(messaging._resolve(client, not_self))
    assert client.asked_for_me == 0


# ---------------------------------------------------------------------------
# THE OTHER SEND TOOLS, which the pre-approval ignored
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tool", ["send_posts", "send_telegram_file", "send_voice_message"])
def test_the_batch_and_file_sends_honour_pre_approval_too(engine, tool):
    """
    _DESTINATION_ARG covered only send_telegram_message and
    save_telegram_draft, so "send those fifty posts to my channel" and a
    file to his own Saved Messages asked every time - while the
    send_telegram_file spec told the model it would not.
    """
    assert _tier(engine, CHANNEL, tool=tool) is Tier.AMBER      # announced first, no question
    assert _tier(engine, "Saved Messages", tool=tool) is Tier.GREEN
    assert _tier(engine, "Rodion", tool=tool) is Tier.RED
    assert _tier(engine, "Ed", tool=tool) is Tier.RED


# ---------------------------------------------------------------------------
# UNDER TAINT: his own notebook, and nothing else
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("to", ["Saved Messages", "me", "my notes"])
def test_his_own_saved_messages_works_after_reading_an_email_when_he_asked(engine, to):
    """
    'Read Andres's email and put a summary in my Saved Messages.' He named
    the destination; the email did not.
    """
    assert _tier(engine, to, origin="content", named_by_him="Saved Messages") is Tier.GREEN


def test_an_email_that_asks_for_saved_messages_is_still_refused(engine):
    """
    THE LINE, and why it is drawn on HIS words rather than on the chat.

    Saved Messages is visible only to him, but that is exactly what makes it
    worth abusing: text an email wrote into his own notebook reads as if HE
    wrote it - a phishing link, a fake instruction to himself. So the
    exception needs the destination to have come from him, not from what
    Jalen read. tests/test_preapproved_sends.py pins the same case from the
    other side.
    """
    assert _tier(engine, "Saved Messages", origin="content") is Tier.BLACK
    assert _tier(engine, "Saved Messages", origin="content",
                 named_by_him="your ML channel") is Tier.BLACK


def test_a_batch_to_his_own_saved_messages_works_under_taint(engine):
    assert _tier(engine, "Saved Messages", tool="send_posts", origin="content",
                 named_by_him="Saved Messages") is Tier.GREEN


@pytest.mark.parametrize("to", [CHANNEL, "Uluhbek", "Ed", "a"])
def test_everything_else_is_still_refused_under_taint(engine, to):
    """
    The channel is read by his community. Something an email ASKED for
    must never be published there, however pre-approved the channel is -
    that is the injection guard's whole job. Even when he named Saved
    Messages: that licenses Saved Messages, nothing else.
    """
    assert _tier(engine, to, origin="content") is Tier.BLACK
    assert _tier(engine, to, origin="content", named_by_him="Saved Messages") is Tier.BLACK


def test_a_file_to_saved_messages_is_still_refused_under_taint(engine):
    """
    Deliberately not extended to files: which file to upload is exactly the
    kind of thing an injected instruction would choose, and an upload leaves
    the machine even when the chat is his.
    """
    assert _tier(engine, "Saved Messages", tool="send_telegram_file",
                 origin="content", named_by_him="Saved Messages") is Tier.BLACK


def test_the_taint_exception_only_exists_if_he_pre_approved_saved_messages():
    cfg = copy.deepcopy(CONFIG)
    cfg["telegram"]["personal"]["send_without_asking_to"] = [CHANNEL]
    engine = SafetyEngine(cfg)
    assert _tier(engine, "Saved Messages", origin="content",
                 named_by_him="Saved Messages") is Tier.BLACK
    assert _tier(engine, "Saved Messages", origin="user") is Tier.RED


# ---------------------------------------------------------------------------
# WHERE "WHAT HE NAMED" COMES FROM
# ---------------------------------------------------------------------------
def test_the_destination_he_named_is_recorded_when_his_instruction_starts():
    import queue
    import threading
    from types import SimpleNamespace

    from jarvis import taint
    from jarvis.app import Jalen

    class _Stop(Exception):
        pass

    j = Jalen.__new__(Jalen)
    j.cfg = CONFIG
    j.audit = SimpleNamespace(utterance=lambda *a, **k: None, write=lambda *a, **k: None)
    j.speaker = SimpleNamespace(stop=lambda: None)
    j.kill = threading.Event()
    j._answer_q, j._reply_q = queue.Queue(), queue.Queue()
    j._awaiting_reply = j._awaiting_confirmation = False
    j._pending_rating = None
    j._last_user_text, j._last_user_at, j._plan = "", 0.0, None
    j._turn_lock, j._active_turns = threading.Lock(), set()
    j.kill_phrases, j.end_phrases = [], []

    def _route(text):
        raise _Stop()

    j.router = SimpleNamespace(route=_route)

    for said, from_him, expected in (
        ("Jalen, read Andres's email and put a summary in my saved messages", True, "Saved Messages"),
        ("Jalen, what's the time", True, ""),
    ):
        try:
            j.process(said, from_him=from_him)
        except _Stop:
            pass
        assert taint.named() == expected, said

    # An utterance that is not positively him does not get to name one.
    taint.he_asked_again()
    try:
        j.process("put it in my saved messages", from_him=False)
    except _Stop:
        pass
    assert taint.named() == ""


def test_both_callers_pass_what_he_named_to_the_gate():
    import inspect

    from jarvis.app import Jalen
    from jarvis.brain.agent import Brain

    assert "named_by_him=" in inspect.getsource(Jalen)
    assert "named_by_him=" in inspect.getsource(Brain)


# ---------------------------------------------------------------------------
# THE INVARIANT
# ---------------------------------------------------------------------------
def test_the_injection_guard_still_comes_first():
    """
    tests/test_adversarial.py pins this by source position; the exception
    for his own chat lives INSIDE the injection block, so pre-approval
    still cannot run before it.
    """
    import inspect

    source = inspect.getsource(SafetyEngine.classify)
    assert source.index('origin == "content"') < source.index("_is_preapproved")
