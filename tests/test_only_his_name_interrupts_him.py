"""
"While it is working it should be able to hear only my Hey Jalen word, nothing
else should interrupt it."                               - his words, 2026-10-23

Barge-in stopped Jalen mid-sentence on any sustained VOICE over a threshold:
the television, someone else in the room, a cough - and Jalen's own voice
coming back through the microphone, which is what the three dials
(barge_in_threshold, _grace_s, _frames) were tuned against, and the reason
replies used to stop at 750 ms. conversation.barge_in now ships "wake_word":
while he speaks, only the wake word stops him, and the window that opens is
his, as if he had said the name to a silent Jalen. `true` keeps the old
any-voice behaviour, `false` turns it off.
"""
from __future__ import annotations

import pytest

from jalen.config import CONFIG

from test_listening_does_not_obey_other_people_or_itself import Heard, Room, _Speaker, _Wake


class _WakeAt(_Wake):
    """The wake model, firing on its `at`-th frame (1-based) and no other."""

    def __init__(self, at=None):
        super().__init__()
        self.at, self.seen = at, 0

    def feed(self, frame):
        self.seen += 1
        return self.at is not None and self.seen == self.at


def a_room_while_he_speaks(monkeypatch, *, wake_at=None, barge_in="wake_word"):
    r = Room(monkeypatch, [Heard("open chrome")], barge=True, barge_in=barge_in)
    r.jalen.wake = _WakeAt(wake_at)
    return r


def test_the_wake_word_is_what_ships():
    assert CONFIG.get_path("conversation.barge_in") == "wake_word"


def test_a_voice_that_is_not_the_wake_word_does_not_stop_him(monkeypatch):
    """The VAD reads every frame as loud speech; without the name, he talks on."""
    r = a_room_while_he_speaks(monkeypatch, wake_at=None)
    r.run()
    assert r.jalen.speaker.speaking, "something other than his name stopped Jalen mid-sentence"
    assert r.dispatched == []
    assert r.rows("barge-in") == []


def test_the_wake_word_stops_him_and_the_window_is_his(monkeypatch):
    r = a_room_while_he_speaks(monkeypatch, wake_at=3)
    r.run()
    assert not r.jalen.speaker.speaking
    assert r.texts() == ["open chrome"]
    assert r.from_him() == [True], "he said the name: what follows is his"
    (row,) = r.rows("the wake word stopped playback")
    assert row


def test_the_wake_word_stops_him_once_per_reply(monkeypatch):
    r = a_room_while_he_speaks(monkeypatch, wake_at=2)
    r.run()
    assert len(r.rows("the wake word stopped playback")) == 1


def test_the_old_any_voice_barge_in_is_still_there_when_asked_for(monkeypatch):
    r = Room(monkeypatch, [Heard("open chrome")], barge=True, barge_in=True)
    r.jalen.wake = _WakeAt(None)
    r.run()
    assert not r.jalen.speaker.speaking, "barge_in: true no longer lets a voice stop him"


def test_barge_in_off_lets_nothing_stop_him(monkeypatch):
    r = a_room_while_he_speaks(monkeypatch, wake_at=2, barge_in=False)
    r.run()
    assert r.jalen.speaker.speaking


@pytest.mark.parametrize("setting,wake_only,any_voice", [
    ("wake_word", True, False), ("wake-word", True, False), (True, False, True),
    (False, False, False), ("false", False, False), ("off", False, False),
])
def test_the_setting_is_read_as_written(setting, wake_only, any_voice):
    from jalen.app import Jalen

    assert Jalen._barge_mode(setting) == (wake_only, any_voice)


def test_the_wake_word_branch_never_relaxes_the_any_voice_rule():
    """The VAD branch still never claims the window was his (test_reported_failures)."""
    import inspect
    import re

    from jalen.app import Jalen

    source = inspect.getsource(Jalen.run)
    wake = source.index("if barge_on_the_wake_word and self.speaker.speaking:")
    voice = source.index("if barge_in and self.speaker.speaking:")
    assert wake < voice
    block = re.search(r"if barge_in and self\.speaker\.speaking:.*?continue", source, re.S)
    assert "wake_initiated = True" not in block.group(0)


def test_a_silent_speaker_is_unaffected(monkeypatch):
    """Not speaking: the ordinary wake word path, nothing new."""
    r = Room(monkeypatch, [Heard("open chrome")], wake_first=True, barge_in="wake_word")
    r.jalen.speaker = _Speaker(on_air=False)
    r.run()
    assert r.texts() == ["open chrome"]
