"""
`jalen check` said "1 blocking problem" while Google had stopped working.

On 2026-09-30 it reported the brain's sign-in and nothing else, while
google_auth had been refusing to refresh its login (RefreshError: the OAuth
consent screen is in Testing mode, so Google expires the token every 7 days)
and every email and calendar request failed. scripts/check_env.py never
looked at Google or at his personal Telegram at all - a diagnostic that
cannot see an outage reports none, the same shape as the brain check it had
already been fixed for once (tests/test_diagnostics_probe_the_real_binary.py).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def check_env(monkeypatch, tmp_path):
    # The account check now leaves Telegram alone while a live Jalen holds
    # the session (test_check_leaves_a_live_telegram_session_alone.py), so
    # these tests must not see the real lock in data/: a gate run while
    # Jalen is up would otherwise change their answer.
    from jalen import runtime

    for name, leaf in (("RUNTIME_DIR", ""), ("LOCK_PATH", "jalen.lock"),
                       ("STOP_PATH", "jalen.stop"), ("SIGNAL_PATH", "jalen.signal")):
        monkeypatch.setattr(runtime, name, tmp_path / leaf if leaf else tmp_path)
    spec = importlib.util.spec_from_file_location("check_env_under_test", ROOT / "scripts" / "check_env.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_env_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def telegram_ok(monkeypatch):
    from jalen.config import CONFIG
    from jalen.tools import messaging

    monkeypatch.setitem(CONFIG.setdefault("telegram", {}).setdefault("personal", {}), "enabled", True)
    monkeypatch.setattr(messaging, "telegram_status", lambda: "Personal Telegram signed in as Jalen (@x).")


def test_a_google_login_that_refuses_to_refresh_is_a_problem(check_env, monkeypatch, capsys, telegram_ok):
    from jalen.integrations import google_auth

    def refuses(**_):
        raise google_auth.GoogleNotConnected("Google refused to refresh the login (RefreshError).")

    monkeypatch.setattr(google_auth, "have_token", lambda: True)
    monkeypatch.setattr(google_auth, "load_credentials", refuses)
    problems = check_env._check_accounts()
    out = capsys.readouterr().out
    assert problems == 1
    assert "[XX] Google" in out and "RefreshError" in out
    assert "connect_google.py" in out and "Publish app" in out


def test_no_google_login_at_all_is_a_problem(check_env, monkeypatch, capsys, telegram_ok):
    from jalen.integrations import google_auth

    monkeypatch.setattr(google_auth, "have_token", lambda: False)
    assert check_env._check_accounts() == 1
    assert "connect_google.py" in capsys.readouterr().out


def test_working_accounts_are_named_and_cost_nothing(check_env, monkeypatch, capsys, telegram_ok):
    from jalen.integrations import google_auth

    monkeypatch.setattr(google_auth, "have_token", lambda: True)
    monkeypatch.setattr(google_auth, "load_credentials", lambda **_: object())
    monkeypatch.setattr(google_auth, "whoami", lambda: "him@example.com")
    assert check_env._check_accounts() == 0
    out = capsys.readouterr().out
    assert "[ok] Google connected as him@example.com" in out
    assert "[ok] Personal Telegram signed in" in out


def test_a_signed_out_telegram_is_a_problem(check_env, monkeypatch, capsys):
    from jalen.config import CONFIG
    from jalen.integrations import google_auth
    from jalen.tools import messaging

    monkeypatch.setattr(google_auth, "have_token", lambda: True)
    monkeypatch.setattr(google_auth, "load_credentials", lambda **_: object())
    monkeypatch.setattr(google_auth, "whoami", lambda: "him@example.com")
    monkeypatch.setitem(CONFIG.setdefault("telegram", {}).setdefault("personal", {}), "enabled", True)
    monkeypatch.setattr(messaging, "telegram_status",
                        lambda: "Personal Telegram isn't signed in. Run: connect_telegram.py")
    assert check_env._check_accounts() == 1
    assert "[XX] Personal Telegram isn't signed in" in capsys.readouterr().out


def test_main_counts_the_accounts():
    source = (ROOT / "scripts" / "check_env.py").read_text(encoding="utf-8")
    assert "problems += _check_accounts()" in source
