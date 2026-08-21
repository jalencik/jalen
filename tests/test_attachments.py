"""
Sending files, and the files that must never be sent.

His ask: Jalen should be able to "copy some files from desktop or the whole
system itself and could be able to paste that into my telegram and my gmail
inbox and send it as well".

An attachment is the easiest exfiltration path there is — "send my session
file to this chat" is one sentence, and the session file IS a password-less
login to his Telegram account. So most of this file is about refusing.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.tools import attachments


ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------- protection
@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".env.example",
        "production.env",
        "data/telegram_user.session",
        "data/telegram_user.session-journal",
        "data/google_token.json",
        "client_secret.json",
        "data/vault.json",
        "id_rsa",
        "server.pem",
        "passwords.kdbx",
        "my_credentials.txt",
    ],
)
def test_credentials_are_never_sendable(name):
    """
    Each of these is a LIVE credential, not a file about one. google_token
    .json is an OAuth refresh token — standing access to his mail and
    calendar — and it was NOT protected until the attachment tool was asked
    to send it and happily would have.
    """
    assert attachments._is_protected(ROOT / name), f"{name} could be attached and sent"


@pytest.mark.parametrize("name", ["README.md", "image.png", "notes.txt", "cv.pdf"])
def test_ordinary_files_are_sendable(name):
    """The guard must not be so broad that it refuses his actual CV."""
    assert attachments._is_protected(ROOT / name) is None


def test_the_refusal_is_not_overridable():
    """
    Protected means protected. A tool that could be talked past by insisting
    is not a protection, and the reply says so rather than implying there is
    a magic phrase.
    """
    _path, problem = attachments._resolve(str(ROOT / ".env"))
    assert "won't send" in problem
    assert "isn't something you can override" in problem


def test_the_check_returns_a_verdict_rather_than_raising():
    """
    The first version called filesystem._is_under_never_touch, which takes
    TWO arguments — so the check raised TypeError instead of refusing. The
    protection crashed rather than working, and a crash inside a send path
    is not a refusal.
    """
    for name in (".env", "README.md", "does_not_exist.xyz"):
        result = attachments._is_protected(ROOT / name)   # must not raise
        assert result is None or isinstance(result, str)


def test_filename_patterns_are_covered_not_just_directories():
    """
    The behaviour the old helper could not produce. It only knew never_touch
    DIRECTORIES; "*.session" and "*token*" are filename PATTERNS, and a
    session file living anywhere other than a protected folder would have
    sailed straight through.
    """
    anywhere = Path("C:/Users/user/Desktop/random_folder/telegram_user.session")
    assert attachments._is_protected(anywhere), (
        "a session file outside a protected directory is still a full login"
    )


# ---------------------------------------------------------------- resolving
def test_a_missing_file_is_reported_not_sent():
    """
    A send that succeeds with nothing attached is the exact silent-success
    failure this project keeps producing.
    """
    path, problem = attachments._resolve("definitely_not_a_real_file_9x7.pdf")
    assert path is None
    assert "couldn't find" in problem


def test_an_empty_name_asks_which_file():
    path, problem = attachments._resolve("   ")
    assert path is None and "which file" in problem.lower()


def test_an_ambiguous_name_refuses_rather_than_picking(monkeypatch):
    """
    Guessing which of three files he meant, and sending it to a person, is
    not recoverable.
    """
    monkeypatch.setattr(
        "jarvis.tools.launcher.find_files",
        lambda q: [str(ROOT / "README.md"), str(ROOT / "SETUP.md")],
    )
    path, problem = attachments._resolve("md")
    assert path is None
    assert "more than one" in problem


def test_a_real_path_resolves():
    path, problem = attachments._resolve(str(ROOT / "README.md"))
    assert problem == ""
    assert path and path.name == "README.md"


# -------------------------------------------------------------------- size
def test_an_oversized_file_is_refused_before_uploading(tmp_path):
    """
    Discovering the limit from a failed upload after ninety seconds is worse
    than being told immediately.
    """
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 2048)
    problem = attachments._too_big(big, 1024, "Gmail")
    assert "megabytes" in problem
    assert "Nothing was sent" in problem


def test_a_normal_file_passes_the_size_check(tmp_path):
    small = tmp_path / "small.txt"
    small.write_text("hello")
    assert attachments._too_big(small, 1024, "Gmail") == ""


def test_the_limits_are_the_real_ones():
    """
    Gmail's 25MB is the whole ENCODED message, and base64 inflates by a
    third — so the real ceiling for a file is around 18MB, not 25.
    """
    assert attachments.GMAIL_MAX_BYTES < 25 * 1024**2
    assert attachments.TELEGRAM_MAX_BYTES > attachments.GMAIL_MAX_BYTES


# ------------------------------------------------------------ reachability
def test_tiers_match_what_each_tool_does():
    """
    A Telegram file reaches a person and cannot be recalled — RED. A Gmail
    draft reaches nobody until he presses send — GREEN.
    """
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    assert engine.classify("send_telegram_file", {}).tier is Tier.RED
    assert engine.classify("draft_email_with_file", {}).tier is Tier.GREEN
    for name in ("send_telegram_file", "draft_email_with_file"):
        assert name in tools.REGISTRY and name in TOOL_SPECS
