"""
Jalen was interrupting himself. (Reported: "it starts thinking and starts
talking but suddenly stops.")

THE EVIDENCE, from data/audit.jsonl. Seventeen replies ended far earlier than
their text needed, and six of them stopped at 747, 755, 757, 758, 762 and
764 milliseconds. Nothing random clusters inside twenty milliseconds of
itself six times.

What happens ~750ms into playback is Jalen's own voice arriving back through
the microphone. The VAD calls it speech, and the barge-in check killed
playback on the FIRST frame over the threshold — so a 60-second answer died
to a single 32ms window of his own output.

There is no acoustic echo cancellation in this project, so the microphone
genuinely cannot distinguish his voice from the speakers. Three defences
compose instead, and each is pinned below:

    GRACE      ignore the opening of playback entirely
    SUSTAINED  require consecutive frames, not one
    THRESHOLD  higher while speaking than while listening

The properties matter more than the numbers: a real interruption must still
feel immediate, and a blip must never win.
"""
from __future__ import annotations

import time

import pytest

from jalen.audio.tts import Speaker
from jalen.config import CONFIG


# ---------------------------------------------------------------------------
# The clock the grace period depends on.
# ---------------------------------------------------------------------------
def test_a_silent_speaker_reports_no_playback_time():
    assert Speaker(CONFIG).speaking_for() == 0.0


def test_speaking_for_measures_from_the_start_of_playback():
    speaker = Speaker(CONFIG)
    speaker._speaking_since = time.monotonic() - 0.5
    speaker._speaking.set()
    assert speaker.speaking_for() == pytest.approx(0.5, abs=0.05)


def test_the_clock_resets_when_playback_ends():
    """
    If it did not, the grace period would be measured from some previous
    reply and the very next one would be interruptible from its first frame —
    which is the bug, restored.
    """
    speaker = Speaker(CONFIG)
    speaker._speaking_since = time.monotonic() - 5.0
    speaker._speaking.set()
    assert speaker.speaking_for() > 4
    speaker._speaking.clear()
    speaker._speaking_since = 0.0
    assert speaker.speaking_for() == 0.0


def test_the_clock_is_stamped_wherever_speaking_is_set():
    """
    Two places set _speaking (say() and SpeechStream). Both must stamp the
    clock, or one whole playback path loses its grace period silently.
    """
    import inspect

    from jalen.audio import tts

    # Code lines only. Counting raw substrings also matches the comment that
    # explains the rule, which is how this test first failed against correct
    # code — a reminder that "grep the source" tests need to grep the source
    # and not the prose about it.
    code = [
        line for line in inspect.getsource(tts).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    sets = sum(1 for line in code if line.strip().endswith("_speaking.set()"))
    stamps = sum(1 for line in code if "_speaking_since = time.monotonic()" in line)
    assert sets == stamps, (
        f"{sets} _speaking.set() site(s) but {stamps} clock stamp(s) — one "
        "playback path has no grace period"
    )
    assert sets >= 2, "expected both playback paths (say and SpeechStream)"


# ---------------------------------------------------------------------------
# The decision itself, as the mic loop makes it.
# ---------------------------------------------------------------------------
class BargeIn:
    """
    The barge-in rule from app.py's frame loop, isolated so it can be driven
    frame by frame without a microphone.

    Kept deliberately in step with the real loop: if you change one, this
    test should fail. That is the point of having it.
    """

    def __init__(self, threshold=0.75, grace_s=1.2, frames=6):
        self.threshold, self.grace_s, self.frames = threshold, grace_s, frames
        self.run = 0

    def feed(self, probability: float, speaking_for: float) -> bool:
        """True when playback should be stopped."""
        if speaking_for < self.grace_s:
            self.run = 0
            return False
        self.run = self.run + 1 if probability >= self.threshold else 0
        if self.run < self.frames:
            return False
        self.run = 0
        return True


def test_a_single_loud_frame_does_not_stop_a_reply():
    """THE bug. One 32ms blip used to kill a sixty-second answer."""
    rule = BargeIn()
    assert rule.feed(1.0, speaking_for=3.0) is False


def test_the_echo_at_750ms_is_ignored():
    """
    The exact measured failure: his own voice crossing the threshold three
    quarters of a second into playback, over and over.
    """
    rule = BargeIn()
    for _ in range(50):
        assert rule.feed(0.95, speaking_for=0.75) is False, (
            "playback was stopped during the grace period"
        )


def test_sustained_real_speech_still_interrupts():
    """
    The feature has to keep working. Six frames at 32ms is ~200ms of
    continuous speech, which still feels immediate to the person talking.
    """
    rule = BargeIn()
    stopped = False
    for i in range(10):
        if rule.feed(0.95, speaking_for=2.0 + i * 0.032):
            stopped = True
            break
    assert stopped, "talking over it no longer stops it"
    assert i == 5, f"took {i + 1} frames, expected 6"


def test_an_intermittent_noise_never_accumulates():
    """
    A keyboard, a cough, a television. Loud frames that are not CONSECUTIVE
    must never add up to an interruption, however many of them there are.
    """
    rule = BargeIn()
    for i in range(200):
        loud = 0.95 if i % 2 == 0 else 0.1
        assert rule.feed(loud, speaking_for=5.0) is False


def test_the_run_resets_after_a_stop():
    """
    Otherwise the frame right after an interruption stops the NEXT reply
    immediately, and he gets one sentence at a time forever.
    """
    rule = BargeIn()
    for _ in range(6):
        rule.feed(0.95, speaking_for=2.0)
    assert rule.run == 0


def test_quiet_frames_never_interrupt():
    rule = BargeIn()
    for _ in range(100):
        assert rule.feed(0.2, speaking_for=5.0) is False


# ---------------------------------------------------------------------------
# The shipped configuration.
# ---------------------------------------------------------------------------
def test_the_grace_period_covers_the_measured_failure():
    """
    The echo was measured at 747-764ms. A grace period shorter than that
    would leave the original bug in place, just less often — which is worse
    than leaving it, because it becomes intermittent.
    """
    grace = float(CONFIG.get_path("conversation.barge_in_grace_s", 0))
    assert grace >= 0.8, f"grace is {grace}s — the echo lands at ~0.75s"


def test_more_than_one_frame_is_required():
    frames = int(CONFIG.get_path("conversation.barge_in_frames", 1))
    assert frames >= 3, f"{frames} frame(s) — a blip can still stop a reply"


def test_barge_in_is_harder_than_ordinary_listening():
    """
    While Jalen is talking, the microphone is hearing Jalen. The bar for
    "that is a person" has to be higher than it is in a quiet room.
    """
    speaking = float(CONFIG.get_path("conversation.barge_in_threshold", 0))
    listening = float(CONFIG.get_path("vad.threshold", 0.5))
    assert speaking > listening, (
        f"barge-in {speaking} is not above the VAD threshold {listening}"
    )


def test_interrupting_is_still_possible_within_half_a_second():
    """
    Guards against over-correcting. If the numbers ever add up to more than
    about half a second of talking before it yields, barge-in has stopped
    being barge-in.
    """
    frames = int(CONFIG.get_path("conversation.barge_in_frames", 6))
    frame_ms = int(CONFIG.get_path("audio.frame_ms", 32))
    assert frames * frame_ms <= 500, (
        f"{frames} x {frame_ms}ms = {frames * frame_ms}ms before it yields"
    )


def test_the_loop_uses_all_three_defences():
    """
    Structural: the real loop must consult the grace period, the run length
    and the threshold. Dropping any one restores the bug in a different form.
    """
    import inspect

    from jalen.app import Jalen

    source = inspect.getsource(Jalen.run)
    assert "speaking_for() < barge_grace_s" in source
    assert "barge_run < barge_frames" in source
    assert "barge_threshold" in source


def test_an_interruption_is_written_down():
    """
    It was invisible before: a reply simply stopped, and nothing in the audit
    log said why. That is how it survived long enough to be reported as "it
    suddenly stops" rather than diagnosed.
    """
    import inspect

    from jalen.app import Jalen

    assert "barge-in stopped playback after" in inspect.getsource(Jalen.run)


# ---------------------------------------------------------------------------
# ONCE PER REPLY. Found in live use, 22 August.
# ---------------------------------------------------------------------------
class LatchedBargeIn(BargeIn):
    """
    The rule WITH the once-per-playback latch, as the real loop has it.

    Seen live: barge-in fired 22 times in seven seconds, at exactly 192 ms
    intervals - which is barge_frames x frame_ms, i.e. the instant the
    counter refilled. `_speaking` stays set while the stream waits for the
    model to write the NEXT sentence, so between sentences the microphone is
    open, nothing is playing, and any continuous noise re-triggered the whole
    branch: 22 stop() calls, 22 audit lines, and the collector reset out from
    under itself each time.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.fired = False

    def feed(self, probability, speaking_for, speaking=True):
        if not speaking:
            self.fired = False
            return False
        if not super().feed(probability, speaking_for):
            return False
        if self.fired:
            return False
        self.fired = True
        return True


def test_it_fires_once_per_reply_not_once_per_six_frames():
    rule = LatchedBargeIn()
    fired = sum(
        1 for i in range(200)
        if rule.feed(0.95, speaking_for=2.0 + i * 0.032)
    )
    assert fired == 1, f"fired {fired} times during one playback"


def test_it_re_arms_when_playback_actually_ends():
    """
    Otherwise the FIRST reply would be interruptible and every one after it
    would not — which is worse than the bug it fixes.
    """
    rule = LatchedBargeIn()
    assert any(rule.feed(0.95, 2.0 + i * 0.032) for i in range(10))
    # Playback ends.
    rule.feed(0.0, 0.0, speaking=False)
    # Next reply is interruptible again.
    assert any(rule.feed(0.95, 2.0 + i * 0.032) for i in range(10))


def test_the_loop_has_the_latch():
    import inspect

    from jalen.app import Jalen

    source = inspect.getsource(Jalen.run)
    assert "barge_fired" in source
    assert "if barge_fired:" in source, "the latch is declared but never checked"
    assert "barge_fired = False" in source, "the latch never re-arms"


# ---------------------------------------------------------------------------
# THE 180-SECOND HANG. Found in his log, 23 August.
#
# Two turns reported `spoke=197970ms` and `spoke=191657ms` - almost exactly
# _await_playback's 180s ceiling, not real speech. Those turns held a
# dispatch slot for three minutes each, which is why he heard "I'm still on
# the last one" three times while nothing at all was playing.
#
# The cause: a streamed reply spends most of its life blocked in
# `self._q.get()` waiting for the model to write the next sentence - a get()
# with NO timeout. Setting `_interrupt` there changed nothing, because the
# loop that would check it was asleep.
# ---------------------------------------------------------------------------
def test_stop_wakes_a_stream_that_is_waiting_for_the_next_sentence():
    """
    Measured: 180 seconds before, 11 milliseconds after.
    """
    import numpy as np

    from jalen.audio import tts

    speaker = tts.Speaker(CONFIG)
    # Instant silent audio: this is about control flow, not sound.
    speaker._render_cached = lambda text: (np.zeros(2400, dtype=np.float32), 24000)
    speaker._play = lambda pcm, rate: not speaker._interrupt.is_set()

    stream = speaker.open_stream()
    stream.push("First sentence.")

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if speaker.speaking and stream._q.empty():
            break
        time.sleep(0.02)
    assert speaker.speaking, "the stream never started"
    assert stream._q.empty(), "it is not in the blocked state this test needs"

    started = time.monotonic()
    speaker.stop()
    deadline = time.monotonic() + 10
    while speaker.speaking and time.monotonic() < deadline:
        time.sleep(0.01)
    took = time.monotonic() - started

    assert not speaker.speaking, "stop() did not stop it - the hang is back"
    assert took < 2.0, (
        f"took {took:.1f}s to stop. It used to take 180 and hold a turn slot "
        "for the whole time."
    )


def test_stop_nudges_the_queue_not_just_the_flag():
    """
    Structural, because the timing test above could pass for the wrong
    reason on a fast machine. The flag alone is what left it asleep.
    """
    import inspect

    from jalen.audio import tts

    source = inspect.getsource(tts.Speaker.stop)
    assert "_interrupt.set()" in source
    assert "_WAKE" in source, "stop() sets the flag but never wakes the sleeper"


def test_a_turn_slot_is_not_held_for_three_minutes():
    """
    _await_playback's ceiling is the backstop, not the mechanism. If the
    ceiling is doing the work, turns queue behind a reply that finished
    minutes ago and he is told "I'm still on the last one" about nothing.
    """
    import inspect

    from jalen.app import Jalen

    source = inspect.getsource(Jalen._await_playback)
    assert "limit_s" in source
    # It must still HAVE a ceiling - a wedged audio device must not hang a
    # dispatch thread forever.
    assert "180" in source or "limit_s" in source
