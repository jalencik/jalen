"""
"Smooth like iPhone."                                   - his words, 2026-10-23

Three things made the orb jerky that do not need a screen to see
(jalen/ui/orb.py):

  * a change of state swapped the sphere's colour between one frame and the
    next - now it eases there over TRANSITION_S from whatever is on screen,
    and a change in the middle of a change starts from where it is;
  * the listening core jumped with every raw microphone level - now it
    follows the level through LEVEL_SMOOTH_S;
  * after() counts from when it is called, so a frame that took 15 ms to draw
    at a 40 ms interval ran at 55 ms: "25 fps" was nearer 18. Each frame is
    now timed from its own start.

NOT CHECKED FROM HERE: how it looks. There is no display on this machine; the
orb has only ever been seen on his.
"""
from __future__ import annotations

import inspect

import pytest

from jalen.config import CONFIG
from jalen.ui import orb as orb_module
from jalen.ui.orb import LEVEL_SMOOTH_S, TRANSITION_S, Orb, _ease_out, _next_frame_ms


@pytest.fixture
def orb():
    return Orb(CONFIG)          # nothing is drawn until start()


# --------------------------------------------------------------------------
# 1. A CHANGE OF STATE EASES, IT DOES NOT SNAP
# --------------------------------------------------------------------------
def test_the_colour_starts_where_it_was_and_ends_on_the_new_state(orb):
    idle, listening = orb.colours["idle"], orb.colours["listening"]
    orb._note_state("listening", now=100.0)
    assert orb._colour_now(100.0) == idle
    assert orb._colour_now(100.0 + TRANSITION_S) == listening
    halfway = orb._colour_now(100.0 + TRANSITION_S / 2)
    assert halfway not in (idle, listening)


def test_a_change_in_the_middle_of_a_change_starts_from_what_is_on_screen(orb):
    orb._note_state("listening", now=100.0)
    shown = orb._colour_now(100.1)
    orb._note_state("thinking", now=100.1)
    assert orb._colour_now(100.1) == shown, "the colour jumped when the state changed again"
    assert orb._colour_now(100.1 + TRANSITION_S) == orb.colours["thinking"]


def test_the_same_state_again_restarts_nothing(orb):
    orb._note_state("speaking", now=100.0)
    orb._note_state("speaking", now=100.2)
    assert orb._changed_at == 100.0


def test_a_new_orb_shows_its_idle_colour_at_once(orb):
    assert orb._colour_now(0.0) == orb.colours["idle"]


def test_the_easing_is_quick_then_settles():
    samples = [_ease_out(i / 10) for i in range(11)]
    assert samples[0] == 0.0 and samples[-1] == 1.0
    assert samples == sorted(samples)
    assert samples[3] > 0.3, "it should be most of the way there early, as a phone's is"


# --------------------------------------------------------------------------
# 2. THE LISTENING CORE FOLLOWS THE MICROPHONE
# --------------------------------------------------------------------------
def test_the_core_follows_a_jump_in_level_rather_than_jumping(orb):
    orb._level_at, orb._shown_level, orb._level = 100.0, 0.0, 1.0
    one_frame = orb._level_now(100.04)
    assert 0.0 < one_frame < 1.0
    later = orb._level_now(100.0 + 8 * LEVEL_SMOOTH_S)
    assert one_frame < later <= 1.0 and later > 0.99


def test_it_never_overshoots_on_the_way_down(orb):
    orb._level_at, orb._shown_level, orb._level = 100.0, 1.0, 0.2
    values = [orb._level_now(100.0 + 0.04 * i) for i in range(1, 30)]
    assert all(0.2 <= v <= 1.0 for v in values) and values == sorted(values, reverse=True)


# --------------------------------------------------------------------------
# 3. EACH FRAME IS TIMED FROM ITS OWN START
# --------------------------------------------------------------------------
@pytest.mark.parametrize("interval, spent, due", [
    (40, 0.000, 40), (40, 0.015, 25), (40, 0.040, 1), (40, 0.200, 1), (150, 0.010, 140),
])
def test_the_next_frame_is_due_on_time_not_late_by_the_drawing(interval, spent, due):
    assert _next_frame_ms(interval, spent) == due


def test_the_frame_loop_uses_all_three():
    source = inspect.getsource(Orb._run)
    assert "_next_frame_ms(" in source and "time.perf_counter() - started" in source
    assert "_note_state(" in source and 'setattr(self, "_state"' not in source
    assert "self._state = str(value)" not in source
    assert "_changing(" in source, "a colour change at the idle rate would be two frames"


def test_the_frame_rates_are_his_to_change(orb):
    assert (orb._frame_ms, orb._idle_frame_ms) == (
        CONFIG.get_path("ui.frame_ms"), CONFIG.get_path("ui.idle_frame_ms"))
    assert (orb._frame_ms, orb._idle_frame_ms) == (40, 150), "the shipped rates changed silently"


def test_the_constants_carry_their_reasoning():
    source = inspect.getsource(orb_module)
    assert "NOT MEASURED" in source.split("TRANSITION_S = ")[0][-1200:]
