"""
How long a question waits - and how long the brain's hook is allowed to.

TWO THINGS THAT MADE ASKING FEEL BROKEN WHERE AN ANSWER IS TYPED
-----------------------------------------------------------------
1. The window was one number, 20 seconds, tuned for a sentence said aloud into
   the room he is standing in. A question that arrives as a Telegram message
   is read when the phone is picked up and answered with a thumb: "No answer,
   so I've cancelled it." came before it could have been seen. Typed modes
   (run.py --text, the Telegram bot) now wait safety.confirm_timeout_typed_s.
   Voice is untouched.

2. Every question the brain asks is asked from INSIDE its PreToolUse hook, and
   the SDK gives a hook 60 seconds (HookMatcher.timeout, "default: 60") unless
   told otherwise. Jalen never told it. One spoken question fits; a limit he is
   asked about followed by the action's own "Confirm?" (jalen/limits.py) does
   not fit with any margin, and a five-minute typed answer cannot fit at all.
   When a hook runs out of time the CLI stops the tool call and hands the
   model an error, so nothing runs unapproved - but the question is cut off
   while Jalen is still asking it, and his answer goes to a call that was
   already refused. The timeout is now sized so it never happens, including
   for an AMBER announcement held for a sentence of his on its way
   (app.ANSWER_HOLD_S).
"""
from __future__ import annotations

import asyncio
import queue
from types import SimpleNamespace

import pytest

from jalen.app import ANSWER_HOLD_S, ConfirmAnswer, Jalen, answer_hold_s
from jalen.audit import AuditLog
from jalen.brain.agent import Brain
from jalen.config import CONFIG, Cfg
from jalen.safety import SafetyEngine


def asking_jalen(mode=None, cfg=CONFIG):
    """A Jalen that can run confirm() and records how long it waited."""
    j = Jalen.__new__(Jalen)
    j.cfg = cfg
    if mode is not None:
        j.mode = mode
    j.said = []
    j.say_blocking = lambda text: j.said.append(text)
    j.orb = SimpleNamespace(set_state=lambda *a: None)
    j.audit = SimpleNamespace(utterance=lambda *a, **k: None, write=lambda *a, **k: None)
    j._answer_q = queue.Queue()
    j._confirm_lock = None
    j._awaiting_confirmation = False
    j._confirm_question = ""
    j._confirm_asked_at = 0.0
    j._confirm_reasked = False
    j.waited = []

    def wait(timeout):
        j.waited.append(timeout)
        return ConfirmAnswer("yes")

    j._wait_for_answer = wait
    return j


def windows(spoken=None, typed=None) -> Cfg:
    safety = {}
    if spoken is not None:
        safety["confirm_timeout_s"] = spoken
    if typed is not None:
        safety["confirm_timeout_typed_s"] = typed
    return Cfg({"safety": safety})


# --------------------------------------------------------------------------
# 1. THE WINDOW FOLLOWS THE CHANNEL
# --------------------------------------------------------------------------
def test_by_voice_the_window_is_still_the_spoken_one():
    assert asking_jalen("voice")._confirm_timeout_s() == float(
        CONFIG.get_path("safety.confirm_timeout_s"))


@pytest.mark.parametrize("mode", ["text", "telegram"])
def test_where_the_answer_is_typed_the_window_is_the_longer_one(mode):
    j = asking_jalen(mode)
    assert j._confirm_timeout_s() == float(CONFIG.get_path("safety.confirm_timeout_typed_s"))
    assert j._confirm_timeout_s() > float(CONFIG.get_path("safety.confirm_timeout_s"))


def test_a_jalen_without_a_mode_is_a_voice_one():
    """Tests (and older callers) build Jalen without __init__; they got 20 seconds before."""
    assert asking_jalen(None)._confirm_timeout_s() == float(
        CONFIG.get_path("safety.confirm_timeout_s"))


def test_the_typed_window_is_never_shorter_than_the_spoken_one():
    """A typo that sets typed below spoken must not make the phone worse than the room."""
    assert asking_jalen("telegram", windows(spoken=30, typed=5))._confirm_timeout_s() == 30.0


def test_both_windows_are_read_from_config_not_hardcoded():
    assert asking_jalen("voice", windows(spoken=12, typed=90))._confirm_timeout_s() == 12.0
    assert asking_jalen("text", windows(spoken=12, typed=90))._confirm_timeout_s() == 90.0
    assert asking_jalen("voice", windows())._confirm_timeout_s() == 20.0       # shipped defaults
    assert asking_jalen("text", windows())._confirm_timeout_s() == 300.0


@pytest.mark.parametrize("mode,expected", [("voice", 20.0), ("telegram", 300.0)])
def test_confirm_really_waits_that_long(mode, expected):
    j = asking_jalen(mode, windows())
    answer = asyncio.run(j.confirm("close app: notepad. Confirm?"))
    assert answer and j.waited == [expected]


def test_silence_still_cancels_and_says_so_in_typed_mode_too():
    j = asking_jalen("telegram")
    j._wait_for_answer = lambda timeout: None
    answer = asyncio.run(j.confirm("close app: notepad. Confirm?"))
    assert not answer and answer.outcome == "timeout"
    assert j.said[-1] == "No answer, so I've cancelled it."


def test_the_shipped_typed_window_says_in_the_design_record_that_it_is_a_guess():
    """
    jalen.yaml's comments ARE the design record (CLAUDE.md: every tuned constant
    carries its measurement). This one has none, and the file has to say so,
    next to the number, where the next person tuning it will read it.
    """
    assert CONFIG.get_path("safety.confirm_timeout_typed_s") == 300
    raw = open("config/jalen.yaml", encoding="utf-8").read()
    comment, _, _rest = raw.partition("confirm_timeout_typed_s: 300")
    assert "NOT MEASURED" in comment[-900:]


# --------------------------------------------------------------------------
# 2. THE HOOK OUTLIVES EVERY QUESTION IT CAN ASK
# --------------------------------------------------------------------------
def a_brain(cfg=CONFIG) -> Brain:
    async def confirm(question):
        return True

    async def announce(text):
        return None

    return Brain(cfg, SafetyEngine(CONFIG), AuditLog(CONFIG, "test-session-hook-timeout"),
                 confirm=confirm, announce=announce)


def longest_question(cfg):
    """What one question can take at its longest, as the brain is told to size it."""
    hold = answer_hold_s(cfg)
    confirm = 2 * (max(float(cfg.get_path("safety.confirm_timeout_s", 20)) + hold,
                       float(cfg.get_path("safety.confirm_timeout_typed_s", 300)))
                   + Brain._QUESTION_SPEECH_S)
    announced = (max(float(cfg.get_path("safety.undo_window_s", 4)) + hold,
                     float(cfg.get_path("safety.undo_window_typed_s", 10)))
                 + Brain._QUESTION_SPEECH_S)
    return max(confirm, announced)


def test_the_hook_timeout_covers_every_question_each_at_its_longest():
    assert Brain._QUESTIONS_PER_CALL == 4      # limit, leash, "Confirm?", consequence
    assert a_brain()._hook_timeout_s() == 4 * longest_question(CONFIG) + 10.0


def test_the_hook_timeout_is_far_past_the_sdks_default_of_sixty_seconds():
    assert a_brain()._hook_timeout_s() > 60.0 * 10


def test_one_slow_question_alone_would_already_have_been_close_to_the_default():
    """The reason the default could not stay: the longest question is 27 s of speech."""
    voice_message_chars = 600
    chars_per_second = 22.4                     # measured, app.speak_brain_reply
    speech = voice_message_chars / chars_per_second
    assert speech + 20.0 > 45.0                 # one question, nowhere near 60 with a second behind it
    assert Brain._QUESTION_SPEECH_S >= speech


def test_the_hook_timeout_follows_config():
    # Announcements kept short here (1 s, and a 9 s hold from a 1 s endpoint),
    # so that the confirmations are the longest wait and the number moves with them.
    cfg = Cfg({"safety": {"confirm_timeout_s": 10, "confirm_timeout_typed_s": 10,
                          "undo_window_s": 1, "undo_window_typed_s": 5},
               "vad": {"silence_ms": 1000}})
    # A "Confirm?" by voice: 10 s, held 9 s, and asked again in full once.
    assert a_brain(cfg)._hook_timeout_s() == 4 * (2 * (10 + 9 + Brain._QUESTION_SPEECH_S)) + 10.0


def test_a_confirm_asked_again_and_held_both_times_still_finishes_inside_the_hook():
    """
    By voice a "Confirm?" waits its 20 s, held up to 12 s for an answer on its
    way, and - when the answer was neither yes nor no - is asked once more in
    full with the whole window again. Sized for one window, the hook was cut
    off in the middle of the question asked again.
    """
    cfg = Cfg({"safety": {"confirm_timeout_s": 20, "confirm_timeout_typed_s": 20,
                          "undo_window_s": 2, "undo_window_typed_s": 5}})
    one_call = 4 * 2 * (20 + ANSWER_HOLD_S + Brain._QUESTION_SPEECH_S)
    assert one_call < a_brain(cfg)._hook_timeout_s()


def test_an_announcement_held_for_his_answer_still_finishes_inside_the_hook():
    """
    By voice an AMBER announcement now waits its window AND a sentence of his on
    its way (ANSWER_HOLD_S): 2 + 12 s as shipped. With confirmations configured
    shorter than that, sizing the hook by the confirmations alone cut a held
    announcement off - three of them did not fit.
    """
    cfg = Cfg({"safety": {"confirm_timeout_s": 5, "confirm_timeout_typed_s": 5,
                          "undo_window_s": 2, "undo_window_typed_s": 5}})
    held = 2 + answer_hold_s(cfg)
    assert held == 2 + ANSWER_HOLD_S
    assert 4 * (held + Brain._QUESTION_SPEECH_S) < a_brain(cfg)._hook_timeout_s()


def test_a_typed_announcement_window_longer_than_any_confirmation_is_covered_too():
    cfg = Cfg({"safety": {"confirm_timeout_s": 5, "confirm_timeout_typed_s": 5,
                          "undo_window_s": 2, "undo_window_typed_s": 90}})
    assert 4 * (90 + Brain._QUESTION_SPEECH_S) < a_brain(cfg)._hook_timeout_s()


def test_the_matcher_carries_the_timeout():
    import claude_agent_sdk

    brain = a_brain()
    matcher = brain._hook_matcher(claude_agent_sdk.HookMatcher)
    assert matcher.timeout == brain._hook_timeout_s()
    assert matcher.matcher is None and len(matcher.hooks) == 1


def test_an_sdk_without_hook_timeouts_still_starts_the_brain():
    """HookMatcher.timeout is newer than the oldest SDK requirements.txt allows."""
    class Old:
        def __init__(self, matcher=None, hooks=None):
            self.matcher, self.hooks = matcher, hooks

    matcher = a_brain()._hook_matcher(Old)
    assert isinstance(matcher, Old) and len(matcher.hooks) == 1


def test_brain_start_passes_the_timeout_on(monkeypatch):
    import claude_agent_sdk

    captured = {}

    class FakeClient:
        def __init__(self, options=None, **_):
            captured["options"] = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)
    brain = a_brain()
    asyncio.run(brain.start())
    (matcher,) = captured["options"].hooks["PreToolUse"]
    assert matcher.timeout == brain._hook_timeout_s()


def test_a_question_that_waits_the_whole_typed_window_still_finishes_inside_the_hook():
    brain = a_brain()
    typed = float(CONFIG.get_path("safety.confirm_timeout_typed_s"))
    # limit question, leash question, the action's own "Confirm?" and its
    # consequence - each of them asked again once, typed answers all the way
    assert 4 * 2 * (typed + Brain._QUESTION_SPEECH_S) < brain._hook_timeout_s()
