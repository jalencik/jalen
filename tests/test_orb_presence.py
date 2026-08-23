"""
The orb has to be visible to do its job.

He reported it as "not appearing". It was appearing — 84 pixels, in a
corner, painted near-black when idle, and click-through so it could not even
be clicked to prove it was there. At that size and colour "not appearing" is
a fair description of what it was doing.

These tests are about the things that made it invisible or unusable, not
about how it looks.
"""
from __future__ import annotations

import pytest

from jarvis.config import CONFIG
from jarvis.ui.orb import MAX_ORB, MIN_ORB, WAVE_HEADROOM, Orb


class FakeRoot:
    """Just the two screen queries _clamp_size and _geometry use."""

    def __init__(self, w=1536, h=864):
        self._w, self._h = w, h

    def winfo_screenwidth(self):
        return self._w

    def winfo_screenheight(self):
        return self._h


def test_it_is_big_and_centred_by_default():
    orb = Orb(CONFIG)
    assert orb.position == "center", "still parked in a corner"
    assert orb.size >= 300, f"still {orb.size}px — that is the size he could not see"


def test_centred_geometry_actually_centres():
    orb = Orb(CONFIG)
    root = FakeRoot(1536, 864)
    geometry = Orb._geometry(orb, root, 700, 700)
    size, x, y = geometry.split("+")[0], int(geometry.split("+")[1]), int(geometry.split("+")[2])
    assert size == "700x700"
    assert abs((x + 350) - 768) <= 2, "not horizontally centred"
    # Deliberately above true centre — see _geometry.
    assert 0 < y < (864 - 700), "vertically off-screen"


@pytest.mark.parametrize(
    "screen_w, screen_h",
    [(1536, 864), (1920, 1080), (3840, 2160), (1280, 720)],
)
def test_the_orb_never_grows_off_the_screen(screen_w, screen_h):
    """
    The bug this caught, on a real 1536x864 display: resizing to 600 produced
    a 1100-pixel-tall window at y = -99, so the top of the orb was off the
    screen entirely. A fixed maximum cannot know the monitor.
    """
    root = FakeRoot(screen_w, screen_h)
    biggest = Orb._clamp_size(root, MAX_ORB * 10)
    window = biggest * WAVE_HEADROOM
    assert window <= min(screen_w, screen_h), (
        f"a {biggest}px orb needs a {window:.0f}px window on a "
        f"{screen_w}x{screen_h} screen"
    )


def test_resizing_stays_within_both_limits():
    root = FakeRoot(1536, 864)
    assert Orb._clamp_size(root, 10) == MIN_ORB, "shrank below legibility"
    assert Orb._clamp_size(root, 100_000) <= MAX_ORB


def test_every_state_has_a_colour():
    """
    A state with no colour falls back to idle, which is near-black — so the
    orb would go dark exactly when it had something to tell him.
    """
    orb = Orb(CONFIG)
    from jarvis.ui.orb import STATES

    for state in STATES:
        assert orb.colours.get(state), f"{state} has no colour"


def test_his_three_colours_are_what_he_asked_for():
    """
    "blue while listening, yellow while executing" — still true.

    The third one, "black when stopped", is NOT. He reversed it: the orb was
    invisible and he asked for it to be visible at all times, in every
    window. Idle is now asserted the other way round, in
    tests/test_orb.py::test_idle_colour_is_visible.
    """
    orb = Orb(CONFIG)

    def channels(hex_colour):
        return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))

    r, g, b = channels(orb.colours["idle"])
    assert r + g + b >= 200, f"idle is {orb.colours['idle']} — too dark to see"

    r, g, b = channels(orb.colours["listening"])
    assert b > r and b > g, f"listening is {orb.colours['listening']} — not blue"

    r, g, b = channels(orb.colours["thinking"])
    assert r > 150 and g > 150 and b < 120, f"thinking is {orb.colours['thinking']} — not yellow"


def test_the_wave_headroom_leaves_room_for_the_rings():
    """
    Rings expand to 1.55x the base radius and anything past the canvas edge
    is clipped square. Headroom below that would slice the waves off flat.
    """
    assert WAVE_HEADROOM >= 1.55


def test_the_orb_has_no_controls_at_all():
    """
    Resizing and moving were removed at his request, after he tried them:
    "make yourself bigger and smaller none of it is working, just forget it
    man... it should stay still in one place, and it should not move, it
    should not be draggable."

    The removal is what makes the cursor guarantee unconditional. With
    nothing on the window meant to be clicked, it is click-through in every
    state, and no sequence of events can make it eat a click again.
    """
    from jarvis.brain.router import IntentRouter
    from jarvis.ui.orb import Orb

    router = IntentRouter(CONFIG)
    for phrase in ("make the orb bigger", "make yourself smaller",
                   "normal size", "move yourself to the top left"):
        hit = router.route(phrase)
        assert hit is None or not hit.tool.startswith("jalen_orb"), (
            f"{phrase!r} still reaches an orb control"
        )
    for gone in ("resize_by", "resize_to", "move_to"):
        assert not hasattr(Orb, gone), f"Orb.{gone} survived the removal"


@pytest.mark.parametrize("phrase", ["smaller", "bigger", "make it bigger"])
def test_resize_does_not_hijack_a_bare_adjective(phrase):
    """
    The follow-up window stays open for twelve seconds after every reply, so
    a rule matching a bare "smaller" would swallow a word meant for whatever
    he was actually talking about.
    """
    from jarvis.brain.router import IntentRouter

    hit = IntentRouter(CONFIG).route(phrase)
    assert hit is None or hit.tool != "jalen_orb_size"


# ---------------------------------------------------------------------------
# WHY THE ORB WAS INVISIBLE.
#
# He reported it twice: "that orb is still not visible on my screen, no matter
# whichever windows I will be". It was never a Z-order problem — the window
# was mapped, on screen, and WS_EX_TOPMOST was set. It was painting nothing.
#
# Measured, on a solid backdrop with the production config:
#     before   0 orb pixels while idle
#     after   ~1,270 orb pixels while idle, click-through still on
# ---------------------------------------------------------------------------
def test_the_window_handle_used_for_styling_is_the_top_level():
    """
    Tk's winfo_id() returns a CHILD window on Windows, not the top-level.
    WS_EX_LAYERED on that child stops the canvas compositing against the
    parent's colour key, so the orb renders nothing at all.
    """
    import inspect

    from jarvis.ui import orb as orbmod

    source = inspect.getsource(orbmod)
    # The two ctypes calls that style the window must never be handed
    # winfo_id() directly.
    assert "set_click_through(root.winfo_id()" not in source
    assert "raise_to_top(root.winfo_id()" not in source
    assert "GetAncestor" in source, "no top-level lookup at all"


def test_an_unrealized_window_reports_no_handle_rather_than_the_wrong_one():
    """
    THE actual failure. tick() runs once directly before mainloop(), when Tk's
    window hierarchy does not exist yet — and GetAncestor on an unrealized
    window returns the handle it was given, which is indistinguishable from
    "this IS the top level".

    So the first tick styled the canvas child, the orb went blank, and because
    _click_through_applied was then True it was never reconsidered for the
    rest of the session. Returning 0 makes the caller wait instead.
    """
    from jarvis.ui.orb import toplevel_hwnd

    class Unrealized:
        def winfo_id(self):
            return 0

    assert toplevel_hwnd(Unrealized()) == 0

    class Exploding:
        def winfo_id(self):
            raise RuntimeError("window does not exist")

    assert toplevel_hwnd(Exploding()) == 0


def test_styling_is_skipped_until_the_handle_resolves():
    """
    No styling is far better than styling the wrong window: one costs a few
    frames of a non-click-through orb, the other costs the whole orb.
    """
    import inspect

    from jarvis.ui import orb as orbmod

    source = inspect.getsource(orbmod.Orb._run)
    assert "if not self._hwnd:" in source
    assert "self.click_through_when_busy and self._hwnd" in source
    assert "self.always_on_top and self._hwnd" in source


def test_topmost_is_re_asserted_rather_than_set_once():
    """
    Windows silently demotes a topmost window when another topmost window, a
    full-screen app or a UAC prompt appears, and there is no event to listen
    for. Setting it once at startup is not "always on top".
    """
    import inspect

    from jarvis.ui import orb as orbmod

    assert orbmod.TOPMOST_REASSERT_TICKS > 0
    source = inspect.getsource(orbmod.raise_to_top)
    assert "SWP_NOACTIVATE" in source, (
        "re-asserting topmost would steal focus from whatever he is typing into"
    )


def test_the_win32_calls_have_prototypes():
    """
    ctypes defaults to a 32-bit signed int return. Window handles on 64-bit
    Windows do not reliably fit, so an unprototyped GetAncestor can return a
    truncated handle that still looks plausible — and every later call then
    styles nothing, or something else.
    """
    from jarvis.ui.orb import _user32

    lib = _user32()
    if lib is None:
        return                       # not Windows; nothing to check
    import ctypes.wintypes as wt
    assert lib.GetAncestor.restype == wt.HWND
    assert lib.SetWindowPos.restype == wt.BOOL


def test_the_orb_carries_its_name_under_it():
    """He asked for the name written below, as in the reference image."""
    orb = Orb(CONFIG)
    assert orb.label
    assert orb.label.lower() == str(CONFIG.get_path("identity.name")).lower()


def test_the_name_follows_a_renamed_assistant():
    """A second user must not stare at somebody else's name on their desktop."""
    from jarvis.config import Cfg

    cfg = Cfg({"identity": {"name": "Ada"}, "ui": {"orb": True}})
    assert Orb(cfg).label == "Ada"
