"""
A gate run in the main checkout writes 55 silent test clips into his live
speech cache, and can make it delete real ones.

tests/test_overhaul_fixes.py::test_audio_cache_is_bounded (50469da) builds
Speaker(CONFIG) with the real CACHE_DIR and renders CACHE_MAX_ENTRIES + 15
dummy phrases ("phrase number 0." ...). _render_cached saves each one to
disk. Found 2026-10-01 by Session B: a worktree gate left exactly those 55
.npz files under data/tts_cache (one timestamp), a probe plugin pinned the
first test after which they existed, and a read-only name comparison found
all 55 in the LIVE cache - 55 of its 272 files.

Why it matters beyond clutter:
  * _save_to_disk deletes the 40 OLDEST files once the cache reaches
    DISK_CACHE_MAX (400), so a gate run can evict his real phrases to make
    room for test junk;
  * warmup() decides "already warm" by counting *.npz files (>= 14), which
    the junk alone satisfies.

Its siblings in the same file already redirect CACHE_DIR to tmp_path
(test_overhaul_fixes.py:524, :550, :580); this one predates them. The
robust fix is an autouse redirect in tests/conftest.py, which the Integrator
owns - so this checks the redirect, without writing anything itself.

FAILS ON MAIN BY DESIGN (Session B, found while gating C1).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = (ROOT / "data" / "tts_cache").resolve()


def test_the_speech_cache_the_suite_uses_is_not_the_live_one():
    from jarvis.audio.tts import Speaker

    assert Path(Speaker.CACHE_DIR).resolve() != LIVE, (
        "a Speaker built in a test caches into the live data/tts_cache")
