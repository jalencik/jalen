"""
A Telegram send that timed out was reported to the brain as a failure, and a
failed post is one it sends again.

send_telegram_message and send_telegram_file end in RUNTIME.run(work), which
waits with asyncio.run_coroutine_threadsafe(...).result(60). On timeout that
raises - and the coroutine KEEPS RUNNING on the Telegram loop, so the post can
land a moment later. The exception escaped the tool, and the tool wrapper
(jarvis/brain/tools.py) turned it into "send_telegram_message failed:
TimeoutError". The brain's obvious next move is to send it again: two copies
in his channel, as him. A network error raised AFTER the request went out has
the same shape. drafting.send_posts already knew this for batches; a single
send did not.

THE RULE NOW: what can be known is said. An error before anything was handed
to Telegram is "Nothing sent - ..."; a timeout, or an error after the send was
attempted, is "Not confirmed - ... check before sending it again", which
send_posts already counts as NOT CONFIRMED rather than FAILED.
"""
from __future__ import annotations

import pytest

from jarvis.tools import messaging

from test_telegram_posting import FakeClient, _run


def _wire(monkeypatch, client, *, resolve_raises=None, run_raises=None):
    async def fake_resolve(_client, name):
        if resolve_raises:
            raise resolve_raises
        return client.entity

    monkeypatch.setattr(messaging, "_resolve", fake_resolve)
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    if run_raises:
        def run(work, **_):
            raise run_raises
        monkeypatch.setattr(messaging.RUNTIME, "run", run)
    else:
        monkeypatch.setattr(messaging.RUNTIME, "run", lambda work, **_: _run(work, client))


class _DropsTheLine(FakeClient):
    async def send_message(self, *a, **kw):
        raise ConnectionResetError("connection reset by peer")


def test_a_timeout_says_it_may_have_gone_and_to_check(monkeypatch):
    _wire(monkeypatch, FakeClient(), run_raises=TimeoutError())
    reply = messaging.send_telegram_message(to="ML community", text="hello")
    assert reply.startswith("Not confirmed"), reply
    assert "before sending it again" in reply


def test_a_dropped_line_after_the_send_is_not_confirmed_either(monkeypatch):
    _wire(monkeypatch, _DropsTheLine())
    reply = messaging.send_telegram_message(to="ML community", text="hello")
    assert reply.startswith("Not confirmed"), reply


def test_an_error_before_anything_was_sent_says_nothing_was_sent(monkeypatch):
    _wire(monkeypatch, FakeClient(), resolve_raises=ConnectionResetError("reset"))
    reply = messaging.send_telegram_message(to="ML community", text="hello")
    assert reply.startswith("Nothing sent"), reply


def test_send_posts_counts_it_unconfirmed_not_failed(monkeypatch):
    from jarvis.tools import drafting

    _wire(monkeypatch, FakeClient(), run_raises=TimeoutError())
    result = drafting.send_posts(to="ML community", posts=["one"])
    assert "FAILED" not in result, result
    assert "NOT CONFIRMED (1)" in result


def test_a_file_that_timed_out_is_not_confirmed(monkeypatch, tmp_path):
    from jarvis.tools import attachments

    doc = tmp_path / "poster.pdf"
    doc.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(attachments, "_resolve", lambda _name: (doc, ""))
    _wire(monkeypatch, FakeClient(), run_raises=TimeoutError())
    monkeypatch.setattr(attachments, "RUNTIME", messaging.RUNTIME, raising=False)
    reply = attachments.send_telegram_file(to="ML community", file="poster")
    assert reply.startswith("Not confirmed"), reply


def test_not_being_signed_in_still_raises_the_sign_in_error(monkeypatch):
    """Unchanged: that error carries the fix (connect_telegram.py) and
    proves nothing went out."""
    from jarvis.integrations.telegram_user import TelegramNotConnected

    _wire(monkeypatch, FakeClient(), run_raises=TelegramNotConnected("run connect_telegram.py"))
    with pytest.raises(TelegramNotConnected):
        messaging.send_telegram_message(to="ML community", text="hello")
