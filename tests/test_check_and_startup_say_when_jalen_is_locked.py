"""
"Jalen refuses everything" looks exactly like "Jalen is broken" - until
something says he asked for it.

A lockdown (jalen/limits.py) survives a restart on purpose. So the places he is
told to look first have to say it is on, and how to end it:

  * `.\\jalen.ps1 check` (scripts/check_env.py) - the "run this first" diagnostic;
  * the first thing Jalen says or prints when he starts, in voice, text and
    Telegram modes (Jalen.startup_notes);

and a limit in safety.yaml that could not be read is a PROBLEM in `check`, not a
silence: it reads as protection and is not.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from jalen import limits
from jalen.config import CONFIG
from jalen.limits import Governor
from jalen.safety import SafetyEngine

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def check_env():
    spec = importlib.util.spec_from_file_location("check_env_limits_probe", ROOT / "scripts" / "check_env.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_env_limits_probe"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("check_env_limits_probe", None)


@pytest.fixture
def lock_file(tmp_path, monkeypatch):
    path = tmp_path / "jalen-lockdown.json"
    monkeypatch.setattr(limits, "LOCK_PATH", path)
    return path


def test_check_counts_the_limits_in_force_and_is_not_a_problem(check_env, lock_file, capsys):
    engine = SafetyEngine(CONFIG)
    assert check_env._check_limits(engine) == 0
    said = capsys.readouterr().out
    assert "limits: 11 in force" in said and "leash of 40" in said
    assert "LOCKDOWN" not in said


def test_check_says_lockdown_is_on_and_names_both_ways_out(check_env, lock_file, capsys):
    lock_file.write_text("{}", encoding="utf-8")
    assert check_env._check_limits(SafetyEngine(CONFIG)) == 0     # his choice: a warning, not a failure
    said = capsys.readouterr().out
    assert "LOCKDOWN is on" in said
    assert "end the lockdown" in said and "delete that file" in said


def test_check_treats_a_limit_that_could_not_be_read_as_a_blocking_problem(check_env, lock_file, capsys):
    engine = SafetyEngine(CONFIG)
    engine.governor = Governor.from_config(
        {"rules": [{"name": "x", "tools": [], "max": 1, "window_s": 60}]})
    assert check_env._check_limits(engine) == 1
    assert "needs a non-empty list of tool names" in capsys.readouterr().out


def test_check_says_when_the_limits_are_switched_off(check_env, lock_file, capsys):
    engine = SafetyEngine(CONFIG)
    engine.governor = Governor.from_config({"enabled": False})
    assert check_env._check_limits(engine) == 0
    assert "switched off" in capsys.readouterr().out


def test_check_only_looks_and_never_writes_the_lock(check_env, lock_file):
    check_env._check_limits(SafetyEngine(CONFIG))
    assert not lock_file.exists()


def test_check_runs_it_inside_the_config_section():
    """The call is in main()'s Config block, after the posture it belongs beside."""
    source = (ROOT / "scripts" / "check_env.py").read_text(encoding="utf-8")
    assert source.index("posture: {engine.posture}") < source.index("_check_limits(engine)") \
        < source.index("voice: {CONFIG.get_path")


@pytest.mark.parametrize("branch", ["text mode", "Telegram mode"])
def test_text_and_telegram_modes_print_the_startup_notes(branch):
    """Voice mode says them aloud in run(); these two have no voice to do it."""
    source = (ROOT / "run.py").read_text(encoding="utf-8")
    banner = source.index(f"Jalen — {branch}")
    assert "jalen.startup_notes()" in source[banner:banner + 400]


def test_voice_mode_says_them_once_when_it_starts():
    import inspect

    from jalen.app import Jalen

    run = inspect.getsource(Jalen.run)
    started = run.index('summary=f"Jalen started (session {self.session_id})"')
    # At once - or, in standby (run.py --standby), when the wake key opens the
    # microphone: a standby Jalen says nothing until he asks for it.
    assert run.count("for note in self.startup_notes():") == 2
    assert "if not standby:" in run[started:started + 120]
    woken = run.index("if not self._wait_in_standby():")
    assert woken < run.index("for note in self.startup_notes():", woken) < run.index("with self.mic:")
