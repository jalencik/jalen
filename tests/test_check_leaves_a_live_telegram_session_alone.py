"""
`jalen check` opened the Telegram session while Jalen was using it.

The session file is one login, and only one client may hold it: a second
client on the same file can get it thrown out (telegram_user.py; the
collaborator brief's "never start a second Jalen while one runs"). run.py
enforces that with its single-instance lock - but `run.py --check` returns
before taking the lock, and scripts/check_env.py's account check called
telegram_status(), which connects, whether or not Jalen (or the owner-run
scripts/live_telegram_check.py, which takes the same lock) was holding the
session. CLAUDE.md and the collaborator brief both say to run `jalen check`
FIRST, so the diagnostic was the likeliest second client on this machine.

Found 2026-10-01 by an independent review of the live check script.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def check_env(monkeypatch, tmp_path):
    from jarvis import runtime
    from jarvis.config import CONFIG
    from jarvis.integrations import google_auth

    for name, leaf in (("RUNTIME_DIR", ""), ("LOCK_PATH", "jarvis.lock"),
                       ("STOP_PATH", "jarvis.stop"), ("SIGNAL_PATH", "jalen.signal")):
        monkeypatch.setattr(runtime, name, tmp_path / leaf if leaf else tmp_path)
    monkeypatch.setitem(CONFIG.setdefault("telegram", {}).setdefault("personal", {}), "enabled", True)
    monkeypatch.setattr(google_auth, "have_token", lambda: True)
    monkeypatch.setattr(google_auth, "load_credentials", lambda **_: object())
    monkeypatch.setattr(google_auth, "whoami", lambda: "him@example.com")

    spec = importlib.util.spec_from_file_location("check_env_under_test", ROOT / "scripts" / "check_env.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_env_under_test"] = module
    spec.loader.exec_module(module)
    return module


def _held_by(mode: str, pid: "int | None" = None) -> None:
    from jarvis import runtime

    if pid is None:
        me = psutil.Process(os.getpid())
        pid, started = me.pid, me.create_time()
    else:
        started = 0.0
    runtime.LOCK_PATH.write_text(f"{pid}\n{started}\n{mode}\n", encoding="utf-8")


def _status_must_not_connect(monkeypatch):
    from jarvis.tools import messaging

    def connects():
        raise AssertionError("jalen check connected to Telegram while another program held the session")

    monkeypatch.setattr(messaging, "telegram_status", connects)


@pytest.mark.parametrize("mode", ["voice", "text", "telegram", "telegram-check"])
def test_a_session_in_use_is_not_opened_a_second_time(check_env, monkeypatch, capsys, mode):
    _held_by(mode)
    _status_must_not_connect(monkeypatch)
    assert check_env._check_accounts() == 0, "a session in use is not a problem"
    out = capsys.readouterr().out
    assert f"{check_env.WARN}personal Telegram not checked" in out and f"({mode} mode)" in out


def test_with_nothing_holding_the_session_it_is_checked_as_before(check_env, monkeypatch, capsys):
    from jarvis.tools import messaging

    monkeypatch.setattr(messaging, "telegram_status", lambda: "Personal Telegram signed in as Jalen (@x).")
    assert check_env._check_accounts() == 0
    assert "[ok] Personal Telegram signed in" in capsys.readouterr().out


def test_a_stale_lock_from_a_crash_does_not_stop_the_check(check_env, monkeypatch, capsys):
    from jarvis.tools import messaging

    dead = 4_000_000          # far above any pid Windows hands out
    assert not psutil.pid_exists(dead)
    _held_by("voice", pid=dead)
    monkeypatch.setattr(messaging, "telegram_status", lambda: "Personal Telegram signed in as Jalen (@x).")
    assert check_env._check_accounts() == 0
    assert "[ok] Personal Telegram signed in" in capsys.readouterr().out
