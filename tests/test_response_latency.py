"""
The latency work, and the reliability bugs that were being felt AS latency.

Every number quoted here came out of data/audit.jsonl — 2300 records from
real sessions — not from intuition about what ought to be slow.

The three costs on a turn, before:

    endpointing   2000ms   fixed, every turn, even router-only ones
    STT            330ms   warm
    brain          3.0s median / 8.0s p75 / 23s p90, ALL of it silent,
                           because ask() waited for the SDK's ResultMessage
                           before returning a single character to TTS
    TTS           1500ms   for the first sentence, AFTER all of the above

and two bugs that produced dead air indistinguishable from a crash:

    a single 32ms noise blip could hold the microphone for 30 seconds
    "I didn't catch that" was 11.8% of everything Jarvis ever said
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from jarvis.app import is_continuation, looks_unfinished
from jarvis.audio.tts import Speaker, SpeechStream
from jarvis.brain.router import IntentRouter
from jarvis.config import CONFIG


# ===========================================================================
# Endpointing: the transcript decides, not the clock
# ===========================================================================

@pytest.mark.parametrize("phrase", [
    "open chrome and",
    "go to youtube and search for",
    "send a message to",
    "could you please open the",
    "i want you to",
    "delete my",
    "what about the",
    "put it in",
    "i was going to",
    "um",
])
def test_an_unfinished_sentence_keeps_listening(phrase):
    assert looks_unfinished(phrase), (
        f"{phrase!r} is the middle of a sentence. Dispatching it truncates "
        "the command, which is the bug that read as 'it is ignoring me'."
    )


@pytest.mark.parametrize("phrase", [
    "open chrome",
    "what time is it",
    "close notepad",
    "open telegram",
    "mute",
    "what is eating up my disk",
    "play dreamcore on youtube",
    "how much battery do i have left",
    # Words that LOOK like sentence-middles but are how he actually ends
    # questions — six such endings in the log, all complete.
    "what things can you do",
    "what do you do",
    "why is that",
    "delete those",
    "i did not know that",
])
def test_a_finished_command_dispatches_immediately(phrase):
    assert not looks_unfinished(phrase), (
        f"{phrase!r} is complete. Making it wait out the patient threshold "
        "is the 1.4s tax this work exists to remove."
    )


def test_the_measured_majority_takes_the_fast_path():
    """
    55% of the 409 real utterances are six words or fewer. If a meaningful
    share of ordinary short commands read as unfinished, the fast endpoint
    stops being the common case and the optimisation is worthless.
    """
    real_short_commands = [
        "open chrome", "open telegram", "what time is it", "close notepad",
        "mute", "unmute", "open youtube", "what's my battery", "stop",
        "open my cv", "minimize this window", "brief me", "open gmail",
        "what did you do today", "lock the screen", "open vs code",
        "take a screenshot", "empty the recycle bin", "open downloads",
        "play the next song",
    ]
    slow = [c for c in real_short_commands if looks_unfinished(c)]
    assert not slow, f"these ordinary commands would wait out 2s for nothing: {slow}"


# ===========================================================================
# Stitching: putting a split instruction back together
# ===========================================================================

@pytest.mark.parametrize("fragment", [
    "and go to youtube",
    "then close it",
    "also open telegram",
    "and then send it",
    "plus the downloads folder",
])
def test_a_fragment_is_recognised_as_the_rest_of_the_last_command(fragment):
    assert is_continuation(fragment)


@pytest.mark.parametrize("fresh", [
    "open chrome",
    "android studio is broken",   # starts with "an", must not match "and"
    "orange the folder",          # starts with "or", must not match "or"
    "thennewfolder",              # no word boundary
    "what time is it",
])
def test_a_new_command_is_not_mistaken_for_a_fragment(fresh):
    assert not is_continuation(fresh), (
        f"{fresh!r} would be glued onto the previous command and change "
        "what Jarvis was asked to do"
    )


# ===========================================================================
# SpeechStream: talking while the answer is still being written
# ===========================================================================

class _RecordingSpeaker(Speaker):
    """A Speaker that records what it would play instead of touching audio."""

    def __init__(self):
        super().__init__(CONFIG)
        self.played: list[str] = []

    def _render_cached(self, sentence):
        # Stand in for the edge-tts round-trip. Returns a token the fake
        # _play can record, rather than real PCM.
        return (sentence, 24000)

    def _play(self, pcm, rate):
        self.played.append(pcm)
        return True


def test_a_sentence_is_spoken_before_the_reply_is_finished():
    """
    The whole point. Brain.ask() used to return only after ResultMessage,
    so nothing was synthesised until the model had finished everything,
    tool calls included — a median of 3.0s and a p90 of 23s of silence.
    """
    speaker = _RecordingSpeaker()
    stream = speaker.open_stream()

    stream.push("Your C drive is at ninety nine percent. ")
    # The first sentence must reach the speakers while the rest of the
    # answer does not exist yet.
    deadline = time.monotonic() + 5
    while not speaker.played and time.monotonic() < deadline:
        time.sleep(0.01)
    assert speaker.played, "nothing was spoken until the reply was complete"
    assert "ninety nine percent" in speaker.played[0]

    stream.push("Only eight hundred megabytes free.")
    spoken = stream.close(timeout=10)
    assert "eight hundred megabytes" in spoken


def test_half_a_sentence_is_never_sent_to_the_synthesiser():
    """
    Token deltas arrive mid-word. edge-tts renders a fragment with the
    falling intonation of a finished statement, so speaking the buffer as
    it arrives turns one thought into a series of confident non-sequiturs.
    """
    speaker = _RecordingSpeaker()
    stream = speaker.open_stream()

    for chunk in ["Your C ", "drive is ", "at ninety ", "nine per"]:
        stream.push(chunk)
    time.sleep(0.15)
    assert speaker.played == [], (
        "spoke a fragment before the sentence was terminated: "
        f"{speaker.played}"
    )

    stream.push("cent.")
    spoken = stream.close(timeout=10)
    assert spoken == "Your C drive is at ninety nine percent."


def test_the_tail_is_flushed_even_without_a_full_stop():
    """A model that ends without punctuation must not lose its last words."""
    speaker = _RecordingSpeaker()
    stream = speaker.open_stream()
    stream.push("Chrome is open on YouTube")
    spoken = stream.close(timeout=10)
    assert "YouTube" in spoken


def test_speaking_stays_true_across_the_whole_stream():
    """
    app.py's main loop treats `speaker.speaking` as "that is Jarvis's own
    voice, ignore it". If the flag dropped between sentences, the mic would
    hear the tail of his own speech in the gap and treat it as the user
    talking — which is one of the ways the follow-up window kept opening
    on nothing.
    """
    speaker = _RecordingSpeaker()
    seen: list[bool] = []

    original_play = speaker._play

    def watching_play(pcm, rate):
        seen.append(speaker.speaking)
        return original_play(pcm, rate)

    speaker._play = watching_play
    stream = speaker.open_stream()
    stream.push("One. Two. Three. ")
    stream.close(timeout=10)

    assert len(seen) >= 3
    assert all(seen), "speaking flag dropped between sentences"


# ===========================================================================
# Router: phrases that reached Claude for commands it already knew
# ===========================================================================

@pytest.mark.parametrize("spoken", [
    # Every one of these is a real line from data/router_misses.log — it
    # went to the brain, cost a round-trip and tokens, and came back doing
    # what the router could have done in 50ms with none.
    "be so kind as to open telegram",
    "you please open the telegram",
    "yes, open telegram",
    "could you please open telegram",
    "i would like you to open telegram",
    "go ahead and open telegram",
    "yeah, could you please just open telegram",
])
def test_stacked_politeness_no_longer_falls_through_to_claude(spoken):
    router = IntentRouter(CONFIG)
    intent = router.route(spoken)
    assert intent is not None, (
        f"{spoken!r} still reaches the brain — a Claude round-trip for "
        '"open telegram"'
    )
    assert "telegram" in str(intent.args).lower()


def test_politeness_stripping_does_not_eat_a_real_query():
    """
    "search reddit for jarvis" ends on a word the courtesy-stripper knows.
    Removing it leaves the search running on the word "for" — the guard
    against that must survive the new looping strip.
    """
    router = IntentRouter(CONFIG)
    intent = router.route("search reddit for jarvis")
    assert intent is not None
    assert "jarvis" in str(intent.args).lower()


# ===========================================================================
# The false-trigger fix
# ===========================================================================

def test_jarvis_stays_quiet_when_a_noise_opened_the_window():
    """
    "I didn't catch that" was 30 of the 255 things Jarvis said in the audit
    log — 11.8% of its entire spoken output. It fires when a listening
    window produces no speech, and in a follow-up window that window is
    usually opened by a noise, not by him. He never asked anything, so
    there is nothing to catch.

    Worse, the announcement is itself speech, which the microphone hears,
    which can trip the window open again. It fired in the middle of a RED
    delete confirmation in the log — talking over the question it had just
    asked, then cancelling the delete for "no answer".
    """
    import inspect

    from jarvis.app import Jarvis

    source = inspect.getsource(Jarvis.run)
    marker = 'if wake_initiated:\n                        self.say("I didn\'t catch that.")'
    assert marker in source, (
        "the empty-utterance branch must be gated on wake_initiated, or a "
        "room noise makes Jarvis interrupt itself"
    )
    # And every path that opens a listening window must set the flag, or
    # the gate is decided by whatever the previous turn happened to leave.
    assert source.count("wake_initiated = True") >= 3
    assert "wake_initiated = False   # a sound opened this, not him" in source


# ===========================================================================
# The guard that never ran
# ===========================================================================

def test_the_preposition_guard_is_a_real_regex_escape():
    """
    router.py carried a paragraph of comment explaining why "search reddit
    FOR JARVIS" must not have "jarvis" stripped as a trailing address — and
    the guard implementing it contained a literal backspace byte (0x08)
    where the two characters backslash-b belonged.

    A raw string holding a real backspace matches nothing, so the guard
    silently never fired: the phrase stripped to "search reddit for" and
    searched Reddit for the word "for". It was the only control character
    in the codebase, which is exactly why nothing caught it by eye.
    """
    import inspect

    source = inspect.getsource(IntentRouter._normalise)
    control = [c for c in source if ord(c) < 9 or 11 <= ord(c) < 32]
    assert not control, (
        f"control character {[hex(ord(c)) for c in control]} in _normalise — "
        "a regex escape has been written as a raw control byte again"
    )


@pytest.mark.parametrize("spoken,must_survive", [
    ("search reddit for jarvis", "jarvis"),
    ("search youtube for boss", "boss"),
    ("search google for please", "please"),
    ("search reddit for cats", "cats"),
])
def test_a_query_is_not_mistaken_for_trailing_courtesy(spoken, must_survive):
    router = IntentRouter(CONFIG)
    intent = router.route(spoken)
    assert intent is not None, f"{spoken!r} no longer routes at all"
    assert must_survive in str(intent.args).lower(), (
        f"the query lost its object: {intent.args} — the search would run "
        "on the preposition instead of what he asked for"
    )


# ===========================================================================
# Brain streaming, against a stand-in SDK client
# ===========================================================================

def test_the_brain_hands_text_over_as_it_arrives():
    """
    Brain.ask() must call on_text with each delta as it lands, and return
    what was streamed — not the SDK's separate `result` string. If it
    returned `result` instead, app.py would be handed a string that differs
    from the audio already playing and the tail would be spoken twice.
    """
    import asyncio
    import types

    from jarvis.audit import AuditLog
    from jarvis.brain.agent import Brain
    from jarvis.safety import SafetyEngine

    class _Delta:
        def __init__(self, text):
            self.event = {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text},
            }

    class _Result:
        subtype = "success"
        result = "SHOULD NOT BE SPOKEN"
        event = None

    class _FakeClient:
        def __init__(self):
            self.asked = None

        async def query(self, text, **kw):
            self.asked = text

        async def receive_response(self):
            for chunk in ["Chrome is open ", "on YouTube. ", "Anything else?"]:
                yield _Delta(chunk)
            yield _Result()

    async def _confirm(q):
        return True

    async def _announce(t):
        return None

    audit = AuditLog(CONFIG, "test-stream")
    brain = Brain(CONFIG, SafetyEngine(CONFIG), audit,
                  confirm=_confirm, announce=_announce, tools=[])
    brain._client = _FakeClient()

    seen: list[str] = []
    try:
        reply = asyncio.run(brain.ask("open chrome", on_text=seen.append))
    finally:
        audit.close()

    assert seen == ["Chrome is open ", "on YouTube. ", "Anything else?"], (
        "deltas did not reach the callback as they arrived — nothing can be "
        "spoken before the reply is finished"
    )
    assert reply == "Chrome is open on YouTube. Anything else?"
    assert "SHOULD NOT BE SPOKEN" not in reply, (
        "returned the SDK result instead of what was streamed; the tail "
        "would be spoken a second time"
    )
