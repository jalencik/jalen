"""
List the fake clips a test run left in Jalen's live speech cache - and only those.

    .venv\\Scripts\\python.exe scripts\\find_test_clips_in_speech_cache.py
    .venv\\Scripts\\python.exe scripts\\find_test_clips_in_speech_cache.py --delete

Until 2026-10-01 every gate run wrote 55 silent clips into data\\tts_cache,
because tests/test_overhaul_fixes.py::test_audio_cache_is_bounded built a
Speaker on the real folder and "rendered" the sentences "phrase number 0." to
"phrase number 54." as ten samples of silence (fixed in 78981d7). The cache
keeps 400 files and drops its 40 oldest when full, so the junk pushes out real
phrases and costs a ~3 s network render each time one is needed again.

A file is listed ONLY if both are true:
  1. its name is the cache name Jalen's own Speaker gives one of those 55
     sentences, with this machine's voice, rate and pitch; and
  2. its audio is exactly what the test wrote: 10 samples, all zero, 24 kHz.
A real render is seconds of sound, so nothing Jalen really said can match.

Without --delete this only lists. With --delete it shows the same list and
asks once ("delete these N files?"); nothing is removed without a typed yes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# What the polluting test wrote: tests/test_overhaul_fixes.py at 78981d7^,
# CACHE_MAX_ENTRIES (40) + 15 sentences, each np.zeros(10) at 24000 Hz.
TEST_SENTENCES = tuple(f"phrase number {i}." for i in range(55))
STUB_SAMPLES = 10
STUB_RATE = 24000


def _is_the_test_stub(path: Path) -> bool:
    import numpy as np

    try:
        with np.load(path) as blob:
            audio, rate = blob["audio"], int(blob["rate"])
    except Exception:  # noqa: BLE001 - unreadable is not provably junk; leave it
        return False
    return rate == STUB_RATE and audio.size == STUB_SAMPLES and not np.any(audio)


def find(speaker=None) -> list[Path]:
    """The cache files that are provably the test's stubs, in a stable order."""
    if speaker is None:
        from jarvis.audio.tts import Speaker
        from jarvis.config import CONFIG

        speaker = Speaker(CONFIG)
    found = []
    for sentence in TEST_SENTENCES:
        path = speaker._cache_path(speaker._cache_key(sentence))
        if path.exists() and _is_the_test_stub(path):
            found.append(path)
    return found


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    found = find()
    if not found:
        print("No test clips in the speech cache. Nothing to do.")
        return 0
    size = sum(p.stat().st_size for p in found)
    print(f"{len(found)} test clips in {found[0].parent} ({size / 1024:.1f} KB):")
    for path in found:
        print(f"    {path.name}")
    if "--delete" not in argv:
        print("Nothing was deleted. Run again with --delete to remove exactly these files.")
        return 0
    answer = input(f"Delete these {len(found)} files? Type yes to delete: ").strip().lower()
    if answer != "yes":
        print("Nothing was deleted.")
        return 1
    removed = 0
    for path in found:
        # Re-checked at the moment of deleting: if a file stopped being the
        # stub since it was listed, it is no longer what he approved.
        if path.exists() and _is_the_test_stub(path):
            path.unlink()
            removed += 1
    print(f"Deleted {removed} of the {len(found)} listed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
