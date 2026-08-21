"""
Five failures he reported in one message, each pinned to the audit log.

These are not hypothetical. Every one is reproduced from data/audit.jsonl for
21 Aug, and each test names the exact line it came from, because the value of
a regression test is entirely in whether the next person can tell what it is
protecting.
"""
from __future__ import annotations

import pytest

from jarvis.brain.router import IntentRouter, looks_like_a_name
from jarvis.config import CONFIG


@pytest.fixture(scope="module")
def router() -> IntentRouter:
    return IntentRouter(CONFIG)


# ---------------------------------------------------------------------------
# 1. "It is rewriting my thing that I told him rather than executing it."
# ---------------------------------------------------------------------------
THE_REAL_SENTENCE = (
    "I would like you to go to my Gmail and find one random email about my "
    "machine learning community. I would like you to make an extensive "
    "research about the project or research that I have been asked to join. "
    "And I would like you to explain it to me in simple words."
)


def test_a_long_instruction_is_not_mistaken_for_a_window_name(router):
    """
    audit.jsonl 11:41:12 — he said the sentence above. The router matched a
    greedy "go to X" catch-all and called focus_window with 48 words as the
    name, and Jalen replied "I can't find a window called <his own
    instruction>". It read his request back to him instead of doing it, and
    the brain never saw the turn at all.
    """
    hit = router.route(THE_REAL_SENTENCE)
    assert hit is None or hit.tool not in ("focus_window", "open_target", "open_app"), (
        f"still swallowed by {hit.tool} as name={hit.args.get('name')!r}"
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "go to my Gmail and find one random email about my machine learning community",
        "open my gmail and tell me what the professor said about the benchmark",
        "switch to chrome and find the paper I was reading yesterday",
        "open telegram and tell me what I missed in the community",
        "go to youtube and find something calm and put it on for me please",
    ],
)
def test_multi_clause_requests_reach_the_brain(router, phrase):
    """
    Anything with a second clause is an instruction, not a name. The router
    answering it instantly and wrongly is far worse than the brain answering
    it in three seconds and correctly.
    """
    hit = router.route(phrase)
    if hit is not None:
        for key in ("name", "app"):
            assert key not in hit.args, (
                f"{phrase!r} routed to {hit.tool} with {key}={hit.args[key]!r}"
            )


@pytest.mark.parametrize(
    "phrase, tool",
    [
        ("open chrome", "open_target"),
        ("switch to telegram", "focus_window"),
        ("go to youtube", "open_url"),
        ("open vs code", "open_target"),
        ("close spotify", "close_app"),
        ("go to youtube and play dreamcore", "play_on_youtube"),
    ],
)
def test_real_short_commands_still_route_instantly(router, phrase, tool):
    """
    The guard must not cost the router its actual job. These are the turns
    that must never reach an LLM.
    """
    hit = router.route(phrase)
    assert hit is not None, f"{phrase!r} now falls through to Claude — the guard is too strict"
    assert hit.tool == tool


@pytest.mark.parametrize(
    "value, expected",
    [
        ("chrome", True),
        ("vs code", True),
        ("Eco Pulse", True),
        ("Blue Zarafshan web team", True),
        ("my cv", True),
        # Not names.
        ("chrome and find the paper I was reading yesterday", False),
        ("my Gmail and find one random email about my machine learning", False),
        ("please open the thing", False),
        ("chrome. then open telegram", False),
        ("a b c d e f g h", False),
        ("", False),
    ],
)
def test_what_counts_as_a_name(value, expected):
    assert looks_like_a_name(value) is expected


# ---------------------------------------------------------------------------
# 2. "My brain hit an error" — forever, until restart.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "message",
    [
        "Cannot write to terminated process (exit code: 129)",
        "CLIConnectionError: Cannot write to terminated process",
        "BrokenPipeError: [Errno 32] Broken pipe",
        "Process exited unexpectedly",
        "Connection closed by peer",
    ],
)
def test_a_dead_cli_subprocess_is_recognised_as_recoverable(message):
    """
    audit.jsonl 11:32:18 and again at 11:35:00 — the SAME error three
    minutes apart, because nothing reconnected. Exit 129 is SIGHUP: the CLI
    subprocess lost the console window it was given. Every turn in between
    answered "My brain hit an error".
    """
    from jarvis.brain.agent import _looks_like_a_dead_client

    assert _looks_like_a_dead_client(RuntimeError(message)) is True


@pytest.mark.parametrize(
    "message",
    ["invalid tool arguments", "rate limit exceeded", "context length exceeded"],
)
def test_real_errors_are_not_mistaken_for_a_dead_client(message):
    """
    Reconnecting on a genuine error would retry a request that failed for a
    reason retrying cannot fix, and hide the reason while doing it.
    """
    from jarvis.brain.agent import _looks_like_a_dead_client

    assert _looks_like_a_dead_client(RuntimeError(message)) is False


def test_the_brain_reconnects_rather_than_giving_up():
    """The reconnect path has to exist, not just the detection."""
    import inspect

    from jarvis.brain.agent import Brain

    source = inspect.getsource(Brain.ask)
    assert "_restart_client" in source, "a dead client is never replaced"
    assert hasattr(Brain, "_restart_client")


class _ExplodingBrain:
    """A brain whose CLI subprocess has died."""

    def __init__(self, message):
        self.message = message
        self.stopped = False

    async def ask(self, text, on_text=None):
        raise RuntimeError(self.message)

    async def stop(self):
        self.stopped = True


class _BareApp:
    """Only what handle_with_brain touches."""

    def __init__(self, brain):
        self.brain = brain

        class Audit:
            def error(self, *a, **k):
                pass

        self.audit = Audit()

    handle_with_brain = None  # bound below


def _ask(app, text="hello"):
    import asyncio

    from jarvis.app import Jalen

    return asyncio.new_event_loop().run_until_complete(
        Jalen.handle_with_brain(app, text)
    )


def test_a_dead_client_is_reset_and_said_in_plain_words():
    """
    The behaviour, not the source text. A dead subprocess must (a) drop the
    client so the NEXT turn reconnects instead of repeating the error, and
    (b) be described in words a person can act on — this string is read
    aloud by a TTS engine.
    """
    brain = _ExplodingBrain("Cannot write to terminated process (exit code: 129)")
    app = _BareApp(brain)

    reply = _ask(app)

    assert app.brain is None, "the dead client was kept, so the next turn fails identically"
    assert brain.stopped is True
    assert "terminated process" not in reply
    assert "exit code" not in reply
    assert "say that again" in reply.lower()


def test_an_ordinary_error_keeps_the_client_and_stays_readable():
    """
    Not every failure is a dead subprocess. A real error must not throw away
    a working connection — and still must not read a traceback out loud.
    """
    brain = _ExplodingBrain("rate limit exceeded")
    app = _BareApp(brain)

    reply = _ask(app)

    assert app.brain is brain, "a working client was discarded over an ordinary error"
    assert "rate limit exceeded" not in reply
    assert "RuntimeError" in reply


# ---------------------------------------------------------------------------
# 3. The Claude CLI console window.
# ---------------------------------------------------------------------------
def test_the_cli_subprocess_gets_no_console_window():
    """
    The window he complained about IS the CLI's console — and closing it is
    what sent SIGHUP and killed the brain for the rest of the session. The
    two complaints are one bug.
    """
    import sys

    import anyio

    from jarvis.brain.agent import suppress_cli_console_window

    suppress_cli_console_window()
    if sys.platform != "win32":
        pytest.skip("Windows-only behaviour")
    assert getattr(anyio.open_process, "_jalen_no_window", False), (
        "anyio.open_process is unpatched — the CLI will open a console window"
    )

    # Idempotent: start() calls it on every reconnect.
    suppress_cli_console_window()
    suppress_cli_console_window()
    assert getattr(anyio.open_process, "_jalen_no_window", False)


def test_brain_start_suppresses_the_window_before_spawning():
    import inspect

    from jarvis.brain.agent import Brain

    source = inspect.getsource(Brain.start)
    assert "suppress_cli_console_window()" in source
    assert source.index("suppress_cli_console_window()") < source.index("ClaudeSDKClient")


# ---------------------------------------------------------------------------
# 4. "I didn't catch that", mid-task, unprompted.
# ---------------------------------------------------------------------------
def test_barge_in_on_noise_does_not_announce_a_failure_to_understand():
    """
    audit.jsonl 11:42:53 — a 20-second reply finished and "I didn't catch
    that" was logged in the same second. Barge-in fires on any SOUND over
    the threshold, including Jalen's own voice returning through the mic, so
    it was setting wake_initiated=True and then announcing a failure to
    understand something nobody had said.
    """
    import inspect
    import re

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen.run)
    block = re.search(
        r"if barge_in and self\.speaker\.speaking:.*?continue", source, re.S
    )
    assert block, "the barge-in branch has moved — re-check this guard"
    assert "wake_initiated = True" not in block.group(0), (
        "barge-in marks the turn as user-initiated again, so room noise during a "
        "reply will announce 'I didn't catch that' at a silent room"
    )


# ---------------------------------------------------------------------------
# 5. "Play X on YouTube" typed into the search bar and wiped it.
# ---------------------------------------------------------------------------
def test_playing_a_video_sends_no_keystrokes():
    """
    It used to open the results page then send six Tabs and an Enter, hoping
    focus had landed on the first result. Focus was usually the YouTube
    search box, so the Tabs walked the header and Enter re-submitted the
    search — he saw it type into the search bar and wipe it.

    Guessing at unobservable focus cannot be made reliable. The video id is
    read from the results HTML and /watch?v=<id> is opened directly.
    """
    import inspect

    from jarvis.tools.web import play_on_youtube

    source = inspect.getsource(play_on_youtube)
    assert "SendKeys" not in source, "still typing into whatever happens to have focus"
    assert "watch?v=" in source, "no longer opens the video directly"


def test_the_first_result_parser_ignores_non_video_ids():
    """
    Matching bare "videoId" picks up autoplay hints and sidebar suggestions,
    which is how you play something unrelated to what he asked for. Only a
    videoRenderer is a real search result.
    """
    import inspect

    from jarvis.tools.web import _first_youtube_result

    source = inspect.getsource(_first_youtube_result)
    assert "videoRenderer" in source


def test_no_video_found_does_not_claim_playback():
    """
    Reporting "Playing X" when nothing started is the failure mode this
    project keeps coming back to: a silent omission that reads as success.
    """
    import jarvis.tools.web as web

    original = web._first_youtube_result
    web._first_youtube_result = lambda q: (None, None)
    launched = []
    original_launch = web._launch
    web._launch = launched.append
    try:
        reply = web.play_on_youtube("something unfindable")
    finally:
        web._first_youtube_result = original
        web._launch = original_launch

    assert "Playing" not in reply
    assert "couldn't" in reply.lower()
    assert launched, "it should still open the results page as a fallback"
