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
  ask      -> THIS TOOL asks him, out loud, naming the site it read from the
              address bar: "Type your sign_in_code into newsite.example just
              this once?" Only his own yes types it, and nothing is written
              down. "From now on" is remember_site_decision, which asks him
              too.

IT USED TO BE THE BRAIN'S FLAG. "ask" refused and told the model to ask him
and call back with approved_once=True - and nothing checked that he had been
asked, what the question named, or that the page was still that site. The
model could set the flag and his password went into whatever had focus
(tests/test_a_secret_goes_only_where_he_said.py reproduced it). The AMBER
announcement never named a site either: "fill credential. Say stop..."

AND A SECRET BELONGS TO A SITE. Approval says where Jalen may type, never
what; see "which site a secret is for" in vault.py. A secret tied to
accounts.google.com is refused on every other host whatever he approved
there, and one stored before tying existed is tied on first use - after his
yes to a question naming it and the host.
"""
from __future__ import annotations

import time
from typing import Any

from . import interaction
from .system import IS_WINDOWS
from .vault import (
    VaultLocked,
    _host_of,
    _normalise_domain,
    describe_secret_sites,
    get_secret,
    has_secret,
    secret_binding,
    site_permission,
    tie_secret,
)


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
    return _focused_page()[1]


def _focused_page() -> tuple[int, str]:
    """
    (window, URL) of the browser window IN FRONT, or (0, "") if the window
    in front is not a browser or its address bar cannot be read. Never raises.

    The foreground window or nothing. This used to fall back to the FIRST
    browser window enumerated when the one in front was not a browser at all,
    so with Notepad or Telegram in front and Chrome behind it, Chrome's
    address bar was checked and approved and the keystrokes - which go to
    whatever has focus - went into Notepad or the chat box. The window comes
    back too, so the typing can check it is still the one he was looking at.
    """
    try:
        import ctypes

        import uiautomation as auto

        from .browsertabs import _windows

        foreground = ctypes.windll.user32.GetForegroundWindow()
        if not foreground or not any(h == foreground for h, _t, _b in _windows()):
            return 0, ""
        control = auto.ControlFromHandle(foreground)
        edit = control.EditControl(searchDepth=10)
        if not edit.Exists(1, 0.2):
            return 0, ""
        url = (edit.GetValuePattern().Value or "").strip()
        return (int(foreground), url) if url else (0, "")
    except Exception:
        return 0, ""


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


def _question(secret: str, host: str, decision: str, where: str) -> str:
    """
    What he is asked before a secret goes into `host`. It names the secret
    and the host as read from the address bar - so a lookalike is said out
    loud - and it carries no agreement word ("approved", "right", "fine"):
    Jalen's own voice coming back through the speakers is parsed by the same
    _parse_yes_no as his answer, and " approved " is on its YES list.
    """
    if decision == "always":
        return (f"Your {secret} is not tied to any site yet. "
                f"Tie it to {host} and type it in?")
    if where == "unbound":
        return (f"{host} is not on your trusted list, and your {secret} is not "
                f"tied to any site. Type it into {host} just this once, and "
                f"tie it to {host}?")
    return (f"{host} is not on your trusted list. Type your {secret} into it "
            f"just this once?")


def _not_typed(secret: str, host: str, answer: "interaction.Answer") -> str:
    """Anything but his yes, said as what happened - never as a refusal he did not make."""
    if answer.outcome == "unavailable":
        return (f"I have to ask you before typing your {secret} into {host}, "
                f"and there's no way to ask you right now, so nothing was typed.")
    if answer.outcome == "timeout":
        return (f"No answer, so nothing was typed into {host}. He may not have "
                f"heard the question - don't say he refused.")
    if answer.outcome == "correction":
        return (f"Nothing typed. He answered with something else: "
                f'"{answer.words}". If that tells you what he wants instead, '
                f"do that - it goes through the safety gate like anything else.")
    if answer.outcome == "failed":
        return f"I couldn't ask you about {host}, so nothing was typed."
    return f"Okay - nothing typed into {host}."


def fill_credential(secret: str, approved_once: bool = False) -> str:
    """
    Type a stored secret into the FOCUSED field — AMBER.

    He must click the field first. Jalen never picks the field, so this
    cannot put a password somewhere unintended.

    approved_once is [NOT READ]. It was the whole approval for a site he had
    not approved for good, and it was the model's to set. Accepted so a stale
    call gets a sentence rather than a TypeError; it grants nothing. What
    grants a one-time use now is his own spoken yes to a question this tool
    asks, naming the host it read.
    """
    del approved_once           # deliberately ignored - see the docstring
    if not IS_WINDOWS:
        return "Windows only."

    name = (secret or "").strip()
    window, url = _focused_page()
    if not url:
        return (
            "I can't read which page you're on, so I haven't typed anything. "
            "Click into the browser window and ask again."
        )
    host = _host_of(url)
    # site_permission is keyed by _normalise_domain, the binding by the
    # parsed host. On any address Chrome shows they agree; if they ever do
    # not, the approval being checked is not the page's, so nothing is typed.
    if not host or host != _normalise_domain(url):
        return ("I can't tell for certain which site this page is on, so I "
                "haven't typed anything.")

    decision = site_permission(url)
    if decision == "never":
        return f"You told me never to fill anything on {host}. Nothing typed."

    # Before he is asked anything: is there something to type at all?
    try:
        stored = has_secret(name)
    except VaultLocked:
        return "The vault is locked, so I have nothing to type. Unlock it first."
    if not stored:
        from .vault import list_secrets

        return f"There's nothing stored called {name!r}. {list_secrets()}"

    where = secret_binding(name, url)
    if where == "elsewhere":
        # No spoken yes moves a secret to another site: that yes is exactly
        # what a lookalike page he once approved would be fishing for.
        return (f"Your {name} belongs to {describe_secret_sites(name)}, and "
                f"this page is on {host}, so I haven't typed it. If it really "
                f"is used here too, add {host} to it in scripts\\vault_setup.py.")

    if decision != "always" or where == "unbound":
        answer = interaction.confirm(_question(name, host, decision, where))
        if not answer.yes:
            return _not_typed(name, host, answer)
        if where == "unbound" and not tie_secret(name, [host]):
            return (f"I couldn't write down that your {name} belongs to {host}, "
                    f"so I didn't type it.")

    # AT THE TYPING, not a question ago: the same window, still on the same
    # site, and the secret still belongs there. He answered by voice, so
    # nothing he did should have moved the page - anything that did is not
    # something he said yes to.
    now_window, now_url = _focused_page()
    if (now_window != window or _host_of(now_url) != host
            or site_permission(now_url) == "never"
            or secret_binding(name, now_url) != "here"):
        return (f"The page changed while I was checking, so I didn't type your "
                f"{name} anywhere. Click into the field on {host} again and ask me.")

    try:
        value = get_secret(name)
    except VaultLocked:
        return "The vault locked itself while I was asking, so nothing was typed."
    except KeyError:
        return f"Your {name} isn't in the vault any more, so nothing was typed."
    try:
        if not _type(value):
            return (
                f"I couldn't send the keystrokes for {name}. Nothing was typed — "
                "check the field still has focus."
            )
    finally:
        del value               # out of scope with this frame, always
    # The VALUE never appears here. This string reaches the model, the
    # transcript window, the audit log and possibly the speakers.
    lasting = "as agreed" if decision == "always" else "just this once"
    reply = (
        f"Typed your {name} into the focused field on {host} ({lasting}). "
        "Check it looks right before you submit."
    )
    if where == "unbound":
        reply += f" It's tied to {host} now, so no other site gets it."
    if decision != "always":
        reply += (f" If you want {host} trusted from now on, say so and I'll "
                  f"remember it.")
    return reply


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
