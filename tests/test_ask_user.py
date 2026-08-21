"""
Asking him something mid-task, and actually waiting.

His requirement, verbatim: "if something it needs it should be able to ask
that thing from me, so that I will answer for example my phone or whatever,
and after asking the question, it might take some time, and it should not
execute anything until I give my answer to it."

Two properties, and the second is the one that matters:

  1. It blocks until he answers.
  2. When he does NOT answer, the caller is told to stop — never handed an
     empty string it might treat as an answer and put in a form.
"""
from __future__ import annotations

import asyncio
import queue
import threading

import pytest

from jarvis.tools import interaction


@pytest.fixture(autouse=True)
def no_stale_callback():
    interaction.uninstall()
    yield
    interaction.uninstall()


# ------------------------------------------------------------- the bridge
def test_without_a_voice_session_it_says_so_rather_than_pretending():
    """
    In text mode, the Telegram bot, or a test there is nobody to ask. A
    caller that believes it asked and got silence fills the field with a
    blank — which is the exact failure this mechanism exists to prevent.
    """
    reply = interaction.ask_user("What's your phone number?")
    assert "no voice session" in reply
    assert "Nothing was filled in or sent" in reply


def test_an_empty_question_is_refused():
    interaction.install(lambda q, t: "should not be reached")
    assert "actual question" in interaction.ask_user("   ")


def test_the_answer_comes_back():
    interaction.install(lambda question, timeout: "+998 90 123 4567")
    assert "+998 90 123 4567" in interaction.ask_user("What's your phone number?")


def test_no_answer_is_reported_as_stop_not_as_an_empty_answer():
    """
    THE LOAD-BEARING ONE. Returning "" would let a caller submit a form with
    a blank field and report success.
    """
    interaction.install(lambda question, timeout: "")
    reply = interaction.ask_user("What's your passport number?")
    assert "stopped rather than guessing" in reply
    assert "Nothing was filled in or sent" in reply


def test_a_failure_to_ask_is_not_silence():
    def explode(question, timeout):
        raise RuntimeError("microphone died")

    interaction.install(explode)
    reply = interaction.ask_user("Anything?")
    assert "RuntimeError" in reply
    assert "Nothing was done" in reply


def test_the_timeout_is_long_enough_to_look_something_up():
    """
    A yes/no gets 20 seconds. Finding a passport number does not, and he
    said so: "it might take some time".
    """
    assert interaction.DEFAULT_TIMEOUT_S >= 120

    seen = {}
    interaction.install(lambda question, timeout: seen.setdefault("t", timeout) or "ok")
    interaction.ask_user("q")
    assert seen["t"] >= 120


# ---------------------------------------------------------- the app side
class FakeSpeaker:
    speaking = False

    def say(self, text):
        pass


class FakeOrb:
    def set_state(self, state):
        pass


class Bare:
    """Only what ask_user touches."""

    def __init__(self):
        self._reply_q: queue.Queue[str] = queue.Queue()
        self._awaiting_reply = False
        self.spoken = []
        self.orb = FakeOrb()

    def say_blocking(self, text):
        self.spoken.append(text)

    _wait_for_reply = None  # bound below


def _make_bare():
    from jarvis.app import Jalen

    bare = Bare()
    bare._wait_for_reply = lambda timeout: Jalen._wait_for_reply(bare, timeout)
    return bare


def test_it_actually_blocks_until_an_answer_arrives():
    from jarvis.app import Jalen

    bare = _make_bare()
    result = {}

    def ask():
        result["answer"] = asyncio.new_event_loop().run_until_complete(
            Jalen.ask_user(bare, "What's your phone number?", timeout_s=5.0)
        )

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()

    # Still waiting a quarter-second later — it has not returned early.
    thread.join(timeout=0.25)
    assert thread.is_alive(), "ask_user returned before he answered"
    assert bare._awaiting_reply is True

    bare._reply_q.put("+998 90 123 4567")
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert result["answer"] == "+998 90 123 4567"
    assert bare._awaiting_reply is False


def test_a_timeout_returns_empty_and_says_so_out_loud():
    from jarvis.app import Jalen

    bare = _make_bare()
    answer = asyncio.new_event_loop().run_until_complete(
        Jalen.ask_user(bare, "What's your passport number?", timeout_s=0.2)
    )
    assert answer == ""
    assert any("No answer" in line for line in bare.spoken)


def test_stale_answers_are_drained_before_the_question():
    """
    Anything already queued predates the question and answers something
    else. Accepting it would put the previous answer into this field.
    """
    from jarvis.app import Jalen

    bare = _make_bare()
    bare._reply_q.put("an answer to an older question")

    result = {}
    thread = threading.Thread(
        target=lambda: result.update(
            a=asyncio.new_event_loop().run_until_complete(
                Jalen.ask_user(bare, "New question?", timeout_s=0.4)
            )
        ),
        daemon=True,
    )
    thread.start()
    thread.join(timeout=3.0)
    assert result["a"] == "", "a stale answer was accepted as the reply"


def test_while_a_question_is_open_everything_he_says_is_the_answer():
    """
    "Open chrome" said in answer to "what's your phone number" is an answer,
    not a command. Routing it would be both wrong and, for some rules,
    irreversible — so this branch sits ahead of the router in process().
    """
    import inspect

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen.process)
    assert source.index("_awaiting_reply") < source.index("self.router.route")


def test_the_mic_stays_open_while_a_question_is_pending():
    """
    Without this he would have to say the wake word again to answer a
    question Jalen just asked him.
    """
    import inspect

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen.run)
    assert "self._awaiting_reply" in source


def test_the_two_answer_queues_are_separate():
    """
    Sharing one queue would let a stray "yes" satisfy "what's your phone
    number", and a phone number satisfy a delete confirmation.
    """
    import inspect

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen.__init__)
    assert "_reply_q" in source and "_answer_q" in source


def test_it_is_reachable_and_ungated():
    """
    Asking a question changes nothing. Gating it would mean a spoken
    confirmation before every question, which is absurd — what he approves
    AFTERWARDS is gated on its own merits.
    """
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    assert "ask_user" in tools.REGISTRY
    assert "ask_user" in TOOL_SPECS
    assert SafetyEngine(CONFIG).classify("ask_user", {}).tier is Tier.GREEN
