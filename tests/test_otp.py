"""
Reading his 2FA code from his own email - and refusing the wrong number.

    "for 2FA if it asks for code, it already has an access to my email, it
     sees it reads it and copy pastes it, is that so hard?"

Legitimate - his inbox, his code, his own security step. The danger is not
the reading; it is reading the WRONG number, or an OLD one, and typing it in
with confidence. So the extraction and the gating carry most of these tests:
a code must be recent, from the right sender, and actually a code rather than
a year or an order number. And it is never returned to anyone - the tests
check the value never leaves as a tool result.
"""
from __future__ import annotations

import time

import pytest

from jarvis.tools import otp


# ---------------------------------------------------------------------------
# PULLING THE CODE OUT OF THE TEXT
# ---------------------------------------------------------------------------
class TestExtraction:

    @pytest.mark.parametrize("text,expected", [
        ("Your ChatGPT code is 034913", "034913"),
        ("123456 is your verification code", "123456"),
        ("G-558231 is your Google verification code", "558231"),
        ("Enter this code to sign in: 9087", "9087"),
        ("Your one-time passcode is 4471902", "4471902"),
        ("OpenAI\nYour code: 246810\nIt expires in 10 minutes.", "246810"),
    ])
    def test_real_code_mails(self, text, expected):
        assert otp._extract_code(text) == expected

    def test_a_year_is_not_a_code(self):
        assert otp._extract_code("Copyright 2026 OpenAI. All rights.") == ""

    def test_prefers_the_number_next_to_the_word_code(self):
        """Order number 778812 sits in the mail too; the CODE is 445566."""
        text = ("Thanks for your order 778812.\n"
                "Your verification code is 445566.")
        assert otp._extract_code(text) == "445566"

    def test_all_zeros_is_refused(self):
        assert otp._extract_code("Your code is 000000") == ""

    def test_empty_text_is_no_code(self):
        assert otp._extract_code("") == ""
        assert otp._extract_code("No numbers here at all.") == ""


class TestSenderTrust:

    @pytest.mark.parametrize("sender,service,ok", [
        ("OpenAI <noreply@openai.com>", "chatgpt", True),
        ("no-reply@accounts.google.com", "gemini", True),
        ("ChatGPT <noreply@email.chatgpt.com>", "chatgpt", True),
        ("Promotions <deals@openai-newsletter.ru>", "chatgpt", False),
        ("no-reply@accounts.google.com", "chatgpt", False),
        ("A friend <someone@gmail.com>", "gemini", False),
    ])
    def test_only_the_services_real_sender_is_trusted(self, sender, service, ok):
        assert otp._from_trusted_sender(sender, service) is ok


class TestCodeMailRecognition:

    def test_a_code_mail_is_recognised(self):
        assert otp._looks_like_code_mail("Your verification code", "…")

    def test_ordinary_mail_is_not(self):
        assert not otp._looks_like_code_mail(
            "Weekly newsletter", "Here are this week's top stories")


# ---------------------------------------------------------------------------
# THE GATES, against a fake Gmail
# ---------------------------------------------------------------------------
class _Exec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeService:
    """The Gmail API surface _recent_code calls: users().messages().get()."""

    def __init__(self, messages):
        self._data = messages

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, format):
        data = next(m for m in self._data if m["id"] == id)
        when_ms = (time.time() - data["age_seconds_ago"]) * 1000.0
        return _Exec({
            "internalDate": str(int(when_ms)),
            "snippet": data.get("snippet", ""),
            "payload": {"from": data["from"], "subject": data["subject"],
                        "body": data["body"]},
        })


def _install(monkeypatch, messages):
    """
    Patch the REAL gmail module's helpers, because _recent_code does
    `from . import gmail` at call time - that binds the package's module
    object, so swapping sys.modules misses it and only patching the module's
    own attributes takes.
    """
    import jarvis.tools.gmail as g
    service = _FakeService(messages)
    monkeypatch.setattr(g, "_enabled", lambda: None)
    monkeypatch.setattr(g, "gmail_service", lambda: service)
    monkeypatch.setattr(g, "_messages",
                        lambda svc, query, n: [{"id": m["id"]} for m in messages][:n])
    monkeypatch.setattr(g, "_header",
                        lambda payload, name: payload.get(name.lower(), ""))
    monkeypatch.setattr(g, "_extract_body", lambda payload: payload.get("body", ""))
    return service


class TestRecentCodeGates:

    def _msg(self, **kw):
        base = dict(id="m1", age_seconds_ago=30,
                    **{"from": "OpenAI <noreply@openai.com>"},
                    subject="Your ChatGPT code",
                    body="Your verification code is 123456", snippet="")
        base.update(kw)
        return base

    def test_a_fresh_code_from_the_right_sender_is_read(self, monkeypatch):
        _install(monkeypatch, [self._msg()])
        code, reason = otp._recent_code("chatgpt", within_minutes=10)
        assert code == "123456", reason

    def test_an_old_code_is_refused(self, monkeypatch):
        _install(monkeypatch, [self._msg(age_seconds_ago=3600)])   # an hour old
        code, reason = otp._recent_code("chatgpt", within_minutes=10)
        assert code == ""
        assert "fresh" in reason or "expired" in reason or "sent yet" in reason

    def test_a_code_from_the_wrong_sender_is_refused(self, monkeypatch):
        _install(monkeypatch, [self._msg(**{"from": "spam@openai-fake.ru"})])
        code, _reason = otp._recent_code("chatgpt", within_minutes=10)
        assert code == ""

    def test_a_non_code_mail_is_ignored(self, monkeypatch):
        _install(monkeypatch, [self._msg(subject="Welcome to ChatGPT",
                                         body="Thanks for joining us!",
                                         snippet="")])
        code, _reason = otp._recent_code("chatgpt", within_minutes=10)
        assert code == ""

    def test_an_unknown_service_never_guesses(self, monkeypatch):
        _install(monkeypatch, [self._msg()])
        code, reason = otp._recent_code("dropbox", within_minutes=10)
        assert code == ""
        assert "sender" in reason.lower()


class TestTheCodeNeverLeaks:

    def test_unknown_service_asks_which_one(self):
        out = otp.fill_login_code("")
        assert "ChatGPT or Gemini" in out

    def test_the_registry_exposes_only_the_filler(self):
        # There is no tool that RETURNS a code - only one that types it.
        assert set(otp.REGISTRY) == {"fill_login_code"}

    def test_a_failure_message_never_contains_a_code(self, monkeypatch):
        _install(monkeypatch, [])   # empty inbox
        out = otp.fill_login_code("chatgpt")
        assert not any(part.isdigit() and len(part) >= 4
                       for part in out.replace(".", " ").split())
