"""
A protected path said with a kind word in front of it is opened.

What is wrong: the never-touch check (SafetyEngine._touches_forbidden_path)
inspects open_target's `name` only when the WHOLE value looks like a path.
Since ce8d92e, open_target strips "file " / "folder " / "directory " /
"document " and "<kind> called|named|titled " off the front and then opens
the explicit path that is left (launcher._open_explicit_path ->
os.startfile). So the gate sees a phrase and says GREEN, and the tool opens
a path on the never-touch list.

Measured: classify("open_target", {"name": "C:\\Windows\\System32\\cmd.exe"})
is BLACK (the control below), while the same path with "file ", "the folder "
or "document named " in front classifies GREEN, and open_target then hands
the protected path to os.startfile (recorded here, nothing launches). The
generic router rule "open (.+)" passes his words after "open" as `name`, so
"open file C:\\Windows\\System32\\cmd.exe" typed in text mode reaches it.

FAILS ON MAIN BY DESIGN (Session B C2 review).
"""
from __future__ import annotations

import os

import pytest

from jarvis import taint
from jarvis.config import CONFIG
from jarvis.safety import SafetyEngine, Tier
from jarvis.tools import launcher

PROTECTED = r"C:\Windows\System32\cmd.exe"


@pytest.fixture
def opened(monkeypatch):
    """What open_target would have handed to os.startfile; nothing is opened."""
    calls: list[str] = []
    monkeypatch.setattr(launcher, "_startfile", lambda target: calls.append(str(target)))
    return calls


@pytest.fixture(autouse=True)
def _clean_turn():
    taint.he_asked_again()
    yield
    taint.he_asked_again()


def _as_the_router_runs_it(engine: SafetyEngine, args: dict) -> str:
    """handle_local's order: classify first, run only what is not refused."""
    verdict = engine.classify("open_target", args, origin=taint.origin_now(),
                              named_by_him=taint.named())
    if verdict.tier is Tier.BLACK:
        return f"I won't do that — {verdict.reason}."
    return launcher.open_target(**args)


@pytest.mark.skipif(not os.path.exists(PROTECTED), reason="needs the Windows folder")
def test_the_bare_protected_path_is_refused_by_the_gate(opened):
    """Control: the plain path is BLACK, so the check below can pass."""
    engine = SafetyEngine(CONFIG)
    reply = _as_the_router_runs_it(engine, {"name": PROTECTED})
    assert reply.startswith("I won't do that"), reply
    assert not opened


@pytest.mark.skipif(not os.path.exists(PROTECTED), reason="needs the Windows folder")
@pytest.mark.parametrize("name, kind", [
    ("file " + PROTECTED, ""),
    ("the folder " + os.path.dirname(PROTECTED), ""),
    ("document named " + PROTECTED, ""),
    (PROTECTED, "file"),
])
def test_a_protected_path_with_a_kind_word_in_front_is_not_opened(opened, name, kind):
    engine = SafetyEngine(CONFIG)
    args = {"name": name, "kind": kind} if kind else {"name": name}
    reply = _as_the_router_runs_it(engine, args)
    protected = [t for t in opened if engine.protected_path(t)]
    assert not protected, (
        f"never-touch path opened for name={name!r}: {protected} (gate said: {reply!r})")
