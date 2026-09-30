"""
The brain has been signed out for ten days, and Jalen has been reading the
error message aloud as if it were an answer.

WHAT HAPPENS TODAY
------------------
When the Claude sign-in has expired, the CLI does not raise. It sends back a
SYNTHETIC assistant message - model "<synthetic>", error
"authentication_failed", text "Failed to authenticate: OAuth session expired
and could not be refreshed" - followed by a ResultMessage with is_error=True.
Brain.ask read only block.text and the result string, so that sentence was
returned as Jalen's reply:

    spoken verbatim, every single turn
    logged as an ordinary utterance, not as an error
    the task marked COMPLETED

Verified live on 2026-09-30 against the real bundled claude.exe:

    start() returned after 9003ms
    ask() -> 'Failed to authenticate: OAuth session expired and could not be
              refreshed'  (400ms)

and in data/audit.jsonl, session b4d9a80bb9ab: four identical replies in 68
seconds, kind=utterance, outcome=null, no error row.

THE FIX IS TO READ THE FIELDS THE SDK ALREADY TYPES
---------------------------------------------------
AssistantMessage.error is a Literal - authentication_failed, billing_error,
rate_limit, invalid_request, server_error, unknown - and ResultMessage
carries is_error, subtype and terminal_reason. Matching on the sentence
instead would be brittle: the CLI has already worded the same failure two
different ways ("401 OAuth access token has been revoked" on 22 August).

WHAT HE SHOULD EXPERIENCE
-------------------------
Told ONCE, in a sentence he can act on. After that, a short reminder that
costs no round-trip - and Jalen tries the real brain again by itself when he
has signed back in, rather than needing a restart to notice.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

from jarvis.brain import agent as agent_mod
from jarvis.brain.agent import Brain, BrainUnavailable, _looks_like_a_dead_client


# ---------------------------------------------------------------------------
# A fake SDK client that replays exactly the messages the CLI sends.
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self, messages):
        self._messages = messages
        self.queries = []

    async def query(self, text):
        self.queries.append(text)

    async def receive_response(self):
        for m in self._messages:
            yield m


def _assistant(text="", error=None):
    return AssistantMessage(content=[TextBlock(text=text)] if text else [],
                            model="<synthetic>" if error else "claude-sonnet-5",
                            error=error)


def _result(subtype="success", is_error=False, result=None, num_turns=1,
            terminal_reason=None):
    return ResultMessage(subtype=subtype, duration_ms=10, duration_api_ms=5,
                         is_error=is_error, num_turns=num_turns,
                         session_id="s", result=result,
                         terminal_reason=terminal_reason)


def _brain_with(messages):
    brain = Brain.__new__(Brain)
    brain._lock = asyncio.Lock()
    brain._client = _FakeClient(messages)
    return brain


AUTH_TEXT = "Failed to authenticate: OAuth session expired and could not be refreshed"


def _ask(brain, text="what's the time", on_text=None):
    return asyncio.run(brain.ask(text, on_text=on_text))


# ---------------------------------------------------------------------------
# CLASSIFICATION, on the typed fields
# ---------------------------------------------------------------------------
def test_an_expired_sign_in_is_an_error_and_not_an_answer():
    """THE BUG, replayed with the exact message shape the live CLI sent."""
    brain = _brain_with([
        _assistant(AUTH_TEXT, error="authentication_failed"),
        _result(is_error=True, result=AUTH_TEXT, terminal_reason="api_error"),
    ])
    with pytest.raises(BrainUnavailable) as caught:
        _ask(brain)
    assert caught.value.kind == "authentication_failed"


def test_the_old_wording_of_the_same_failure_is_caught_too():
    """
    22 August's version said '401 OAuth access token has been revoked'.
    Matching on words would have caught one of the two and missed the
    other; the typed field catches both.
    """
    brain = _brain_with([
        _assistant("API Error: 401 OAuth access token has been revoked.",
                   error="authentication_failed"),
        _result(is_error=True),
    ])
    with pytest.raises(BrainUnavailable) as caught:
        _ask(brain)
    assert caught.value.kind == "authentication_failed"


@pytest.mark.parametrize("kind", ["billing_error", "rate_limit", "server_error",
                                  "invalid_request", "unknown"])
def test_every_typed_failure_is_classified(kind):
    brain = _brain_with([_assistant("x", error=kind), _result(is_error=True)])
    with pytest.raises(BrainUnavailable) as caught:
        _ask(brain)
    assert caught.value.kind == kind


def test_the_usage_limit_keeps_the_reset_time_the_cli_gave():
    """
    The CLI's own sentence for a usage limit says WHEN it resets - "resets
    6:40pm (Asia/Tashkent)" is in his log. That is the one piece of
    information he needs, so it is kept rather than replaced.
    """
    detail = "You've hit your session limit - resets 6:40pm (Asia/Tashkent)"
    brain = _brain_with([_assistant(detail, error="rate_limit"),
                         _result(is_error=True)])
    with pytest.raises(BrainUnavailable) as caught:
        _ask(brain)
    assert "6:40pm" in caught.value.detail


def test_a_result_flagged_as_an_error_is_caught_even_without_the_assistant_flag():
    brain = _brain_with([_result(is_error=True, result="boom",
                                 terminal_reason="api_error")])
    with pytest.raises(BrainUnavailable):
        _ask(brain)


def test_the_new_error_does_not_look_like_a_dead_client():
    """
    _looks_like_a_dead_client triggers a client restart. If this error's
    text contained any of its signs, every turn would restart the CLI and
    fail again - a loop that costs nine seconds a time.
    """
    for kind in ("authentication_failed", "rate_limit", "server_error"):
        exc = BrainUnavailable(kind, "detail")
        assert not _looks_like_a_dead_client(exc), kind


# ---------------------------------------------------------------------------
# SILENT FAILURES, which the same reading fixes
# ---------------------------------------------------------------------------
def test_running_out_of_steps_with_nothing_said_is_not_silence():
    """
    A tool-only turn that hit max_turns returned '', and say('') returns
    before speaking or logging anything. The long research turns he asks
    for are exactly the ones that hit the limit.
    """
    brain = _brain_with([_result(subtype="error_max_turns", is_error=True,
                                 num_turns=30)])
    reply = _ask(brain)
    assert reply.strip(), "a turn that stopped early said nothing at all"
    assert "step" in reply.lower()


def test_an_execution_error_with_nothing_said_is_not_silence():
    brain = _brain_with([_result(subtype="error_during_execution",
                                 is_error=True)])
    reply = _ask(brain)
    assert reply.strip()


def test_a_turn_cut_short_after_speaking_says_so():
    """
    Report done and not-done in the same breath: when part of the answer
    was already spoken, he is told it may be incomplete, through the same
    stream he is listening to - not in a return value nobody reads.
    """
    from claude_agent_sdk.types import StreamEvent

    spoken = []
    delta = StreamEvent(uuid="u", session_id="s", parent_tool_use_id=None,
                        event={"type": "content_block_delta",
                               "delta": {"type": "text_delta",
                                         "text": "Here is the first half."}})
    brain = _brain_with([delta, _result(subtype="error_max_turns",
                                        is_error=True, num_turns=30)])
    reply = _ask(brain, on_text=spoken.append)
    assert "first half" in reply
    assert any("incomplete" in s.lower() or "stopped" in s.lower()
               for s in spoken), spoken


def test_a_normal_turn_is_unchanged():
    brain = _brain_with([_assistant("It's ten past four."),
                         _result(result="It's ten past four.")])
    assert _ask(brain) == "It's ten past four."


# ---------------------------------------------------------------------------
# THE APP: tell him once, then remember
# ---------------------------------------------------------------------------
class _Audit:
    def __init__(self):
        self.errors = []

    def error(self, where, exc):
        self.errors.append(where)

    def write(self, *a, **k):
        pass


class _DownBrain:
    def __init__(self, kind="authentication_failed", detail=AUTH_TEXT):
        self.calls = 0
        self.kind = kind
        self.detail = detail

    async def ask(self, text, on_text=None):
        self.calls += 1
        raise BrainUnavailable(self.kind, self.detail)


def _app(brain, creds_file):
    from jarvis.app import Jalen

    app = Jalen.__new__(Jalen)
    app.brain = brain
    app.audit = _Audit()
    app._brain_down = None
    app._brain_down_at = 0.0
    app._brain_down_told = False
    app._credentials_file = creds_file
    app._credentials_seen = app._credentials_stamp()
    return app


def test_the_first_turn_after_sign_in_expires_says_what_to_do(tmp_path):
    app = _app(_DownBrain(), tmp_path / ".credentials.json")
    reply = asyncio.run(app.handle_with_brain("what's the weather"))
    assert "sign" in reply.lower()
    assert "jalen check" in reply.lower()
    assert "Failed to authenticate" not in reply, "the raw CLI text was spoken"
    assert app.audit.errors, "an expired sign-in was not recorded as an error"


def test_later_turns_get_a_short_reminder_without_asking_claude_again(tmp_path):
    brain = _DownBrain()
    app = _app(brain, tmp_path / ".credentials.json")
    first = asyncio.run(app.handle_with_brain("one"))
    second = asyncio.run(app.handle_with_brain("two"))
    assert brain.calls == 1, "every turn paid another round-trip to learn the same thing"
    assert second and len(second) < len(first)


def test_signing_back_in_is_noticed_without_a_restart(tmp_path):
    """
    Logging in rewrites ~/.claude/.credentials.json. That is the moment to
    try again - not ten minutes later, and not only after a restart.
    """
    creds = tmp_path / ".credentials.json"
    creds.write_text("{}")
    brain = _DownBrain()
    app = _app(brain, creds)
    asyncio.run(app.handle_with_brain("one"))
    assert brain.calls == 1

    later = time.time() + 5
    os.utime(creds, (later, later))
    asyncio.run(app.handle_with_brain("two"))
    assert brain.calls == 2, "a fresh sign-in was not tried"


def test_the_latch_is_retried_eventually_even_with_no_file_change(tmp_path):
    from jarvis.app import Jalen

    brain = _DownBrain()
    app = _app(brain, tmp_path / ".credentials.json")
    asyncio.run(app.handle_with_brain("one"))
    app._brain_down_at -= Jalen.BRAIN_RETRY_S + 1
    asyncio.run(app.handle_with_brain("two"))
    assert brain.calls == 2


def test_a_successful_turn_clears_the_latch(tmp_path):
    class _Works:
        async def ask(self, text, on_text=None):
            return "fine"

    app = _app(_Works(), tmp_path / ".credentials.json")
    app._brain_down = "authentication_failed"
    app._brain_down_at = time.monotonic() - 10_000
    assert asyncio.run(app.handle_with_brain("hi")) == "fine"
    assert app._brain_down is None


def test_a_server_error_is_not_latched(tmp_path):
    """Claude's servers having a bad minute is not a reason to stop trying."""
    brain = _DownBrain(kind="server_error", detail="overloaded")
    app = _app(brain, tmp_path / ".credentials.json")
    asyncio.run(app.handle_with_brain("one"))
    asyncio.run(app.handle_with_brain("two"))
    assert brain.calls == 2


def test_the_usage_limit_sentence_carries_the_reset_time(tmp_path):
    detail = "You've hit your session limit - resets 6:40pm (Asia/Tashkent)"
    app = _app(_DownBrain(kind="rate_limit", detail=detail),
               tmp_path / ".credentials.json")
    reply = asyncio.run(app.handle_with_brain("one"))
    assert "6:40pm" in reply
