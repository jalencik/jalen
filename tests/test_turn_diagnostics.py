"""
A timing line you can act on.

    "Every active turn should have a correlation ID. This lets us diagnose
     future 'it's slow' complaints immediately."

The old line was:

    timing route=router heard=1616ms thought=2662ms wait=4354ms spoke=2050ms

which says he waited 4.3 seconds and does not say why. Measured on this
machine, the two facts that decide almost all of it:

    TTS from the phrase cache        25ms
    TTS from edge-tts             ~3000ms
    STT via Groq, healthy         ~1600ms
    STT fallen back to moonshine   varies, and silently

So "thought=2662ms" is a number to stare at and "thought=2662ms tts=network"
is a diagnosis. These tests hold the line to carrying both, plus an id so a
complaint can point at one turn rather than at a time of day.
"""
from __future__ import annotations

import re

from jarvis.timing import TimingLog, TurnTimer


def _turn(**fields) -> TurnTimer:
    timer = TurnTimer(**fields)
    for mark in ("speech_end", "transcript", "first_audio", "done"):
        timer.mark(mark)
    return timer


class TestTheCorrelationId:

    def test_every_turn_has_one(self):
        assert _turn().turn_id

    def test_it_is_short_enough_to_quote(self):
        turn_id = _turn().turn_id
        assert len(turn_id) == 4, f"{turn_id!r} is not a handle, it's a hash"
        assert re.fullmatch(r"[0-9a-f]{4}", turn_id)

    def test_it_is_stable_across_reads(self):
        """An id that changes when you look at it is not an id."""
        timer = _turn()
        assert timer.turn_id == timer.turn_id == timer.turn_id

    def test_it_appears_in_the_audit_line(self):
        timer = _turn()
        assert f"turn={timer.turn_id}" in timer.summary()

    def test_two_turns_differ(self):
        first, second = _turn(), _turn()
        assert first.turn_id != second.turn_id


class TestTheLineExplainsItself:

    def test_it_names_the_speech_engine(self):
        line = _turn(route="router", stt_engine="groq").summary()
        assert "stt=groq" in line

    def test_it_names_the_audio_source(self):
        line = _turn(route="router", tts_source="network").summary()
        assert "tts=network" in line

    def test_a_silent_fallback_becomes_visible(self):
        """
        The dangerous failure: the transcript is fine, so nothing looks
        wrong, and nobody learns the primary is down.
        """
        line = _turn(route="router", stt_engine="moonshine").summary()
        assert "stt=moonshine" in line

    def test_unknown_fields_are_omitted_not_guessed(self):
        """A turn that never spoke has no audio source. Saying "tts=" would
        read as one that failed, which is a different fact entirely."""
        line = _turn(route="brain").summary()
        assert "tts=" not in line
        assert "stt=" not in line

    def test_the_existing_stages_survive(self):
        line = _turn(route="router", stt_engine="groq",
                     tts_source="cache").summary()
        for stage in ("route=router", "heard=", "thought=", "wait=", "spoke="):
            assert stage in line, f"{stage} disappeared from the timing line"

    def test_a_slow_turn_is_diagnosable_from_one_line(self):
        """The whole point, stated as a test."""
        line = _turn(route="router", stt_engine="groq",
                     tts_source="network").summary()
        assert "tts=network" in line and "stt=groq" in line, (
            "this line still cannot answer 'why was that slow'"
        )


class TestNothingElseBroke:

    def test_the_log_still_records_and_reports(self):
        log = TimingLog()
        log.record(_turn(route="router", stt_engine="groq", tts_source="cache"))
        log.record(_turn(route="brain", stt_engine="groq", tts_source="network"))
        assert log.last is not None
        assert log.median_wait_s("router") is not None
        assert "waited" in log.report()

    def test_wait_and_spoke_stay_separate(self):
        """
        They are different complaints with opposite fixes and adding them
        together is how a length problem gets mistaken for a latency one.
        """
        timer = _turn(route="brain")
        assert timer.wait_s is not None
        assert timer.speaking_s is not None
        assert "wait=" in timer.summary() and "spoke=" in timer.summary()
