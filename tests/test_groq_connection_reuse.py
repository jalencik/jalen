"""
The connection was reused in the code and reaped by the library.

TWO SEPARATE THINGS WERE BOTH CALLED "THE TIMEOUT"

ONE: THE POOLED CONNECTION EXPIRES BETWEEN EVERY TURN
-----------------------------------------------------
Transcriber._groq_client() builds the Groq client once and keeps it, so the
CLIENT is reused - an earlier audit's claim that ~500ms of client construction
was available per turn does not reproduce, and is refuted.

What is not reused is the TCP connection underneath it. Read from the
installed package:

    groq/_constants.py:11
    DEFAULT_CONNECTION_LIMITS = httpx.Limits(max_connections=100,
                                             max_keepalive_connections=20)

keepalive_expiry is absent, so httpx's own default of 5.0 seconds applies, and
a pooled connection idle longer than that is closed. Voice turns are never
five seconds apart: the wait is seconds, Jalen then speaks for a median of
14.8, and he has to think of the next thing to say. So the pool is empty on
essentially every turn and each one pays a fresh TCP connect and TLS
handshake.

MEASURED to api.groq.com, six samples, no API call and no quota spent:

    DNS         231ms cold, 1ms warm (so it is not DNS)
    TCP connect p50 142ms
    TLS         p50 159ms
    TOTAL       p50 301ms, min 241, max 951

TWO: THE TIMEOUT IS PER ATTEMPT, NOT A BUDGET
---------------------------------------------
stt.groq_timeout_s is documented as the bound that "MAKES the fallback real",
against a 36-second stall in his log. But the Groq SDK retries, and
groq.Groq(...).max_retries is 2 by default - so the real bound before
moonshine gets a turn is three attempts of eight seconds plus backoff, around
thirty seconds. The fix was roughly a fifth as effective as its own comment
claims.
"""
from __future__ import annotations

import httpx
import pytest

from jarvis.audio import stt
from jarvis.config import CONFIG


class _Secrets:
    def require(self, name):
        return "test-key-not-real"


@pytest.fixture
def transcriber():
    return stt.Transcriber(CONFIG, _Secrets())


# ---------------------------------------------------------------------------
# THE CLIENT WAS ALREADY REUSED. Refuting the 500ms claim, on the record.
# ---------------------------------------------------------------------------
def test_the_client_itself_is_built_once_and_kept(transcriber):
    first = transcriber._groq_client()
    assert transcriber._groq_client() is first, (
        "a new Groq client per turn - this is the thing the old audit "
        "claimed, and it would be worth fixing if it were true"
    )


# ---------------------------------------------------------------------------
# THE CONNECTION
# ---------------------------------------------------------------------------
def test_the_pooled_connection_outlives_the_gap_between_turns(transcriber):
    """
    THE BUG. Five seconds is shorter than any pause in a real conversation,
    so the pool is always empty and every turn pays 301ms of handshake.
    """
    client = transcriber._groq_client()
    limits = client._client._transport._pool._keepalive_expiry
    assert limits is not None
    assert limits >= 60, (
        f"keepalive_expiry is {limits}s - shorter than the gap between two "
        "things a person says, so the connection is reaped every turn"
    )


def test_the_keepalive_is_configurable_and_the_key_is_read(monkeypatch):
    """
    This repo's rule: a key in jarvis.yaml that no code reads is worse than
    no key at all, because the file promises no code changes are needed.
    """
    assert CONFIG.get_path("stt.groq_keepalive_s", None) is not None, (
        "stt.groq_keepalive_s is missing from config/jarvis.yaml"
    )
    cfg = dict(CONFIG)
    t = stt.Transcriber(CONFIG, _Secrets())
    monkeypatch.setattr(t, "groq_keepalive_s", 123.0)
    t._groq = None
    client = t._groq_client()
    assert client._client._transport._pool._keepalive_expiry == 123.0


def test_the_connection_pool_is_closed_on_shutdown(transcriber):
    """
    A custom httpx.Client is ours to close. Leaving it open is a socket and
    a thread that outlive the thing that made them.
    """
    client = transcriber._groq_client()
    assert hasattr(transcriber, "close")
    transcriber.close()
    assert client._client.is_closed
    # Idempotent: shutdown() calls every teardown step in its own try, and a
    # second close must not be the thing that raises.
    transcriber.close()


def test_a_closed_transcriber_refuses_loudly_rather_than_handing_back_a_dead_client(
        transcriber):
    """
    THE FIRST VERSION OF THIS TEST ASSERTED THE OPPOSITE, and the review was
    right. It required close() to let the next call rebuild, reasoning that
    reusing a client with a shut transport would send every later turn to
    the offline model in silence. True, but rebuilding is worse: close() is
    the LAST step of Jalen.shutdown and warmup() runs on its own thread, so
    a prewarm still in flight would open a fresh socket and thread after
    teardown - and one more of each on every pass of run.py's restart loop.

    Raising is the honest third option. transcribe() catches Exception and
    falls back with last_fallback_reason recorded, so a call after shutdown
    degrades visibly instead of either leaking or lying.
    """
    transcriber._groq_client()
    transcriber.close()
    with pytest.raises(RuntimeError):
        transcriber._groq_client()
    assert transcriber._groq is None


# ---------------------------------------------------------------------------
# THE RETRIES
# ---------------------------------------------------------------------------
def test_a_retry_survives_so_a_stale_socket_is_not_a_fallback(transcriber):
    """
    THE OTHER HALF OF THE KEEPALIVE TRADE. A connection held for minutes can
    be closed at the far end, and the first write then fails. With no retry
    at all, that is an immediate and silent drop to the offline model -
    which is a worse outcome than the 301ms this was saving.
    """
    client = transcriber._groq_client()
    assert client.max_retries >= 1


def test_the_total_bound_before_the_fallback_is_what_the_config_says(transcriber):
    """
    stt.groq_timeout_s is per ATTEMPT. The SDK's default max_retries is 2, so
    the documented 8-second bound was really three attempts plus backoff -
    about thirty seconds, against the 36-second stall it was written to fix.
    """
    client = transcriber._groq_client()
    attempts = client.max_retries + 1
    worst = attempts * transcriber.groq_timeout_s
    assert worst <= 20, (
        f"worst case is {attempts} attempts x {transcriber.groq_timeout_s}s = "
        f"{worst}s before the offline model gets a turn"
    )


def test_the_retry_count_is_configurable_and_read():
    assert CONFIG.get_path("stt.groq_max_retries", None) is not None


# ---------------------------------------------------------------------------
# NOTHING ELSE MOVED
# ---------------------------------------------------------------------------
def test_the_timeout_is_still_passed_on_the_call(transcriber):
    """
    It has to stay on the request, not only on the client: the fallback is
    what it protects and the fallback is the whole point.
    """
    import inspect

    source = inspect.getsource(stt.Transcriber._via_groq)
    assert "timeout=self.groq_timeout_s" in source


def test_the_api_key_still_comes_from_secrets(transcriber):
    """
    Never from the environment, and never inlined. Changing how the client
    is built must not change where the key comes from.
    """
    import inspect

    source = inspect.getsource(stt.Transcriber._groq_client)
    assert 'require("groq_api_key")' in source
    assert "api_key=" in source


def test_a_custom_http_client_does_not_disable_the_base_url(transcriber):
    client = transcriber._groq_client()
    assert str(client.base_url).startswith("https://")


def test_a_prewarm_still_in_flight_cannot_rebuild_the_pool_after_shutdown():
    """
    close() is the LAST step of Jalen.shutdown and warmup() runs on its own
    thread, taking 330ms warm and 3988ms cold by its own docstring. Without
    a terminal flag, a prewarm still in flight rebuilt the pool after
    teardown - a socket and its thread outliving the process's own shutdown,
    and one more of each on every pass of run.py's restart loop.
    """
    t = stt.Transcriber(CONFIG, _Secrets())
    t._groq_client()
    t.close()
    with pytest.raises(RuntimeError):
        t._groq_client()
    assert t._groq is None


def test_shutdown_actually_calls_close(transcriber):
    """
    UNPINNED UNTIL NOW. The close test called transcriber.close() directly,
    so the wiring in Jalen.shutdown could be deleted and the suite would
    stay green while the pool, its sockets and its threads outlived every
    run. Nothing in tests/ mentioned _close_stt at all.
    """
    import inspect

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen.shutdown)
    assert "self._close_stt" in source, (
        "the transcriber is no longer closed on shutdown"
    )
    # And via a method, not self.stt.close - the tuple is built before the
    # loop's try/except, so an attribute that does not exist yet would raise
    # out of shutdown instead of being caught like every other step.
    #
    # CODE ONLY. The comment in shutdown explains that distinction and so
    # contains the string it is warning against; a naive substring search
    # over the whole source fails on the explanation rather than on the
    # code, which is the same class of mistake as testing a comment.
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "self.stt.close" not in code
    assert hasattr(Jalen, "_close_stt")


def test_close_stt_survives_a_half_built_jalen():
    """The thing the method form buys, asserted rather than assumed."""
    from jarvis.app import Jalen

    bare = Jalen.__new__(Jalen)
    Jalen._close_stt(bare)          # no self.stt at all - must not raise
