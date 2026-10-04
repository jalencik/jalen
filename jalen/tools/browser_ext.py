"""
Driving his ACTUAL Chrome, through Jalen's extension.

Every tool here goes through the bridge server (jalen/bridge/server.py),
which talks to the extension running inside his everyday Chrome. So unlike
the CDP path in webagent.py - a separate Chrome on a dedicated profile -
these act on the very tab he is looking at.

They fail LOUDLY when the extension is not connected, rather than falling
back to the other browser behind his back: he asked specifically for the
real-Chrome route, and a silent fallback to the dedicated profile is exactly
the "why won't you use MY Chrome" frustration that started this. When the
extension is down, the honest answer is "install/enable it", not a different
window opening.
"""
from __future__ import annotations

from typing import Any


def _server():
    from ..bridge.server import get_server
    srv = get_server()
    srv.start()          # idempotent; binds the loopback socket if not already
    return srv


def _need_extension(exc) -> str:
    return (f"{exc} Load the Jalen extension in Chrome and make sure Chrome "
            f"is open - see docs/CHROME_EXTENSION_SETUP.md. For now I can "
            f"still use the separate browser route if you'd rather.")


def ext_status() -> str:
    """Is his Chrome extension connected right now? — GREEN."""
    from ..bridge.server import get_server
    srv = get_server()
    srv.start()
    if srv.connected:
        return "Your Chrome extension is connected - I can act on your real tabs."
    return ("Your Chrome extension isn't connected. Open Chrome with the Jalen "
            "extension loaded; setup is in docs/CHROME_EXTENSION_SETUP.md.")


def ext_page_state() -> str:
    """What's on the tab he's looking at — GREEN."""
    from ..bridge.server import BridgeError
    try:
        state = _server().send_command("get_page_state", timeout=15)
    except BridgeError as exc:
        return _need_extension(exc)
    heads = ", ".join(state.get("headings", [])[:6]) or "no headings"
    return (f"{state.get('title','(untitled)')} — {state.get('url','')}\n"
            f"Headings: {heads}. {state.get('fieldCount',0)} form field(s)"
            f"{'; a password field is present' if state.get('hasPasswordField') else ''}.")


def ext_form_fields() -> str:
    """List the fields on his current page — GREEN. Ask for what's missing."""
    from ..bridge.server import BridgeError
    try:
        data = _server().send_command("get_form_fields", timeout=15)
    except BridgeError as exc:
        return _need_extension(exc)
    fields = data.get("fields", [])
    if not fields:
        return "There's no form on this page that I can see."
    lines = []
    for f in fields:
        if not f.get("visible"):
            continue
        flag = " (required)" if f.get("required") else ""
        lines.append(f"  - {f['label']} [{f['type']}]{flag}")
    return "Fields on this page:\n" + "\n".join(lines)


def ext_fill_form_from_profile() -> str:
    """
    Fill the form on his real page from data/personal_info — AMBER.

    Same discipline as the CDP fill_form_from_profile: ordinary fields only,
    never a password (vault) or a payment field (his by rule), and it reports
    exactly what it filled and what it still needs. The only difference is
    WHERE - this is the tab he is actually looking at, not a separate window.
    """
    from ..bridge.server import BridgeError
    from . import profile

    data_profile = profile.load_profile()
    if not data_profile:
        return ("I don't have your details yet. Fill in "
                "data/personal_info/profile.md and say this again.")
    try:
        srv = _server()
        scan = srv.send_command("get_form_fields", timeout=15)
    except BridgeError as exc:
        return _need_extension(exc)

    fields = scan.get("fields", [])
    filled, needed, payment, secret = [], [], [], []
    for field in fields:
        if not field.get("visible") or field.get("disabled"):
            continue
        if field.get("type") == "password":
            secret.append(field["label"])
            continue
        if profile._is_payment(field):
            payment.append(field["label"])
            continue
        concept = profile._concept_for(field)
        if concept is None:
            continue
        value = profile._value_for(concept, data_profile)
        if not value:
            if field.get("required"):
                needed.append(field["label"])
            continue
        try:
            srv.send_command("fill", {"index": field["index"], "value": value},
                             timeout=10)
            filled.append(field["label"])
        except BridgeError:
            needed.append(field["label"])

    parts = ["Filled " + ", ".join(filled) if filled else "I didn't fill anything"]
    if needed:
        parts.append("still need from you: " + ", ".join(needed))
    if payment:
        parts.append("left the payment fields for you (" + ", ".join(payment) + ")")
    if secret:
        parts.append("password fields come from your vault, not here")
    tail = (". Add anything missing to data/personal_info/profile.md."
            if needed else ".")
    return ". ".join(parts) + tail


REGISTRY: dict[str, Any] = {
    "ext_status": ext_status,
    "ext_page_state": ext_page_state,
    "ext_form_fields": ext_form_fields,
    "ext_fill_form_from_profile": ext_fill_form_from_profile,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
