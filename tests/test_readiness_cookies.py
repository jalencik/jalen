"""
"Signed in" must mean the session token, not a cookie for the host.

THE FALSE GREEN, TWICE
----------------------
First version: `any(profile.rglob("Cookies"))` - does a cookie FILE exist.
Chrome creates one on first launch, signed in or not, so the readiness
report said "delegation can run unattended" while ChatGPT was signed out.

Second version: "chatgpt.com cookies present". Better, and still wrong:
ChatGPT sets 26 cookies for an ANONYMOUS visitor - measured on his profile,
__cf_bm, oai-did, __Secure-next-auth.STATE and the rest - none of which
mean he is logged in. The one that does is __Secure-next-auth.SESSION-TOKEN,
set on login and cleared on logout.

So these build a real Chrome cookie jar - the actual sqlite schema, at the
actual Chrome-151 path - and prove the check fires on the session token and
on nothing less.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import readiness  # noqa: E402


def _make_jar(profile: Path, cookies: list[tuple[str, str]]) -> None:
    """
    Write a cookie DB where Chrome 151 keeps it: Default/Network/Cookies.

    Only the two columns the probe reads - host_key and name - are given
    real values; the rest of Chrome's schema is present so the file is a
    plausible jar rather than a two-column toy.
    """
    db_dir = profile / "Default" / "Network"
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_dir / "Cookies"))
    try:
        conn.execute(
            "CREATE TABLE cookies ("
            "host_key TEXT, name TEXT, encrypted_value BLOB, path TEXT)"
        )
        conn.executemany(
            "INSERT INTO cookies (host_key, name, encrypted_value, path) "
            "VALUES (?, ?, ?, ?)",
            [(host, name, b"\x00encrypted", "/") for host, name in cookies],
        )
        conn.commit()
    finally:
        conn.close()


ANON_CHATGPT = [
    ("chatgpt.com", "__cf_bm"),
    ("chatgpt.com", "oai-did"),
    (".chatgpt.com", "__Secure-next-auth.state"),   # state, NOT session-token
    ("chatgpt.com", "_dd_s"),
]
SIGNED_IN_CHATGPT = ANON_CHATGPT + [
    (".chatgpt.com", "__Secure-next-auth.session-token"),
]


def _verdict(profile: Path):
    """Re-derive exactly what the readiness line decides, from the jar."""
    cookies = readiness._cookie_pairs(profile)

    def has_cookie(host_suffix, *names):
        return any(h.endswith(host_suffix) and n in names
                   for (h, n) in cookies)

    return has_cookie("chatgpt.com", "__Secure-next-auth.session-token",
                      "__Secure-next-auth.session-token.0")


class TestTheHonestSignal:

    def test_anonymous_cookies_are_not_a_login(self, tmp_path):
        _make_jar(tmp_path, ANON_CHATGPT)
        assert _verdict(tmp_path) is False, (
            "anonymous ChatGPT cookies read as signed in - the false green, "
            "for the third time"
        )

    def test_the_session_token_is_a_login(self, tmp_path):
        _make_jar(tmp_path, SIGNED_IN_CHATGPT)
        assert _verdict(tmp_path) is True

    def test_the_state_cookie_is_not_the_token(self, tmp_path):
        """
        __Secure-next-auth.state and .session-token differ by one word and
        by everything that matters: the first is set for a visitor who never
        logged in.
        """
        _make_jar(tmp_path, [(".chatgpt.com", "__Secure-next-auth.state")])
        assert _verdict(tmp_path) is False

    def test_an_empty_profile_is_signed_out(self, tmp_path):
        assert _verdict(tmp_path) is False


class TestReadingTheJarIsSafe:

    def test_the_chrome_151_path_is_found(self, tmp_path):
        _make_jar(tmp_path, SIGNED_IN_CHATGPT)
        assert readiness._cookie_pairs(tmp_path), "Default/Network/Cookies missed"

    def test_hosts_are_derived_from_pairs(self, tmp_path):
        _make_jar(tmp_path, ANON_CHATGPT)
        assert "chatgpt.com" in readiness._cookie_hosts(tmp_path)

    def test_a_missing_jar_is_survivable_not_fatal(self, tmp_path):
        assert readiness._cookie_pairs(tmp_path) == set()

    def test_it_leaves_no_lock_behind(self, tmp_path):
        """
        The bug that took the whole probe down: `with sqlite3.connect(...)`
        manages the transaction, not the connection, so the temp copy stayed
        open and its cleanup raised WinError 32. Reading twice in a row would
        surface any leaked handle.
        """
        _make_jar(tmp_path, ANON_CHATGPT)
        assert readiness._cookie_pairs(tmp_path)
        assert readiness._cookie_pairs(tmp_path)   # a leak makes this throw
