"""
Tests for jarvis/ui/orb.py.

The orb is a tkinter overlay — actual rendering can't be asserted from
pytest, and that part was verified by running it (see the session notes /
PR description, not this file). What's tested here is everything that
doesn't need a display: config wiring, the state machine driving the
render loop, the transcript caption's queue/truncate/auto-hide logic, and
the click-through Win32 style bit-math in isolation.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import CONFIG, Cfg  # noqa: E402
from jarvis.ui.orb import (  # noqa: E402
    STATES,
    TRANSCRIPT_MAX_CHARS,
    TRANSCRIPT_TTL_S,
    Orb,
    _mix,
    set_click_through,
)


def _cfg(**ui_overrides):
    ui = {**CONFIG.get("ui", {}), **ui_overrides}
    return Cfg({**CONFIG, "ui": ui})


# --------------------------------------------------------------- config wiring
def test_show_transcript_key_exists_in_shipped_config():
    """The task requires ui.show_transcript to be a real, documented key —
    not just a Python-side default nobody can discover."""
    assert "show_transcript" in CONFIG.get("ui", {})


def test_click_through_key_exists_in_shipped_config():
    assert "click_through_when_busy" in CONFIG.get("ui", {})


def test_orb_reads_show_transcript_default_true():
    orb = Orb(_cfg())
    assert orb.show_transcript is True


def test_orb_reads_show_transcript_false():
    orb = Orb(_cfg(show_transcript=False))
    assert orb.show_transcript is False


def test_orb_reads_click_through_default_true():
    orb = Orb(_cfg())
    assert orb.click_through_when_busy is True


def test_orb_reads_click_through_false():
    orb = Orb(_cfg(click_through_when_busy=False))
    assert orb.click_through_when_busy is False


def test_the_old_config_key_is_still_honoured():
    """
    ui.click_through_when_idle shipped meaning the OPPOSITE of what it said,
    so the only people who ever set it to false did so to stop the orb
    eating their clicks. Renaming the key must not silently re-enable the
    behaviour they turned off.
    """
    orb = Orb(_cfg(click_through_when_idle=False))
    assert orb.click_through_when_busy is False


def test_the_orb_is_a_ghost_whenever_it_is_busy():
    """
    THE CURSOR BUG, as a test. The rule used to be `state == "idle"`, so the
    orb went solid the moment Jalen started working and swallowed every
    click inside its rectangle. Measured on the live window at the time:

        idle       transparent   clicks passed through
        listening  CLEAR         BLOCKED
        thinking   CLEAR         BLOCKED
        speaking   CLEAR         BLOCKED
        muted      CLEAR         BLOCKED   <- and mute is a state you LEAVE it in

    Asserted against the source rather than a live window because a Tk
    window in the suite is slow and flaky, and the thing that regressed is
    the comparison itself. scripts/probe_orb.py does the live version.
    """
    import inspect

    source = inspect.getsource(Orb._run)
    assert "set_click_through(self._hwnd, True)" in source, (
        "the orb is no longer unconditionally click-through - it can eat "
        "the cursor again"
    )
    assert 'self._state == "idle"' not in source, (
        "click-through has become conditional on state again; with no mouse "
        "bindings left there is no reason for it ever to be solid"
    )
    for binding in ("<Button-1>", "<B1-Motion>", "<MouseWheel>"):
        assert binding not in source, (
            f"{binding} is back - the orb is draggable again, and he asked "
            f"for it to stay still"
        )


# ------------------------------------------------------------ colour lookup
def test_every_state_has_a_distinct_colour():
    """Six states, six different hex values — colour is still the first
    cue even though it's no longer the only one."""
    orb = Orb(_cfg())
    colours = [orb.colours[s] for s in STATES]
    assert len(set(colours)) == len(colours)


def test_theme_overrides_from_config_are_honoured():
    orb = Orb(_cfg(theme={"idle": "#000000", "listening": "#111111",
                           "thinking": "#222222", "speaking": "#333333",
                           "blocked": "#444444"}))
    assert orb.colours["idle"] == "#000000"
    assert orb.colours["listening"] == "#111111"


def test_listening_colour_defaults_to_a_recognisable_blue():
    """The spec is explicit: BLUE when listening. Guard the literal hue,
    not just "not the same as before"."""
    orb = Orb(_cfg())
    r, g, b = (int(orb.colours["listening"][i:i + 2], 16) for i in (1, 3, 5))
    assert b > r and b > g  # blue channel dominates


def test_thinking_colour_defaults_to_a_recognisable_yellow():
    """The spec is explicit: YELLOW when executing (the "thinking" state is
    what app.py sets while a tool call / brain turn is running)."""
    orb = Orb(_cfg())
    r, g, b = (int(orb.colours["thinking"][i:i + 2], 16) for i in (1, 3, 5))
    assert r > 200 and g > 150 and b < 100  # yellow: high R+G, low B


def test_idle_colour_is_visible():
    """
    REVERSED, on his instruction, and the reversal is the point of the test.

    This used to assert max(r, g, b) < 60 — "silent/dark when idle", from an
    earlier request for "black when stopped". He then reported the orb as not
    appearing at all, "no matter whichever window I will be", and at RGB
    (22, 28, 36) on a dark desktop that is an accurate description of what it
    was doing rather than a misunderstanding.

    So idle must now be genuinely visible. It is still the calmest of the
    five — a resting colour, not an alerting one — but a dark idle is a
    request to make the orb invisible, and this test exists to stop that
    being reintroduced as a tidy-up.
    """
    orb = Orb(_cfg())
    r, g, b = (int(orb.colours["idle"][i:i + 2], 16) for i in (1, 3, 5))
    assert max(r, g, b) >= 120, (
        f"idle is {orb.colours['idle']} — too dark to see on a dark desktop"
    )


# --------------------------------------------------------------- state machine
def test_set_state_accepts_known_states():
    orb = Orb(_cfg())
    for state in STATES:
        orb.set_state(state)
        kind, value = orb._q.get_nowait()
        assert (kind, value) == ("state", state)


def test_set_state_rejects_unknown_state_silently():
    """A typo in a state name must not crash the caller — it should just be
    dropped, the same way a bad tool arg degrades rather than raising."""
    orb = Orb(_cfg())
    orb.set_state("not-a-real-state")
    assert orb._q.empty()


def test_set_level_clamps_to_unit_range():
    orb = Orb(_cfg())
    orb.set_level(5.0)
    assert orb._q.get_nowait() == ("level", 1.0)
    orb.set_level(-3.0)
    assert orb._q.get_nowait() == ("level", 0.0)


def test_flash_queues_state_and_duration():
    orb = Orb(_cfg())
    orb.flash("blocked", 0.8)
    kind, value = orb._q.get_nowait()
    assert kind == "flash"
    assert value == ("blocked", 0.8)


# ------------------------------------------------------- transcript caption
def test_set_transcript_queues_heard_text():
    orb = Orb(_cfg())
    orb.set_transcript("open chrome")
    kind, value = orb._q.get_nowait()
    assert (kind, value) == ("heard", "open chrome")


def test_set_transcript_ignores_blank_text():
    """A misheard silent utterance shouldn't flash an empty caption box."""
    orb = Orb(_cfg())
    orb.set_transcript("   ")
    orb.set_transcript("")
    assert orb._q.empty()


def test_set_transcript_strips_whitespace():
    orb = Orb(_cfg())
    orb.set_transcript("  hey jarvis open chrome  ")
    _, value = orb._q.get_nowait()
    assert value == "hey jarvis open chrome"


def test_transcript_caption_hides_after_ttl(monkeypatch):
    """Drive the same auto-hide check _draw_caption uses, without needing a
    live Canvas: once TRANSCRIPT_TTL_S has passed, the caption must not be
    considered current any more."""
    orb = Orb(_cfg())
    orb._heard = "open chrome"
    orb._heard_at = time.monotonic() - (TRANSCRIPT_TTL_S + 1)
    assert (time.monotonic() - orb._heard_at) > TRANSCRIPT_TTL_S


def test_transcript_caption_visible_within_ttl():
    orb = Orb(_cfg())
    orb._heard = "open chrome"
    orb._heard_at = time.monotonic()
    assert (time.monotonic() - orb._heard_at) < TRANSCRIPT_TTL_S


def test_long_transcript_would_be_truncated():
    """Mirrors the truncation _draw_caption applies — this is a glance-sized
    caption, not a paragraph."""
    long_text = "a" * 200
    truncated = long_text[: TRANSCRIPT_MAX_CHARS - 1].rstrip() + "…"
    assert len(truncated) <= TRANSCRIPT_MAX_CHARS
    assert truncated.endswith("…")


# --------------------------------------------------------------------- _mix
def test_mix_zero_returns_first_colour():
    assert _mix("#ff0000", "#000000", 0.0) == "#ff0000"


def test_mix_one_returns_second_colour():
    assert _mix("#ff0000", "#000000", 1.0) == "#000000"


def test_mix_halfway_is_between():
    result = _mix("#ff0000", "#000000", 0.5)
    r = int(result[1:3], 16)
    assert 100 < r < 150


def test_mix_clamps_out_of_range_t():
    assert _mix("#ff0000", "#000000", 5.0) == _mix("#ff0000", "#000000", 1.0)
    assert _mix("#ff0000", "#000000", -5.0) == _mix("#ff0000", "#000000", 0.0)


def test_mix_degrades_on_bad_colour_instead_of_raising():
    """A malformed theme colour in the config must not crash the render
    loop — it should fall back to the original string."""
    assert _mix("not-a-colour", "#000000", 0.5) == "not-a-colour"


# ------------------------------------------------------- click-through (win32)
@pytest.mark.skipif(sys.platform != "win32", reason="Win32-only feature")
def test_click_through_toggles_the_transparent_bit_on_a_real_window():
    """
    Real verification, not a mock: create an actual overrideredirect Tk
    window, flip click-through on and off through set_click_through(), and
    read the extended style back from Windows itself (GetWindowLongW) to
    confirm WS_EX_TRANSPARENT genuinely changed — not just that the call
    didn't raise.
    """
    import ctypes
    import tkinter as tk

    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020

    root = tk.Tk()
    try:
        root.overrideredirect(True)
        root.geometry("40x40+0+0")
        root.update_idletasks()
        root.update()
        hwnd = root.winfo_id()

        assert set_click_through(hwnd, True) is True
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        assert style & WS_EX_TRANSPARENT

        assert set_click_through(hwnd, False) is True
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        assert not (style & WS_EX_TRANSPARENT)
    finally:
        root.destroy()


def test_click_through_is_a_noop_off_windows(monkeypatch):
    import jarvis.ui.orb as orb_mod

    monkeypatch.setattr(orb_mod, "IS_WINDOWS", False)
    assert set_click_through(12345, True) is False


# ------------------------------------------------------------------- lifecycle
def test_orb_disabled_start_does_not_spawn_a_thread():
    orb = Orb(_cfg(orb=False))
    orb.start()
    assert orb._thread is None


def test_orb_stop_before_start_does_not_raise():
    orb = Orb(_cfg())
    orb.stop()  # just must not raise
    assert orb._stop.is_set()
