"""
The tools that drive his real Chrome through the extension.

These sit on top of the bridge (tested in test_bridge.py) and the page ops
(tested in test_extension_page_ops.py), so here a FAKE server stands in for
the whole transport and the tests check the tool logic: that it fills
ordinary fields, refuses the payment and password ones, and - the thing he
specifically asked for - fails LOUDLY when the extension is down rather than
quietly opening the other browser.
"""
from __future__ import annotations

import pytest

from jalen.tools import browser_ext
from jalen.bridge.server import BridgeError


class _FakeServer:
    def __init__(self, connected=True, fields=None):
        self.connected = connected
        self._fields = fields or []
        self.filled = {}          # index -> value actually sent

    def start(self):
        pass

    def send_command(self, command, payload=None, timeout=20):
        if not self.connected:
            raise BridgeError("Jalen's Chrome extension isn't connected.")
        if command == "get_form_fields":
            return {"fields": self._fields}
        if command == "get_page_state":
            return {"title": "T", "url": "https://x", "headings": ["H"],
                    "fieldCount": len(self._fields), "hasPasswordField": True}
        if command == "fill":
            self.filled[payload["index"]] = payload["value"]
            return {"filled": True}
        raise AssertionError(f"unexpected command {command}")


def _install(monkeypatch, server):
    monkeypatch.setattr(browser_ext, "_server", lambda: server)
    import jalen.bridge.server as s
    monkeypatch.setattr(s, "get_server", lambda: server)
    return server


def _field(**kw):
    base = {"index": 0, "label": "Field", "type": "text", "name": "",
            "autocomplete": "", "required": False, "visible": True,
            "disabled": False}
    base.update(kw)
    return base


class TestStatus:

    def test_connected(self, monkeypatch):
        _install(monkeypatch, _FakeServer(connected=True))
        assert "connected" in browser_ext.ext_status().lower()

    def test_not_connected_points_at_setup(self, monkeypatch):
        _install(monkeypatch, _FakeServer(connected=False))
        out = browser_ext.ext_status()
        assert "isn't connected" in out or "not connected" in out
        assert "CHROME_EXTENSION_SETUP" in out


class TestFillFromProfile:

    PROFILE = {"first name": "Jaloliddin", "last name": "Musayev", "age": "17"}

    def _fields(self):
        return [
            _field(index=0, label="First name", name="fname",
                   autocomplete="given-name", required=True),
            _field(index=1, label="Age", name="age"),
            _field(index=2, label="Card number", name="card",
                   autocomplete="cc-number"),
            _field(index=3, label="Password", name="pw", type="password"),
            _field(index=4, label="Email", name="email",
                   autocomplete="email", required=True),
        ]

    def test_it_fills_ordinary_fields_only(self, monkeypatch):
        server = _install(monkeypatch, _FakeServer(fields=self._fields()))
        monkeypatch.setattr("jalen.tools.profile.load_profile",
                            lambda: self.PROFILE)
        out = browser_ext.ext_fill_form_from_profile()
        # First name and age filled; card, password, email not.
        assert server.filled == {0: "Jaloliddin", 1: "17"}
        assert "First name" in out and "Age" in out

    def test_it_leaves_payment_and_password_alone(self, monkeypatch):
        server = _install(monkeypatch, _FakeServer(fields=self._fields()))
        monkeypatch.setattr("jalen.tools.profile.load_profile",
                            lambda: self.PROFILE)
        out = browser_ext.ext_fill_form_from_profile()
        assert 2 not in server.filled, "a payment field was filled"
        assert 3 not in server.filled, "a password field was filled"
        assert "payment" in out.lower()

    def test_it_reports_a_required_field_it_lacks(self, monkeypatch):
        _install(monkeypatch, _FakeServer(fields=self._fields()))
        monkeypatch.setattr("jalen.tools.profile.load_profile",
                            lambda: self.PROFILE)
        out = browser_ext.ext_fill_form_from_profile()
        assert "Email" in out and "need" in out.lower()


class TestNoSilentFallback:
    """
    He asked for the real-Chrome route specifically. When the extension is
    down, the tools must SAY so, not quietly do something else - the silent
    fallback is the frustration that started this whole thread.
    """

    def test_page_state_fails_loudly(self, monkeypatch):
        _install(monkeypatch, _FakeServer(connected=False))
        out = browser_ext.ext_page_state()
        assert "extension" in out.lower()

    def test_fill_fails_loudly(self, monkeypatch):
        _install(monkeypatch, _FakeServer(connected=False))
        monkeypatch.setattr("jalen.tools.profile.load_profile",
                            lambda: {"first name": "J"})
        out = browser_ext.ext_fill_form_from_profile()
        assert "extension" in out.lower()
