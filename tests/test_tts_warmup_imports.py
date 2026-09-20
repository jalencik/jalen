"""
The warmup that exists to pay the cold cost stopped paying the biggest one.

Speaker.warmup() has one job, stated in its own docstring: "pay edge-tts's
first-connection cost up front (measured 4562ms cold vs ~2000ms warm)".
prewarm() runs it in a background thread at startup for exactly that reason.

WHAT IT ACTUALLY SKIPS
----------------------
`import edge_tts` appears at exactly ONE place in jarvis/audio/tts.py - inside
_synthesise. And _synthesise is called from warmup() only here:

    already_warm = len(list(self.CACHE_DIR.glob("*.npz"))) >= max(8, ...)
    if not already_warm:
        asyncio.run(self._synthesise("ready"))

so the moment the phrase cache is populated - which is every run after the
first - warmup() never imports edge_tts at all. The remaining work in warmup()
is _render_cached_quiet over the common phrases, and those all hit the cache,
so they do not import it either.

MEASURED ON THIS MACHINE
------------------------
    data/tts_cache/*.npz          260 files  (threshold is 8)
    import edge_tts, cold         2919ms / 2637ms / 2181ms across three
                                  fresh interpreters

So two and a half seconds are moved off startup, where nobody is waiting, and
onto the first sentence Jalen says that is not a canned phrase - which is the
first real answer of the session, with him sitting in the silence. That is the
exact cost prewarm() was written to eliminate, and the short-circuit that
skips it was added to save a DIFFERENT cost.

THE SHORT-CIRCUIT IS STILL RIGHT ABOUT THE THING IT WAS FOR. Its comment says
"there is nothing to pay when the first thing Jalen says will come off the disk
anyway", and that is true of the network round-trip. It is not true of the
import, which is paid in-process whatever the cache holds. So the probe stays
conditional and the import becomes unconditional.
"""
from __future__ import annotations

import inspect

import pytest

from jarvis.audio import tts


class _Counter:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return object()


@pytest.fixture
def warm_speaker(tmp_path, monkeypatch):
    """
    A Speaker whose cache looks full, with everything that touches the
    network stubbed out. Only the import decision is under test.
    """
    speaker = tts.Speaker.__new__(tts.Speaker)
    speaker.CACHE_DIR = tmp_path
    for i in range(16):
        (tmp_path / f"phrase{i}.npz").write_bytes(b"")
    speaker._COMMON_PHRASES = ("one", "two")
    speaker._render_cached_quiet = lambda sentence: None

    async def _never(text):
        raise AssertionError(
            "warmup made a network round-trip although the cache was full - "
            "that probe is what the already_warm short-circuit is for"
        )

    speaker._synthesise = _never
    return speaker


def test_the_import_is_paid_at_startup_even_when_the_cache_is_full(
        warm_speaker, monkeypatch):
    """
    THE BUG. Two and a half seconds of import moved onto his first real
    answer, by a short-circuit that was only ever meant to skip a network
    probe.
    """
    counter = _Counter()
    monkeypatch.setattr(tts, "_import_edge_tts", counter)
    warm_speaker.warmup()
    assert counter.calls >= 1, (
        "warmup did not import edge_tts - the 2.6s import is still being "
        "paid on the first uncached sentence, which is the first real answer"
    )


def test_a_full_cache_still_skips_the_network_probe(warm_speaker, monkeypatch):
    """
    The other half, and the reason the short-circuit exists. warm_speaker's
    _synthesise raises if it is called at all.
    """
    monkeypatch.setattr(tts, "_import_edge_tts", _Counter())
    warm_speaker.warmup()          # must not raise


def test_there_is_still_exactly_one_place_that_imports_edge_tts():
    """
    The import was moved into a named function so warmup and _synthesise can
    share it. A second bare `import edge_tts` somewhere else would make this
    fix true in one path and false in the other, which is how it got lost in
    the first place.
    """
    source = inspect.getsource(tts)
    bare = [
        line for line in source.splitlines()
        if (line.strip().startswith("import edge_tts")
            or line.strip().startswith("from edge_tts import"))
        and "def _import_edge_tts" not in line
    ]
    assert len(bare) == 1, (
        f"expected one `import edge_tts`, found {len(bare)}: {bare}"
    )
    assert "def _import_edge_tts" in source


def test_synthesise_goes_through_the_same_helper():
    """Otherwise the two paths can disagree about what has been imported."""
    assert "_import_edge_tts()" in inspect.getsource(tts.Speaker._synthesise)
