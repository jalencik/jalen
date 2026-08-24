"""
The message contract between the Jalen app and its Chrome extension.

ONE SCHEMA, ONE VERSION, CHECKED AT BOTH ENDS
---------------------------------------------
Every message carries `version` and a `request_id`, and there are exactly
four shapes: a command (app -> extension), a response and an error (extension
-> app, answering one command), and an event (extension -> app, unsolicited -
a tab closed, the connection came up). A message that does not match one of
these is rejected here rather than acted on, because a malformed frame from
a browser extension is either a bug or a hostile page probing the seam, and
both deserve a closed door.

WHY VALIDATION LIVES IN ONE PLACE
---------------------------------
The app trusts nothing off the wire. The extension runs in his browser
alongside pages that would love to speak for it, so the app re-checks the
shape of everything it receives - a command's own id echoed back, a known
command name, a payload that is a dict. build_* construct valid messages;
parse_* reject invalid ones with a reason. Nothing else in the bridge builds
a message by hand.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

# Bumped only on a breaking change to the shapes below. Both ends refuse a
# version they do not speak, so a stale extension fails loudly at the seam
# instead of subtly three calls later.
PROTOCOL_VERSION = 1

# The complete set of actions the app may ask the extension to perform. An
# allowlist, not a suggestion: connectNative gives the extension real power
# in his browser, so the app can request only these, and the extension
# executes only these. Arbitrary-JS is deliberately absent - a brain that
# can run any script in his logged-in Chrome is one prompt-injection away
# from being asked to.
COMMANDS = frozenset({
    # read-only
    "ping",
    "get_page_state",
    "get_form_fields",
    "get_visible_text",
    "list_tabs",
    # navigation
    "navigate",
    "new_tab",
    "focus_tab",
    "reload",
    "go_back",
    "go_forward",
    "close_tab",
    # page actions
    "click",
    "fill",
    "select",
    "check",
    "press",
    "scroll",
    "wait_for",
    # The chat panel: the app pushes a line of text for the panel to show.
    # App-originated, like every command, so the asymmetry holds - the page
    # cannot make the app say anything; only the app speaks into its panel.
    "show_message",
})

# Events the extension may raise on its own. Also an allowlist.
EVENTS = frozenset({
    "connected",
    "disconnected",
    "tab_updated",
    "page_state_changed",
    "heartbeat",
    # He typed a line into the chat panel. This is UNTRUSTED user text from a
    # browser surface, handled by the app's normal brain turn - not a way for
    # a web page to reach the app, because only the panel UI (not page script)
    # can post it, and the app treats it as a request to consider, never a
    # command to obey.
    "user_message",
})

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProtocolError(ValueError):
    """A message that does not meet the contract. Never acted on."""


def new_request_id() -> str:
    """A short, unique id for one command/response pair."""
    return uuid.uuid4().hex[:16]


def build_command(command: str, payload: dict | None = None,
                  request_id: str | None = None) -> dict:
    """A command from the app to the extension. Raises on an unknown name."""
    if command not in COMMANDS:
        raise ProtocolError(f"unknown command {command!r}")
    return {
        "version": PROTOCOL_VERSION,
        "request_id": request_id or new_request_id(),
        "type": "command",
        "command": command,
        "payload": dict(payload or {}),
    }


def build_response(request_id: str, result: Any) -> dict:
    return {
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "type": "response",
        "ok": True,
        "result": result,
    }


def build_error(request_id: str, code: str, message: str) -> dict:
    return {
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "type": "response",
        "ok": False,
        "error": {"code": code, "message": message},
    }


def build_event(event: str, payload: dict | None = None) -> dict:
    if event not in EVENTS:
        raise ProtocolError(f"unknown event {event!r}")
    return {
        "version": PROTOCOL_VERSION,
        "request_id": new_request_id(),
        "type": "event",
        "event": event,
        "payload": dict(payload or {}),
    }


def _check_common(msg: Any) -> dict:
    if not isinstance(msg, dict):
        raise ProtocolError("message is not an object")
    if msg.get("version") != PROTOCOL_VERSION:
        raise ProtocolError(
            f"version {msg.get('version')!r} != {PROTOCOL_VERSION}")
    rid = msg.get("request_id")
    if not isinstance(rid, str) or not _ID_RE.match(rid):
        raise ProtocolError("missing or malformed request_id")
    return msg


def parse_command(msg: Any) -> dict:
    """Validate an incoming command (extension side). Raises ProtocolError."""
    _check_common(msg)
    if msg.get("type") != "command":
        raise ProtocolError(f"type {msg.get('type')!r} is not 'command'")
    if msg.get("command") not in COMMANDS:
        raise ProtocolError(f"unknown command {msg.get('command')!r}")
    payload = msg.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("payload is not an object")
    return msg


def parse_incoming(msg: Any) -> dict:
    """
    Validate anything the app receives from the extension: a response or an
    event. This is the app's front door, so it is strict.
    """
    _check_common(msg)
    kind = msg.get("type")
    if kind == "response":
        if "ok" not in msg:
            raise ProtocolError("response without 'ok'")
        if msg["ok"] is False:
            err = msg.get("error")
            if not isinstance(err, dict) or "code" not in err:
                raise ProtocolError("error response without a coded error")
        return msg
    if kind == "event":
        if msg.get("event") not in EVENTS:
            raise ProtocolError(f"unknown event {msg.get('event')!r}")
        return msg
    raise ProtocolError(f"type {kind!r} is neither 'response' nor 'event'")
