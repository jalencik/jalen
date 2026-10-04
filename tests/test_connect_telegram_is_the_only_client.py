"""
scripts/connect_telegram.py said "stop Jalen first" and checked nothing.

Run while Jalen was up - which is exactly when someone reconnecting Telegram
would run it - it opened a second Telethon client on the same session file,
the collision that gets a session thrown out. It now takes run.py's own
single-instance lock, so it refuses while Jalen (or the live check) holds it,
and Jalen refuses to start while it runs.

Also: a wrong two-step password ended in a traceback, and "not signed in" did
not say what to type. No real Telegram client is ever built here.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("connect_telegram_under_test",
                                                  ROOT / "scripts" / "connect_telegram.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lock_in_tmp(tmp_path, monkeypatch):
    from jarvis import runtime

    monkeypatch.setattr(runtime, "RUNTIME_DIR", tmp_path)
    for name, leaf in (("LOCK_PATH", "jarvis.lock"), ("STOP_PATH", "stop.request"),
                       ("SIGNAL_PATH", "signal")):
        if hasattr(runtime, name):
            monkeypatch.setattr(runtime, name, tmp_path / leaf)
    return runtime


@pytest.fixture
def no_client(monkeypatch):
    """Any attempt to build a Telethon client fails the test."""
    from jarvis.integrations import telegram_user

    def refuse(*a, **k):
        raise AssertionError("a second Telegram client was opened while Jalen holds the session")

    monkeypatch.setattr(telegram_user, "build_client", refuse)


@pytest.mark.parametrize("argv", [[], ["--status"], ["--logout"]])
def test_it_refuses_while_jalen_holds_the_session(lock_in_tmp, no_client, argv, capsys):
    runtime = lock_in_tmp
    # Jalen is running: the lock names this very (live) process in voice mode.
    assert runtime.acquire("voice") is None
    try:
        code = _load().main(argv)
    finally:
        runtime.release()
    out = capsys.readouterr().out
    assert code == 2
    assert "Jalen is running" in out and "run.py --stop" in out


def test_it_takes_the_lock_and_gives_it_back(lock_in_tmp, monkeypatch):
    runtime = lock_in_tmp
    module = _load()
    seen = {}

    async def fake_status():
        seen["holder"] = runtime.running_instance()
        return 0

    monkeypatch.setattr(module, "_status", fake_status)
    assert module.main(["--status"]) == 0
    assert seen["holder"] is not None and seen["holder"].mode == "telegram-connect"
    assert seen["holder"].pid == os.getpid()
    assert runtime.running_instance() is None, "the lock was not released"


def test_not_signed_in_says_the_exact_command(lock_in_tmp, monkeypatch, capsys):
    from jarvis.integrations import telegram_user

    monkeypatch.setattr(telegram_user, "have_session", lambda: False)
    assert _load().main(["--status"]) == 1
    out = capsys.readouterr().out
    assert "scripts\\connect_telegram.py" in out


def test_a_wrong_two_step_password_is_asked_again_not_a_traceback(lock_in_tmp, monkeypatch, capsys):
    from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError

    from jarvis.integrations import telegram_user

    module = _load()
    passwords = iter(["wrong", "right"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(passwords))
    answers = iter(["+998901234567", "12345"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(telegram_user, "have_session", lambda: False)
    monkeypatch.setattr(telegram_user, "credentials", lambda: (1, "x"))

    class Me:
        first_name, last_name, username, id = "Test", "", "", 1

    class Client:
        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return False

        async def send_code_request(self, phone):
            pass

        async def sign_in(self, phone=None, code=None, password=None):
            if password is None:
                raise SessionPasswordNeededError(request=None)
            if password != "right":
                raise PasswordHashInvalidError(request=None)

        async def get_me(self):
            return Me()

    monkeypatch.setattr(telegram_user, "build_client", lambda **k: Client())
    assert module.main([]) == 0
    out = capsys.readouterr().out
    assert "attempt(s) left" in out
    assert "Signed in as Test" in out
    assert "wrong" not in out and "right" not in out.replace("wasn't right", "")
