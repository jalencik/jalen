"""
Text before and after a tool call was glued together with no separator.

WHAT THE LOG SHOWS
------------------
65 of the 828 replies Jalen logged (data/audit.jsonl, read 2026-10-01) have a
full stop jammed against the next capital letter:

    "...can't answer that one from here.Right, no uptime tool here, Boss."
    "...give me one more second.It's still scanning ... "

The brain talks, calls a tool, and talks again. Each stretch of speech is its
own text block in its own model message, and Brain.ask collected the token
deltas of every block with "".join, so the last word of one block touched the
first word of the next. Out loud that is two sentences run together; on the
transcript it is a typo; and when the model says the same sentence before and
after the tool the reply says it twice.

THE FIX
-------
A separator between blocks, never inside one, and a block that exactly repeats
an earlier one is not spoken again. What is handed to on_text (what is SPOKEN)
and what is returned (what is FILED) must stay the same string, which is why
both are tested here.
"""
from __future__ import annotations

import asyncio

from claude_agent_sdk.types import (AssistantMessage, ResultMessage, StreamEvent,
                                    TextBlock, ToolUseBlock)

from jalen.brain.agent import Brain


class _FakeClient:
    def __init__(self, messages):
        self._messages = messages

    async def query(self, text):
        pass

    async def receive_response(self):
        for m in self._messages:
            yield m


def _brain(messages):
    brain = Brain.__new__(Brain)
    brain._lock = asyncio.Lock()
    brain._client = _FakeClient(messages)
    return brain


def _ev(event):
    return StreamEvent(uuid="u", session_id="s", event=event)


def _message_start():
    return _ev({"type": "message_start", "message": {}})


def _text_block_start(index=0):
    return _ev({"type": "content_block_start", "index": index,
                "content_block": {"type": "text", "text": ""}})


def _delta(text, index=0):
    return _ev({"type": "content_block_delta", "index": index,
                "delta": {"type": "text_delta", "text": text}})


def _block_stop(index=0):
    return _ev({"type": "content_block_stop", "index": index})


def _tool_block_start(index=1):
    return _ev({"type": "content_block_start", "index": index,
                "content_block": {"type": "tool_use", "id": "t", "name": "x",
                                  "input": {}}})


def _assistant(*content):
    return AssistantMessage(content=list(content), model="claude-sonnet-5")


def _result():
    return ResultMessage(subtype="success", duration_ms=10, duration_api_ms=5,
                         is_error=False, num_turns=2, session_id="s", result="")


def _spoken_and_returned(messages):
    spoken: list[str] = []
    reply = asyncio.run(_brain(messages).ask("how long has my laptop been on",
                                             on_text=spoken.append))
    return "".join(spoken), reply


def _stream(*blocks_around_a_tool):
    """
    Model message 1: text, then a tool call. Model message 2: text. Exactly the
    order the CLI sends them in, with the token deltas split the way the API
    splits them (several per block).
    """
    first, second = blocks_around_a_tool
    out = [_message_start(), _text_block_start(0)]
    out += [_delta(piece) for piece in first]
    out += [_block_stop(0), _tool_block_start(1), _block_stop(1),
            _assistant(TextBlock(text="".join(first)),
                       ToolUseBlock(id="t", name="log_weakness", input={})),
            _message_start(), _text_block_start(0)]
    out += [_delta(piece) for piece in second]
    out += [_block_stop(0),
            _assistant(TextBlock(text="".join(second))),
            _result()]
    return out


# ---------------------------------------------------------------------------
# THE BUG, with the exact sentences from the log
# ---------------------------------------------------------------------------
def test_text_before_and_after_a_tool_call_is_not_fused():
    spoken, reply = _spoken_and_returned(_stream(
        ["I don't have a tool that reports uptime, Boss ", "- can't answer that one from here."],
        ["Right, no uptime ", "tool here, Boss."]))
    assert "from here.Right" not in reply
    assert reply == ("I don't have a tool that reports uptime, Boss - can't answer "
                     "that one from here. Right, no uptime tool here, Boss.")


def test_what_is_spoken_is_the_same_string_as_what_is_filed():
    """
    on_text feeds the speaker and the reply is what the audit log, the
    transcript and the echo defence compare against. If the separator existed
    in only one of them the address gate would be comparing the microphone
    against words that were never said.
    """
    spoken, reply = _spoken_and_returned(_stream(
        ["Give me one more second."], ["It's still scanning."]))
    assert spoken.strip() == reply
    assert "second. It's" in spoken


def test_a_separator_is_not_doubled_when_the_text_already_has_whitespace():
    _, trailing = _spoken_and_returned(_stream(["First part. "], ["Second part."]))
    assert trailing == "First part. Second part."
    _, leading = _spoken_and_returned(_stream(["First part."], [" Second part."]))
    assert leading == "First part. Second part."


def test_chunks_inside_one_block_are_never_given_a_separator():
    """Only block boundaries get one: 'Hel' + 'lo' is one word."""
    spoken, reply = _spoken_and_returned(_stream(["Hel", "lo wor", "ld."], ["Done."]))
    assert reply == "Hello world. Done."
    assert spoken.strip() == reply


def test_a_stream_without_block_events_still_gets_the_separator():
    """
    An SDK build that sends deltas and whole AssistantMessages but none of the
    block-start events. The whole message ending is itself a boundary.
    """
    messages = [
        _delta("One thing."),
        _assistant(TextBlock(text="One thing."), ToolUseBlock(id="t", name="x", input={})),
        _delta("Another thing."),
        _assistant(TextBlock(text="Another thing.")),
        _result(),
    ]
    spoken, reply = _spoken_and_returned(messages)
    assert reply == "One thing. Another thing."
    assert spoken.strip() == reply


# ---------------------------------------------------------------------------
# AN EXACT REPEAT IS NOT SAID TWICE
# ---------------------------------------------------------------------------
def test_a_block_that_repeats_an_earlier_one_exactly_is_dropped():
    spoken, reply = _spoken_and_returned(_stream(
        ["C is at 95 percent full with 7.8 gigabytes free."],
        ["C is at 95 percent ", "full with 7.8 gigabytes free."]))
    assert reply == "C is at 95 percent full with 7.8 gigabytes free."
    assert spoken.strip() == reply


def test_a_repeat_differing_only_in_case_and_spacing_is_still_a_repeat():
    _, reply = _spoken_and_returned(_stream(
        ["Right, no uptime tool here."], ["right,  no uptime tool   here."]))
    assert reply == "Right, no uptime tool here."


def test_a_block_that_only_starts_like_the_last_one_is_kept_whole():
    """
    The repeat check holds a block back while it still matches an earlier one.
    A block that matches for a while and then goes its own way must come out
    complete and in order, not lose the part that was held.
    """
    spoken, reply = _spoken_and_returned(_stream(
        ["It's 3 PM."], ["It's ", "3 PM", ", Boss, and the battery is full."]))
    assert reply == "It's 3 PM. It's 3 PM, Boss, and the battery is full."
    assert spoken.strip() == reply


def test_a_block_that_is_only_the_start_of_an_earlier_one_is_kept():
    """A proper prefix is not a repeat: nothing may be silently swallowed."""
    _, reply = _spoken_and_returned(_stream(["It's 3 PM, Boss."], ["It's 3 PM"]))
    assert reply == "It's 3 PM, Boss. It's 3 PM"


# ---------------------------------------------------------------------------
# WHAT MUST NOT CHANGE
# ---------------------------------------------------------------------------
def test_a_reply_with_no_tool_call_is_exactly_what_it_was():
    messages = [_message_start(), _text_block_start(0), _delta("Hello"),
                _delta(", Boss."), _block_stop(0),
                _assistant(TextBlock(text="Hello, Boss.")), _result()]
    spoken, reply = _spoken_and_returned(messages)
    assert (spoken, reply) == ("Hello, Boss.", "Hello, Boss.")


def test_a_tool_only_turn_still_falls_back_to_the_finished_blocks():
    """No deltas at all (an SDK that does not stream): the whole-message path."""
    messages = [_assistant(TextBlock(text="First."), ToolUseBlock(id="t", name="x", input={})),
                _assistant(TextBlock(text="Second.")), _result()]
    reply = asyncio.run(_brain(messages).ask("hi"))
    assert reply == "First. Second."


def test_the_whole_message_path_also_drops_an_exact_repeat():
    messages = [_assistant(TextBlock(text="One moment.")),
                _assistant(TextBlock(text="One moment.")),
                _assistant(TextBlock(text="It is 3 PM.")), _result()]
    reply = asyncio.run(_brain(messages).ask("hi"))
    assert reply == "One moment. It is 3 PM."
