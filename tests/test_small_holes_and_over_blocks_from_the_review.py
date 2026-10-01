"""
Three small findings from the independent review of today's work.

1. run_own_tests(subset="tests/../../anything.py") ran an ARBITRARY Python
   file. The subset only had to START with "tests", and pytest treats the
   rest as a path, so a ".." walks out of the tests folder and pytest
   imports and runs whatever it finds. It is a GREEN tool the model can call.
   The subset must now resolve to a file or folder INSIDE tests/.

2. A value with a newline in it skipped the never-touch inspection entirely
   (a guard against prose), while the tools strip a trailing newline before
   they use the value: start_coding_job(folder="C:\\Windows\\n") got through.
   Leading and trailing whitespace is stripped first now; a newline in the
   MIDDLE still marks it as prose.

3. OVER-BLOCKING, which matters for someone who works with machine-learning
   models. "*token*.json" refused every HuggingFace tokenizer.json,
   tokenizer_config.json and special_tokens_map.json, ".env*" refused
   .env.example, and any message that merely MENTIONED a password manager
   was refused because the check looked for the names in every argument
   joined together - c8ca2a3 fixed prose for domains and left apps behind.
   Plain names that hold no secret are now on a short list
   (never_touch.harmless_names), checked after the protected FOLDERS and
   only against the filename patterns, never in place of them. And the
   password-manager check looks at the arguments that name a program or a
   window, not at prose.
"""
from __future__ import annotations

import pytest

from jarvis.config import CONFIG
from jarvis.safety import SafetyEngine, Tier


@pytest.fixture
def engine():
    return SafetyEngine(CONFIG)


# ------------------------------------------------------ 1. run_own_tests
@pytest.fixture
def spawned(monkeypatch):
    from jarvis.tools import selfcontrol

    calls = []

    class _Done:
        stdout = "1 passed in 0.1s"
        stderr = ""

    monkeypatch.setattr(selfcontrol.subprocess, "run", lambda args, **kw: calls.append(args) or _Done())
    return calls


@pytest.mark.parametrize("subset", [
    "tests/../../Downloads/planted.py",
    "../outside.py",
    "tests/../jarvis/app.py",
    "tests\\..\\..\\x.py",
    "C:/Users/someone/Downloads/planted.py",
    "tests/nonexistent_file_for_this_test.py",
])
def test_a_subset_outside_the_tests_folder_runs_nothing(spawned, subset):
    from jarvis.tools import selfcontrol

    reply = selfcontrol.run_own_tests(subset)
    assert spawned == [], f"{subset!r} reached pytest: {spawned}"
    assert "tests" in reply.lower()


@pytest.mark.parametrize("subset", ["test_orb.py", "tests/test_orb.py", "tests/test_orb.py::test_x"])
def test_a_real_subset_still_runs(spawned, subset):
    from jarvis.tools import selfcontrol

    selfcontrol.run_own_tests(subset)
    assert spawned and spawned[0][-1].replace("\\", "/").startswith("tests/test_orb.py")


# --------------------------------------------------------- 2. the newline
@pytest.mark.parametrize("raw", ["C:\\Windows\n", "\nC:\\Windows\\System32", "  ~\\.ssh\\config\r\n"])
def test_surrounding_whitespace_does_not_hide_a_protected_path(engine, raw):
    assert engine.classify("start_coding_job", {"folder": raw}).tier is Tier.BLACK, repr(raw)


def test_a_newline_in_the_middle_is_still_prose(engine):
    out = engine.classify("send_telegram_message",
                          {"to": "Saved Messages", "text": "see C:/Windows\nthen reboot"})
    assert out.tier is not Tier.BLACK


# --------------------------------------------------------- 3. over-blocking
@pytest.mark.parametrize("name", [
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    ".env.example", ".env.sample", ".env.template",
])
def test_plain_files_are_readable(engine, tmp_path, name):
    # Not named for the word the patterns look for: pytest builds tmp_path
    # from the test's name, and a folder called "...secret..." is protected.
    assert engine.classify("read_file", {"path": str(tmp_path / name)}).tier is not Tier.BLACK, name


@pytest.mark.parametrize("raw", [
    ".env", ".env.local", ".env.production", "google_token.json", "client_secret.json",
    "vault.json", "passwords.txt", "id_rsa", "x.pem",
    "refresh_token.json", "my-tokenizer-token.json",
])
def test_real_secrets_are_still_refused(engine, tmp_path, raw):
    assert engine.classify("read_file", {"path": str(tmp_path / raw)}).tier is Tier.BLACK, raw


def test_a_harmless_name_inside_a_protected_folder_is_still_protected(engine):
    import os

    ssh_like = os.path.expanduser("~/.ssh/tokenizer.json")
    assert engine.classify("read_file", {"path": ssh_like}).tier is Tier.BLACK


def test_a_post_that_mentions_a_password_manager_is_prose(engine):
    out = engine.classify("send_telegram_message",
                          {"to": "Saved Messages",
                           "text": "Tip: use Bitwarden or KeePass instead of reusing passwords"})
    assert out.tier is not Tier.BLACK
    assert engine.classify("web_search", {"query": "bitwarden vs 1password for teams"}).tier is not Tier.BLACK


@pytest.mark.parametrize("tool, args", [
    ("open_app", {"name": "Bitwarden"}),
    ("open_target", {"name": "KeePass"}),
    ("focus_window", {"name": "1Password"}),
    ("close_app", {"name": "LastPass"}),
    ("read_screen", {"window": "Bitwarden"}),
    ("type_text", {"window": "KeePass - database", "text": "x"}),
    ("click_element", {"window": "1Password", "name": "Copy"}),
])
def test_targeting_a_password_manager_is_still_refused(engine, tool, args):
    assert engine.classify(tool, args).tier is Tier.BLACK, (tool, args)
