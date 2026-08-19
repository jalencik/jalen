"""
Regression tests for the product-quality overhaul.

Every test here corresponds to a defect found by measuring or driving the
real application, not by reading it — the user's own report ("takes a
minute", "basic commands don't work", "I can't stop it", "old commands
execute later in a burst") is the source of truth for all of them.
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.brain.router import IntentRouter  # noqa: E402
from jarvis.config import CONFIG, Cfg  # noqa: E402


@pytest.fixture
def router():
    return IntentRouter(CONFIG)


def route_tool(router, text):
    intent = router.route(text)
    return intent.tool if intent else None


# --------------------------------------------------------- app launching
@pytest.mark.parametrize(
    "phrase",
    ["open chrome", "launch chrome", "start chrome", "run chrome",
     "fire up chrome", "pull up chrome", "open up chrome"],
)
def test_every_launch_verb_routes_locally(router, phrase):
    """Only "open" was recognised; the rest paid a full Claude round-trip
    (seconds) for something the router does in under a millisecond."""
    assert route_tool(router, phrase) == "open_app"


def test_trailing_self_address_is_stripped(router):
    """"open chrome, Jarvis" reached open_app as name="chrome, jarvis" and
    tried to launch an app by that literal name."""
    intent = router.route("open chrome, jarvis")
    assert intent.tool == "open_app"
    assert intent.args["name"] == "chrome"


@pytest.mark.parametrize("phrase", ["can you open chrome", "please open chrome",
                                    "could you please open chrome"])
def test_leading_politeness_is_stripped(router, phrase):
    intent = router.route(phrase)
    assert intent.tool == "open_app"
    assert intent.args["name"] == "chrome"


def test_open_folder_is_not_shadowed_by_open_app(router):
    """The open_app catch-all "^open (.+)$" sat first and swallowed every
    "open the folder X", making open_folder unreachable."""
    assert route_tool(router, "open the folder Downloads") == "open_folder"


def test_play_named_media_app_launches_it(router):
    """"play spotify" hit the play/pause media key — a silent no-op when
    Spotify isn't running — instead of launching it."""
    assert route_tool(router, "play spotify") == "open_app"


def test_generic_play_music_still_uses_the_media_key(router):
    assert route_tool(router, "play the music") == "media_play_pause"


@pytest.mark.parametrize("phrase", ["minimize this window", "maximize this window",
                                    "minimize the window", "maximize window"])
def test_window_state_matches_every_article(router, phrase):
    """The optional-article group had a space on only one branch, so
    "...this window" never matched."""
    assert route_tool(router, phrase) == "window_state"


# ------------------------------------------------------------- lifecycle
@pytest.mark.parametrize(
    "phrase,expected",
    [("quit jarvis", "jarvis_quit"), ("exit", "jarvis_quit"), ("shut down", "jarvis_quit"),
     ("pause", "jarvis_pause"), ("hold on", "jarvis_pause"),
     # "stop listening"/"go to sleep" hit the older jarvis_sleep rule, which
     # now pauses for real instead of only claiming to.
     ("stop listening", "jarvis_sleep"), ("go to sleep", "jarvis_sleep"),
     ("resume", "jarvis_resume"), ("wake up", "jarvis_resume"),
     ("restart jarvis", "jarvis_restart")],
)
def test_lifecycle_commands_exist(router, phrase, expected):
    """There was no spoken way to stop Jarvis at all — only Ctrl+C in
    whichever terminal launched it, useless once that window is closed."""
    assert route_tool(router, phrase) == expected


def test_no_catch_all_rule_shadows_a_jarvis_command(router):
    """
    Guards the whole CLASS of bug, not just the instances found.

    Three separate defects were catch-all rules eating specific ones:
    "open the folder X" -> open_app, "go to sleep" -> focus_window (a window
    named "sleep"), "play spotify" -> media key. Any command aimed at Jarvis
    itself must reach a jarvis_* tool and never be reinterpreted as an app,
    window or file name.
    """
    self_commands = {
        "quit": "jarvis_quit", "exit": "jarvis_quit", "shut down": "jarvis_quit",
        "pause": "jarvis_pause", "resume": "jarvis_resume", "wake up": "jarvis_resume",
        "restart": "jarvis_restart", "go to sleep": "jarvis_sleep",
        "sleep": "jarvis_sleep", "stop listening": "jarvis_sleep",
        "mute": "jarvis_mute", "unmute": "jarvis_unmute",
        "be quiet": "jarvis_mute", "shut up": "jarvis_mute",
    }
    wrong = {}
    for phrase, expected in self_commands.items():
        got = route_tool(router, phrase)
        if got != expected:
            wrong[phrase] = f"expected {expected}, got {got}"
    assert not wrong, f"catch-all rules are shadowing Jarvis's own commands: {wrong}"


# ---------------------------------------------------- window title matching
def test_title_pattern_is_case_insensitive():
    """The router lowercases every utterance, so a case-sensitive title
    regex never matched real window titles — close/focus silently failed."""
    from jarvis.tools.system import _title_pattern

    assert _title_pattern("chrome").search("Untitled - Google Chrome")
    assert _title_pattern("notepad").search("Untitled - Notepad")


def test_title_pattern_escapes_regex_characters():
    from jarvis.tools.system import _title_pattern

    assert _title_pattern("c++ (test)").search("my c++ (test) window")


# --------------------------------------------------------- app resolution
def test_unknown_app_is_reported_not_faked():
    """open_app used to reply "Opening X" for anything Popen didn't raise on,
    so a nonexistent app produced a confident success and nothing opened."""
    from jarvis.tools.system import open_app

    result = open_app("zzz_definitely_not_an_app_9x7")
    assert "couldn't find" in result.lower()
    assert "opening" not in result.lower()


def test_known_app_resolves_to_a_real_target():
    from jarvis.tools.system import _resolve_executable

    assert _resolve_executable("notepad")
    assert _resolve_executable("zzz_definitely_not_an_app_9x7") is None


# ------------------------------------------------------------------- tts
def test_sentence_streaming_config_is_actually_read():
    """tts.sentence_streaming was documented and configured but nothing read
    it — synthesis and playback were always strictly serial."""
    from jarvis.audio.tts import Speaker

    on = Cfg({**CONFIG, "tts": {**CONFIG.get("tts", {}), "sentence_streaming": True}})
    off = Cfg({**CONFIG, "tts": {**CONFIG.get("tts", {}), "sentence_streaming": False}})
    assert Speaker(on).sentence_streaming is True
    assert Speaker(off).sentence_streaming is False


def test_speaker_serializes_concurrent_calls():
    """Two turns finishing at once both called say(), and since the
    interrupt/speaking flags are instance state one call's cleanup clobbered
    the other's mid-playback."""
    from jarvis.audio.tts import Speaker

    speaker = Speaker(CONFIG)
    overlaps = []
    active = threading.Event()

    def fake_render(sentence):
        if active.is_set():
            overlaps.append(sentence)
        active.set()
        import time as t
        t.sleep(0.05)
        active.clear()
        return None  # skip playback entirely

    speaker._render = fake_render
    speaker._say_sequential = lambda sents: all(fake_render(s) is None for s in sents)

    threads = [threading.Thread(target=speaker.say, args=(f"Sentence {i}.",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not overlaps, f"say() ran concurrently: {overlaps}"


# ------------------------------------------------------------- mic buffer
def test_queued_seconds_reports_backlog():
    """The wake handler needs this to tell "the rest of your sentence"
    (keep) from "the machine stalled" (drop)."""
    import numpy as np
    from jarvis.audio.mic import Microphone

    mic = Microphone(CONFIG)
    assert mic.queued_seconds() == 0.0
    for _ in range(10):
        mic._q.put(np.zeros(mic.blocksize, dtype=np.float32))
    expected = 10 * mic.blocksize / mic.sample_rate
    assert abs(mic.queued_seconds() - expected) < 1e-6


# ---------------------------------------------------------------- safety
def test_window_state_is_green_not_amber():
    """Unclassified tools default to AMBER, so minimising a window paid a
    mandatory 4-second announce-and-wait."""
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    verdict = engine.classify("window_state", {"state": "minimize"})
    assert verdict.tier.value == "green"
    assert not verdict.detail.get("unclassified")


def test_private_mode_default_is_honoured(tmp_path):
    """memory.private_mode_default was configurable but ignored."""
    from jarvis.audit import AuditLog

    cfg = Cfg({
        **CONFIG,
        "memory": {**CONFIG.get("memory", {}), "private_mode_default": True},
        "audit": {**CONFIG.get("audit", {}),
                  "db_path": str(tmp_path / "a.db"),
                  "jsonl_path": str(tmp_path / "a.jsonl")},
    })
    assert AuditLog(cfg, "test-private").private is True
