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
    "black when stopped, blue while listening, yellow while executing."
    Executing is the `thinking` state — that is what app.py sets while a tool
    call or brain turn is in flight.
    """
    orb = Orb(CONFIG)

    def channels(hex_colour):
        return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))

    r, g, b = channels(orb.colours["idle"])
    assert r + g + b < 150, f"idle is {orb.colours['idle']} — not dark"

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


def test_resize_is_reachable_by_voice():
    """
    The orb is click-through while idle, so it receives no mouse events at
    all — including scroll. Voice is the only control surface that works on
    the state it spends most of its time in.
    """
    from jarvis.brain.router import IntentRouter

    router = IntentRouter(CONFIG)
    bigger = router.route("make the orb bigger")
    smaller = router.route("make the orb smaller")
    assert bigger is not None and bigger.tool == "jalen_orb_size"
    assert bigger.args["delta"] > 0
    assert smaller is not None and smaller.args["delta"] < 0


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
