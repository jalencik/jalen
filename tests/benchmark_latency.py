"""
Measure the response-latency work against the real session log.

    .venv\\Scripts\\python.exe tests\\benchmark_latency.py

Not a pytest file — it reports numbers rather than asserting them, because
the useful output is "how much did this save", not pass/fail. The assertions
that guard the behaviour live in tests/test_response_latency.py.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jalen.app import is_continuation, looks_unfinished          # noqa: E402
from jalen.audio.vad import VAD, UtteranceCollector              # noqa: E402
from jalen.brain.router import IntentRouter                      # noqa: E402
from jalen.config import CONFIG                                  # noqa: E402

AUDIT = ROOT / "data" / "audit.jsonl"
MISSES = ROOT / "data" / "router_misses.log"

BAR = "=" * 74


def user_utterances() -> list[str]:
    out = []
    if not AUDIT.exists():
        return out
    for line in AUDIT.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "utterance" and '"user"' in (row.get("detail") or ""):
            out.append(row.get("summary") or "")
    return [u for u in out if u.strip()]


def endpointing(utterances: list[str]) -> None:
    print(BAR)
    print("ENDPOINTING — the fixed cost paid on every turn")
    print(BAR)

    vad = VAD(CONFIG)
    collector = UtteranceCollector(CONFIG, vad)
    fast, patient = collector.fast_silence_ms, collector.patient_silence_ms

    unfinished = [u for u in utterances if looks_unfinished(u)]
    n = len(utterances) or 1
    slow_share = len(unfinished) / n

    before = patient
    after = fast + slow_share * patient
    print(f"  fast endpoint            {fast:>6} ms")
    print(f"  patient endpoint         {patient:>6} ms")
    print(f"  utterances needing it    {len(unfinished):>6} of {n}  ({slow_share*100:.1f}%)")
    print()
    print(f"  before, every turn       {before:>6.0f} ms")
    print(f"  after, weighted average  {after:>6.0f} ms")
    print(f"  saved per turn           {before-after:>6.0f} ms   "
          f"({(1-after/before)*100:.0f}% of the endpointing cost)")
    if unfinished:
        print("\n  the ones that still wait (correctly):")
        for u in unfinished[:5]:
            print(f"    ...{u.strip()[-58:]!r}")


def noise_blip_hang() -> None:
    print()
    print(BAR)
    print("NOISE-BLIP HANG — one 32ms click used to hold the mic for 30s")
    print(BAR)

    class _BlipVAD(VAD):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.calls = 0

        def is_speech(self, frame):
            self.calls += 1
            return self.calls == 1

        def reset(self):
            pass

    collector = UtteranceCollector(CONFIG, _BlipVAD(CONFIG))
    frame = np.zeros(512, dtype=np.float32)
    released_at = None
    for i in range(int(collector.max_s * 1000 / collector.frame_ms) + 10):
        if collector.feed(frame) is not None:
            released_at = (i + 1) * collector.frame_ms
            break

    old = collector.max_s * 1000
    print(f"  before   {old:>7.0f} ms   (waited out max_utterance_s)")
    print(f"  after    {released_at:>7} ms   (no_speech_timeout_ms)")
    print(f"  saved    {old - (released_at or 0):>7.0f} ms on every false trigger")


def router_hit_rate() -> None:
    print()
    print(BAR)
    print("ROUTER — phrases that reached Claude for commands it already knew")
    print(BAR)

    if not MISSES.exists():
        print("  no router_misses.log yet")
        return

    phrases = [p.strip() for p in MISSES.read_text(encoding="utf-8", errors="replace").splitlines() if p.strip()]
    router = IntentRouter(CONFIG)
    rescued = [p for p in phrases if router.route(p) is not None]

    print(f"  recorded misses          {len(phrases):>6}")
    print(f"  now handled locally      {len(rescued):>6}  ({len(rescued)/max(len(phrases),1)*100:.1f}%)")
    print("  each one saved a Claude round-trip and its tokens.")
    if rescued:
        print("\n  rescued (first 12):")
        seen = set()
        for p in rescued:
            if p in seen:
                continue
            seen.add(p)
            print(f"    {p[:66]!r}")
            if len(seen) >= 12:
                break


def speech_shape(utterances: list[str]) -> None:
    print()
    print(BAR)
    print("STITCHING — fragments that would be meaningless on their own")
    print(BAR)
    frags = [u for u in utterances if is_continuation(u)]
    print(f"  utterances opening on a connector  {len(frags)}")
    for f in frags[:6]:
        print(f"    {f.strip()[:64]!r}")
    if not frags:
        print("    (none in this log — the guard is for splits the fast")
        print("     endpoint can newly create, which the old 2s could not)")


def tts_cache() -> None:
    print()
    print(BAR)
    print("TTS PHRASE CACHE — the ack line must itself be instant")
    print(BAR)
    from jalen.audio.tts import Speaker

    speaker = Speaker(CONFIG)
    for phrase in ("Give me a second.", "That's the short version, the full text is on screen."):
        listed = phrase in speaker._COMMON_PHRASES
        print(f"  pre-rendered at startup: {listed!s:<5}  {phrase!r}")
    print("  a filler that took 1.5s to synthesise would just move the silence.")


def main() -> None:
    utterances = user_utterances()
    print()
    print(f"source: {AUDIT.name} — {len(utterances)} real user utterances")
    print()
    endpointing(utterances)
    noise_blip_hang()
    router_hit_rate()
    speech_shape(utterances)
    tts_cache()
    print()
    print(BAR)
    print("Time-to-first-audio on a brain turn is not measurable offline —")
    print("it depends on the model. Structurally it moved from 'after the")
    print("whole reply plus a TTS round-trip' to 'after the first sentence',")
    print("because Brain.ask() now streams deltas into a SpeechStream.")
    print(BAR)


if __name__ == "__main__":
    main()
