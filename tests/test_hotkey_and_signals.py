"""
The global hotkey, and the file-based signal it talks through.

Ctrl+Alt+J has to work in two completely different situations that look the
same from the keyboard: Jalen is not running (launch him), and Jalen is
running but muted, paused, or mid-sentence (get his attention). The press
must do the right thing in all of them without the user knowing which state
he was in — that is the entire point of one key.

The Win32 parts (RegisterHotKey, the message loop, the named mutex) are not
unit-testable in-process; they are verified live. Everything that decides
WHAT a press means is a pure function, and that is what these cover.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jalen import runtime  # noqa: E402


# ------------------------------------------------------------------ signals
@pytest.fixture(autouse=True)
def clean_signal_file():
    """Each test starts with no pending signal and leaves none behind."""
    for _ in range(2):
        try:
            runtime.SIGNAL_PATH.unlink()
        except OSError:
            pass
        yield
        return


def test_take_signal_is_none_when_nothing_pending():
    assert runtime.take_signal() is None


def test_a_signal_is_consumed_exactly_once():
    """
    The read-and-delete is what makes this safe without a lock. A signal
    that survived being read would re-fire on every poll — one key press
    waking him over and over, thirty times a second.
    """
    runtime.SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    runtime.SIGNAL_PATH.write_text("wake", encoding="utf-8")

    assert runtime.take_signal() == "wake"
    assert runtime.take_signal() is None


def test_garbage_in_the_signal_file_is_ignored_not_obeyed():
    """
    The file is world-writable like any other file in data/. An unknown word
    must be dropped, not passed through to a handler that then does
    something undefined with it.
    """
    runtime.SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    runtime.SIGNAL_PATH.write_text("rm -rf /", encoding="utf-8")
    assert runtime.take_signal() is None


def test_sending_an_unknown_signal_is_a_programming_error():
    with pytest.raises(ValueError):
        runtime.send_signal("explode")


def test_send_signal_does_nothing_when_he_is_not_running(monkeypatch):
    """
    Writing a signal for a process that does not exist would leave it on
    disk to be picked up by the NEXT launch — a key press from an hour ago
    waking him at login.
    """
    monkeypatch.setattr(runtime, "running_instance", lambda: None)
    runtime.send_signal("wake")
    assert not runtime.SIGNAL_PATH.exists()


def test_send_signal_writes_when_he_is_running(monkeypatch):
    monkeypatch.setattr(runtime, "running_instance", lambda: object())
    runtime.send_signal("toggle")
    assert runtime.take_signal() == "toggle"


# ---------------------------------------------------- what a press means
class FakeSpeaker:
    def __init__(self, speaking=False):
        self.speaking = speaking
        self.stopped = False

    def stop(self):
        self.stopped = True
        self.speaking = False


class FakeOrb:
    def __init__(self):
        self.state = "idle"

    def set_state(self, state):
        self.state = state


class Bare:
    """The three attributes _apply_signal touches, and nothing else."""

    def __init__(self, *, muted=False, paused=False, speaking=False):
        self.muted = muted
        self.paused = paused
        self.speaker = FakeSpeaker(speaking)
        self.orb = FakeOrb()


def apply(state: Bare, signal: str) -> bool:
    from jalen.app import Jalen

    return Jalen._apply_signal(state, signal)


@pytest.mark.parametrize(
    "muted, paused, speaking",
    [
        (True, False, False),    # muted — the mute is what's in the way
        (False, True, False),    # paused — the pause is
        (True, True, False),     # both
        (False, False, False),   # awake and idle — nothing is; just listen
    ],
)
def test_toggle_always_ends_with_him_listening(muted, paused, speaking):
    """
    One key, one meaning: "pay attention to me". Whatever is in the way of
    that — mute, pause, or simply not having been addressed — the press
    removes it. He must never have to remember which state he left it in.
    """
    state = Bare(muted=muted, paused=paused, speaking=speaking)
    assert apply(state, "toggle") is True
    assert state.muted is False
    assert state.paused is False


def test_toggle_while_speaking_shuts_him_up():
    """
    The one state where "pay attention to me" means the opposite: he is
    already talking. This is barge-in for someone who would rather press a
    key than shout over it — and it must NOT then start a new turn, or the
    key that stops him would immediately make him listen for a command he
    did not intend to give.
    """
    state = Bare(speaking=True)
    assert apply(state, "toggle") is False
    assert state.speaker.stopped is True


def test_explicit_mute_and_unmute():
    state = Bare()
    assert apply(state, "mute") is False
    assert state.muted is True

    assert apply(state, "unmute") is False
    assert state.muted is False
    assert state.paused is False


def test_wake_clears_both_blockers_and_listens():
    state = Bare(muted=True, paused=True)
    assert apply(state, "wake") is True
    assert (state.muted, state.paused) == (False, False)


def test_unknown_signal_changes_nothing():
    state = Bare(muted=True, paused=True)
    assert apply(state, "nonsense") is False
    assert (state.muted, state.paused) == (True, True)


# -------------------------------------------------------- parsing hotkeys
@pytest.fixture(scope="module")
def hotkeys():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "jalen_hotkeys", ROOT / "scripts" / "hotkeys.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "spec, mods_expected, key_expected",
    [
        ("ctrl+alt+j", ["ctrl", "alt"], "J"),
        ("Ctrl+Alt+J", ["ctrl", "alt"], "J"),
        ("ctrl + alt + j", ["ctrl", "alt"], "J"),
        ("ctrl+alt+space", ["ctrl", "alt"], "space"),
        ("ctrl+shift+f9", ["ctrl", "shift"], "f9"),
        ("win+alt+k", ["win", "alt"], "K"),
    ],
)
def test_hotkey_specs_parse(hotkeys, spec, mods_expected, key_expected):
    parsed = hotkeys.parse_hotkey(spec)
    assert parsed is not None, f"{spec!r} should parse"
    mods, vk = parsed
    for name in mods_expected:
        assert mods & hotkeys.MODIFIERS[name], f"{spec!r} lost its {name}"
    assert mods & hotkeys.MOD_NOREPEAT, "holding the key must not fire repeatedly"
    assert vk == hotkeys.KEYS.get(key_expected, hotkeys.KEYS.get(key_expected.lower()))


@pytest.mark.parametrize(
    "spec, why",
    [
        ("j", "a bare key with no modifier would swallow ordinary typing"),
        ("ctrl+alt+banana", "unknown key name"),
        ("ctrl+alt", "modifiers with no key"),
        ("", "empty"),
        ("ctrl+meta+j", "unknown modifier"),
    ],
)
def test_bad_hotkey_specs_are_rejected_not_raised(hotkeys, spec, why):
    """
    A typo in config must cost one hotkey and a log line — never an
    exception. Crashing here would take out the OTHER hotkey too, and that
    one might be the kill switch for a runaway assistant.
    """
    assert hotkeys.parse_hotkey(spec) is None, why


def test_configured_hotkey_is_tried_before_its_fallbacks(hotkeys):
    """
    Fallbacks exist because combinations collide — Ctrl+Alt+Space was
    already owned by another app on this machine. They must never take
    priority over what the config actually asks for.
    """
    order = hotkeys._candidates("safety.kill_switch_hotkey", "ctrl+alt+k",
                                ["ctrl+shift+k", "ctrl+alt+f10"])
    from jalen.config import CONFIG

    assert order[0] == CONFIG.get_path("safety.kill_switch_hotkey", "ctrl+alt+k")
    assert len(order) == len(set(order)), "a fallback duplicating the choice wastes an attempt"


def test_every_shipped_hotkey_default_actually_parses(hotkeys):
    """The defaults in config/jalen.yaml must not be typos."""
    from jalen.config import CONFIG

    for path, default in [
        ("startup.wake_hotkey", "ctrl+alt+j"),
        ("safety.kill_switch_hotkey", "ctrl+alt+k"),
    ]:
        spec = CONFIG.get_path(path, default)
        assert hotkeys.parse_hotkey(spec) is not None, f"{path} = {spec!r} does not parse"


# ----------------------------------------------------------- the installer
def test_installer_removes_the_jarvis_era_entries():
    """
    A rename that leaves the old login shortcut in place gives you TWO
    assistants at login, both opening the same microphone. That is the
    doubled-replies failure runtime.py exists to prevent, arriving through
    the one door it cannot see.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "install_autostart", ROOT / "scripts" / "install_autostart.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "Jarvis.lnk" in module.LEGACY
    assert "start_jarvis.vbs" in module.LEGACY
    names = [name for name, _vbs, _target, _args in module.ENTRIES]
    assert "Jalen.lnk" in names
    assert "Jalen Hotkeys.lnk" in names, "the hotkey listener must start at login too"
