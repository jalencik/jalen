"""
After Jalen read anything, his plainest spoken commands were refused.

00f900c put the GREEN tools that act on the world on
injection_guard.refuse_from_content, so a page cannot type, click, launch or
write on its own say-so. But the ROUTER path - handle_local, where a phrase
like "scroll down" matches a rule and runs with no model involved - took its
origin from the same process-wide taint. Taint clears only when he addresses
Jalen by name with no other turn running, so after Jalen read an email or a
page and answered, the follow-up he said without the name was refused. Real
rule matches, classified under a tainted turn on 2026-10-01:

    cancel that      cancel_task        BLACK
    scroll down      keyboard_shortcut  BLACK
    go back          keyboard_shortcut  BLACK
    close this       keyboard_shortcut  BLACK
    close notepad    close_app          BLACK
    open spotify     open_app           BLACK
    open chrome      open_target        BLACK
    open my downloads  open_folder      BLACK

"Ask me again yourself and I will" is the wrong answer to a command he
just gave himself.

WHY THE ROUTER MAY TRUST WHAT IT MATCHED. A router rule matches his own words:
speech, typing, or a message from his own authenticated Telegram chat
(TELEGRAM_ALLOWED_USER_IDS). A web page, an email or another model cannot
write them. What taint protects against is the MODEL acting on read text, and
that path - the brain's tool hook - is untouched and still refuses.

THE ONE WAY CONTENT CAN STILL REACH THE ROUTER is expand_references, which
turns "open it" into "open <the remembered subject>", and that subject can be
something Jalen read (an email subject line). So the exemption applies only
when expansion left his words exactly as he said them; an expanded sentence
keeps the taint check. A recalled habit keeps it too.
"""
from __future__ import annotations

import pytest

from jarvis import taint
from jarvis.app import Jalen
from jarvis.brain.router import Intent

from test_router_safety_gate import jarvis  # noqa: F401 - the fixture


@pytest.fixture(autouse=True)
def _tainted_turn():
    taint.he_asked_again()
    taint.mark("email from someone", "an email body")
    yield
    taint.he_asked_again()


@pytest.fixture
def ran(monkeypatch):
    """Records what would have run; nothing real is touched."""
    calls = []
    from jarvis import tools as systools

    monkeypatch.setattr(systools, "call", lambda tool, args: calls.append((tool, dict(args))) or "done")
    return calls


@pytest.mark.parametrize("tool, args", [
    ("keyboard_shortcut", {"keys": "{PageDown}"}),
    ("close_app", {"name": "notepad"}),
    ("open_app", {"name": "spotify"}),
    ("open_folder", {"path": "~/Downloads"}),
    ("cancel_task", {"text": "cancel that"}),
])
def test_a_rule_that_matched_his_own_words_runs_after_a_read(jarvis, ran, tool, args):
    reply = jarvis.handle_local(Intent(tool=tool, args=args, reply=None), his_own_words=True)
    assert ran and ran[0][0] == tool, f"{tool} was refused: {reply}"


@pytest.mark.parametrize("tool, args", [
    ("keyboard_shortcut", {"keys": "{PageDown}"}),
    ("open_app", {"name": "spotify"}),
])
def test_without_that_flag_the_taint_still_decides(jarvis, ran, tool, args):
    """The default is unchanged: a habit recalled, or a caller that does not
    know, is checked against the taint exactly as before."""
    reply = jarvis.handle_local(Intent(tool=tool, args=args, reply=None))
    assert not ran
    assert "I won't do that" in (reply or "")


def test_his_own_words_do_not_unlock_what_the_never_touch_list_forbids(jarvis, ran):
    reply = jarvis.handle_local(
        Intent(tool="open_target", args={"name": "C:\\Windows\\System32\\cmd.exe"}, reply=None),
        his_own_words=True)
    assert not ran, reply


def test_process_passes_the_flag_for_a_router_match_and_not_for_a_habit():
    import inspect

    source = inspect.getsource(Jalen.process)
    assert "his_own_words=his_own_words" in source
    habit_call = source[source.index("habits.recall"):]
    assert "handle_local(Intent(tool=tool, args=args, reply=None))" in habit_call


def test_an_expanded_sentence_is_not_his_own_words(jarvis, ran, monkeypatch):
    """'open it' became 'open <something that was read>': taint decides."""
    from jarvis import conversation

    # "open spotify" is a real router rule, so the expanded sentence DOES
    # reach handle_local - the assertion below cannot pass by not running.
    monkeypatch.setattr(conversation, "expand_references",
                        lambda text: "open spotify")
    seen = {}
    real = Jalen.handle_local

    def spy(self, intent, **kw):
        seen["his_own_words"] = kw.get("his_own_words")
        return real(self, intent, **kw)

    monkeypatch.setattr(Jalen, "handle_local", spy)
    jarvis.say = lambda t, force=False: None
    jarvis.process("open it", from_him=False)
    assert "his_own_words" in seen, "the expanded sentence never reached the router path"
    assert seen["his_own_words"] is False
    assert not ran, "an expanded sentence ran after a read"


def test_an_unexpanded_router_match_sends_the_flag_through_process(jarvis, ran, monkeypatch):
    seen = {}
    real = Jalen.handle_local

    def spy(self, intent, **kw):
        seen["his_own_words"] = kw.get("his_own_words")
        return real(self, intent, **kw)

    monkeypatch.setattr(Jalen, "handle_local", spy)
    jarvis.say = lambda t, force=False: None
    jarvis.process("scroll down", from_him=False)
    assert seen.get("his_own_words") is True
    assert ran and ran[0][0] == "keyboard_shortcut"


def test_a_sentence_admitted_by_a_door_keeps_the_taint_check(jarvis, ran, monkeypatch):
    """
    The listening work lets some sentences in WITHOUT his name (a misheard
    name, a polite request, the bare-wake window). Those are the least certain
    speech there is, so they are not "his own words" for the purpose of
    skipping the taint check: found by the independent re-check of that work.
    An ordinary follow-up (no door) keeps the exemption.
    """
    jarvis.say = lambda t, force=False: None

    jarvis.process("scroll down", from_him=False, door="polite-request")
    assert not ran, "a door-admitted sentence ran after a read"

    jarvis.process("scroll down", from_him=False)
    assert ran and ran[0][0] == "keyboard_shortcut"


def test_run_carries_the_door_to_the_turn():
    import inspect

    source = inspect.getsource(Jalen.run)
    # The sound's onset rides along since 2026-10-04 (an answer older than
    # the question it would answer is not its answer); the door still does.
    assert "args=(text, turn_id, timer, from_him, door, sound_began_at)" in source
    assert "door=door" in source


def test_the_brain_path_is_untouched():
    """The model's tool calls still take their origin from the taint."""
    import inspect

    from jarvis.brain.agent import Brain

    assert "taint.origin_now()" in inspect.getsource(Brain._make_hook)
