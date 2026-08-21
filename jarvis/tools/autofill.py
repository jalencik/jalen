"""
Typing his details into a form — including the ones from the vault.

This is the piece Phase 4 was missing. The vault stored secrets and the
approval system remembered which sites he trusted, and nothing could
actually TYPE anything: all gate, no action.

HE FOCUSES THE FIELD. JALEN TYPES INTO IT.
------------------------------------------
The obvious build is to find the password box on the page and fill it.
Chrome does not permit that — its accessibility tree is a handful of
anonymous panes and the address bar, and web.py already carries the scar
tissue from trying. Any "find the field" implementation is really a
Tab-and-hope, and Tab-and-hope typed a search query into YouTube's search
box and wiped it, which he reported.

So the contract is explicit and stated in every reply: he clicks the field,
Jalen types into it. That cannot put a password in the wrong box, because
Jalen never chooses the box.

THE URL COMES FROM THE ADDRESS BAR, NOT FROM HIM
------------------------------------------------
Permission is per domain, so something has to know the domain, and asking
him ("which site is this?") would be both tedious and — worse — a place to
lie. A phishing page cannot change what Chrome puts in its own address bar,
so that is what gets checked. Verified live: Chrome exposes it as an Edit
control named "Address and search bar" with a readable value.

If the URL cannot be read, nothing is typed. An unknown domain is not a
trusted one.

THE THREE-WAY ANSWER HE ASKED FOR
---------------------------------
"It should ask me out loud if I approve it permanently or just one time, so
I will give my answer it should remember and act accordingly."

  never    -> refused, and says which site.
  always   -> types it.
  ask      -> refuses THIS call and tells the brain to ask him. If he says
              "just this once", the brain passes approved_once=True and
              nothing is written down. If he says "from now on", the brain
              calls remember_site_decision first.
"""
from __future__ import annotations

import time
from typing import Any

from .system import IS_WINDOWS
from .vault import VaultLocked, _normalise_domain, get_secret, site_permission


def current_page_url() -> str:
    """
    The URL of the focused browser window — GREEN, read-only.

    Read from Chrome's own address bar. A page cannot forge that, which is
    exactly why permission is checked against it rather than against
    anything the page says about itself.
    """
    if not IS_WINDOWS:
        return "Windows only."
    url = _read_url()
    return url or (
        "I can't read the address bar — either no browser window is focused, "
        "or it isn't a browser I recognise."
    )


def _read_url() -> str:
    """The raw URL, or "" if it cannot be read. Never raises."""
    try:
        import uiautomation as auto

        from .browsertabs import _windows

        windows = _windows()
        if not windows:
            return ""
        # The FOREGROUND browser window, not the first one enumerated — he is
        # looking at one particular page and typing into that.
        import ctypes

        foreground = ctypes.windll.user32.GetForegroundWindow()
        hwnd = next((h for h, _t, _b in windows if h == foreground), windows[0][0])

        control = auto.ControlFromHandle(hwnd)
        edit = control.EditControl(searchDepth=10)
        if not edit.Exists(1, 0.2):
            return ""
        return (edit.GetValuePattern().Value or "").strip()
    except Exception:
        return ""


def _type(text: str) -> bool:
    """Type into whatever is focused. True if the keystrokes went out."""
    try:
        import uiautomation as auto

        # SendKeys on the desktop root goes to the focused control, whatever
        # it is — no window search, nothing to get wrong.
        auto.SendKeys(_escape(text), waitTime=0.01)
        return True
    except Exception:
        return False


def _escape(text: str) -> str:
    """
    uiautomation.SendKeys treats {}()+^%~ as syntax. His sign-in code ends in
    three asterisks and could contain any of these; typed unescaped they
    become modifier keys and the password silently arrives wrong.
    """
    out = []
    for char in text:
        out.append("{" + char + "}" if char in "{}()+^%~[]" else char)
    return "".join(out)


def fill_credential(secret: str, approved_once: bool = False) -> str:
    """
    Type a stored secret into the FOCUSED field — AMBER.

    He must click the field first. Jalen never picks the field, so this
    cannot put a password somewhere unintended.
    """
    if not IS_WINDOWS:
        return "Windows only."

    url = _read_url()
    if not url:
        return (
            "I can't read which page you're on, so I haven't typed anything. "
            "Click into the browser window and ask again."
        )
    domain = _normalise_domain(url) or url

    decision = site_permission(url)
    if decision == "never":
        return f"You told me never to fill anything on {domain}. Nothing typed."
    if decision != "always" and not approved_once:
        return (
            f"You haven't approved {domain} yet, so I've typed nothing. Ask him "
            "whether this is just this once or from now on. If just this once, "
            "call me again with approved_once. If from now on, call "
            "remember_site_decision first."
        )

    try:
        value = get_secret(secret)
    except VaultLocked:
        return "The vault is locked, so I have nothing to type. Unlock it first."
    except KeyError:
        from .vault import list_secrets

        return f"There's nothing stored called {secret!r}. {list_secrets()}"

    if not _type(value):
        return (
            f"I couldn't send the keystrokes for {secret}. Nothing was typed — "
            "check the field still has focus."
        )
    # The VALUE never appears here. This string reaches the model, the
    # transcript window, the audit log and possibly the speakers.
    lasting = "as agreed" if decision == "always" else "just this once"
    return (
        f"Typed your {secret} into the focused field on {domain} ({lasting}). "
        "Check it looks right before you submit."
    )


def fill_field(text: str) -> str:
    """
    Type ordinary text into the FOCUSED field — GREEN.

    For the rest of a form: name, phone, the answer to a short question.
    Nothing secret goes through here — that is fill_credential, which checks
    the site first.
    """
    if not IS_WINDOWS:
        return "Windows only."
    if not (text or "").strip():
        return "Nothing to type."
    if not _type(text):
        return "I couldn't send the keystrokes. Nothing was typed."
    preview = text if len(text) <= 60 else text[:57] + "..."
    return f"Typed {preview!r} into the focused field."


def next_field() -> str:
    """
    Tab to the next field — GREEN.

    Useful between fields he has already lined up, and deliberately its own
    tool rather than something fill_field does automatically: a Tab that
    fires when he did not expect it is how text lands in the wrong box.
    """
    if not IS_WINDOWS:
        return "Windows only."
    try:
        import uiautomation as auto

        auto.SendKeys("{Tab}", waitTime=0.05)
        time.sleep(0.1)
        return "Moved to the next field."
    except Exception as exc:
        return f"I couldn't send Tab ({type(exc).__name__})."


REGISTRY: dict[str, Any] = {
    "current_page_url": current_page_url,
    "fill_credential": fill_credential,
    "fill_field": fill_field,
    "next_field": next_field,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
