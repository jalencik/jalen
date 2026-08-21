"""
"Hand this task to cowork." / "Hand this off to code."

Two jargon phrases he defined, each meaning: take what we were just talking
about, write it up properly, and start the right agent on it.

The property that matters most here is that a handoff CANNOT SILENTLY FAIL.
Every desktop-automation failure in this project so far has been silent —
typing into a window that was not focused, Tab-and-Enter onto a control that
was not there, "I played it" for a video that never started. A handoff that
reported success while the agent sat on an empty prompt would be the same
bug wearing a new hat. So the prompt goes on the clipboard BEFORE anything
is launched, and the reply always tells him where it ended up.
"""
from __future__ import annotations

import pytest

from jarvis.tools import handoff


def test_the_prompt_survives_the_clipboard_exactly():
    """
    Dictated text carries em dashes, curly quotes and non-ASCII from his own
    speech. clip.exe mangles those under a legacy code page, which is why
    this goes through CF_UNICODETEXT directly.
    """
    import ctypes

    text = 'Objective — ship it. "Quoted", ünïcödé,\nand a second line.'
    assert handoff.set_clipboard(text) is True

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    assert user32.OpenClipboard(None)
    try:
        pointer = kernel32.GlobalLock(user32.GetClipboardData(13))
        assert ctypes.wstring_at(pointer) == text
    finally:
        user32.CloseClipboard()


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_an_empty_brief_is_refused_not_launched(empty):
    """
    Launching an agent on nothing is worse than not launching it: he watches
    a window open, assumes it is working, and it is not.
    """
    for fn in (handoff.hand_off_to_cowork, handoff.hand_off_to_code):
        reply = fn(empty)
        assert "Tell me what the task is" in reply


def test_the_clipboard_is_loaded_before_the_app_is_launched(monkeypatch):
    """
    Ordering IS the safety property. If the app were launched first and the
    clipboard filled after, a fast paste would drop whatever was on his
    clipboard before — which could be anything at all.
    """
    order = []
    monkeypatch.setattr(handoff, "set_clipboard", lambda t: order.append("clipboard") or True)
    monkeypatch.setattr(handoff, "_launch_desktop_app", lambda: order.append("launch") or True)
    monkeypatch.setattr(handoff, "_paste_into_foreground", lambda: order.append("paste") or True)
    monkeypatch.setattr(handoff.time, "sleep", lambda s: None)

    handoff.hand_off_to_cowork("Objective: do the thing.")
    assert order == ["clipboard", "launch", "paste"]


def test_a_failed_paste_still_tells_him_where_the_brief_is(monkeypatch):
    """
    The important case. If the paste does not land, the prompt is still one
    Ctrl+V away — and he has to be told, or he sees an empty box and assumes
    Jalen did nothing.
    """
    monkeypatch.setattr(handoff, "set_clipboard", lambda t: True)
    monkeypatch.setattr(handoff, "_launch_desktop_app", lambda: True)
    monkeypatch.setattr(handoff, "_paste_into_foreground", lambda: False)
    monkeypatch.setattr(handoff.time, "sleep", lambda s: None)

    reply = handoff.hand_off_to_cowork("Objective: do the thing.")
    assert "clipboard" in reply.lower()
    assert "ctrl+v" in reply.lower()


def test_a_failed_launch_is_reported_as_a_failure(monkeypatch):
    monkeypatch.setattr(handoff, "set_clipboard", lambda t: True)
    monkeypatch.setattr(handoff, "_launch_desktop_app", lambda: False)
    monkeypatch.setattr(handoff.time, "sleep", lambda s: None)

    reply = handoff.hand_off_to_cowork("Objective: do the thing.")
    assert "couldn't start" in reply.lower()


def test_cowork_never_presses_send(monkeypatch):
    """
    He reviews before it goes. The handoff pastes and stops — anything that
    submitted for him would be putting words in his mouth to an agent.
    """
    keys = []
    monkeypatch.setattr(handoff, "set_clipboard", lambda t: True)
    monkeypatch.setattr(handoff, "_launch_desktop_app", lambda: True)
    monkeypatch.setattr(handoff.time, "sleep", lambda s: None)

    def record():
        keys.append("ctrl+v")
        return True

    monkeypatch.setattr(handoff, "_paste_into_foreground", record)
    handoff.hand_off_to_cowork("Objective: do the thing.")
    assert keys == ["ctrl+v"], "something other than a paste was sent"


def test_code_goes_through_the_argv_path_not_a_paste(monkeypatch):
    """
    Claude Code takes the prompt as an argv entry, so quotes and newlines in
    dictated text cannot break the command or be reinterpreted by a shell.
    That is deterministic in a way no paste can be, and it must not quietly
    become a paste.
    """
    seen = {}
    monkeypatch.setattr(handoff, "set_clipboard", lambda t: True)

    import jarvis.tools.coding as coding

    monkeypatch.setattr(
        coding, "ask_claude_code",
        lambda prompt, folder="", agent="": seen.update(prompt=prompt, folder=folder) or "started",
    )
    reply = handoff.hand_off_to_code("Objective: fix the failing tests.", folder="repo")
    assert reply == "started"
    assert seen["prompt"] == "Objective: fix the failing tests."
    assert seen["folder"] == "repo"


# ------------------------------------------------------------- reachability
def test_both_tools_are_dispatchable_and_gated():
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    for name in ("hand_off_to_cowork", "hand_off_to_code"):
        assert name in tools.REGISTRY, f"{name} is not dispatchable"
        assert name in TOOL_SPECS, f"{name} is invisible to the brain"
        # AMBER: he hears the destination and has two seconds to stop it.
        # GREEN would start an autonomous agent with no announcement at all.
        assert engine.classify(name, {}).tier is Tier.AMBER


@pytest.mark.parametrize(
    "phrase",
    [
        "hand this task to cowork",
        "hand this off to code",
        "hand this task to co-work",
        "hand this off to cork",
        "jalen hand this task to cowork",
    ],
)
def test_the_jargon_reaches_the_brain_not_a_router_rule(phrase):
    """
    Deliberately NOT routed. "This task" is the conversation, which only the
    brain has — a router rule would launch an agent on a placeholder.
    """
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    hit = IntentRouter(CONFIG).route(phrase)
    assert hit is None or hit.tool not in ("open_target", "focus_window", "open_app"), (
        f"{phrase!r} was swallowed as {hit.tool} — it never reaches the brief-writer"
    )


def test_the_brief_standard_is_in_the_prompt():
    """
    He asked for prompts written "like a prompt engineer with at least 10
    years of experience". That is a real requirement, not flourish: the
    receiving agent has none of the conversation.
    """
    from jarvis.brain.agent import Brain
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    async def noop(*a, **k):
        return True

    prompt = Brain(
        CONFIG, SafetyEngine(CONFIG), None, confirm=noop, announce=noop
    ).system_prompt().lower()

    for required in ("objective", "deliverable", "constraints", "acceptance criteria"):
        assert required in prompt, f"the brief standard lost {required!r}"
    assert "hand_off_to_cowork" in prompt
    assert "hand_off_to_code" in prompt
