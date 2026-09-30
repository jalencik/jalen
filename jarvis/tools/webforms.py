r"""
Filling in forms, in Jalen's own browser, without guessing.

WHY THIS IS ALLOWED TO EXIST WHEN autofill.py IS NOT
-----------------------------------------------------
`jarvis/tools/autofill.py` deliberately refuses to pick a form field. That
refusal is correct THERE and must stay: it drives the desktop with keystrokes,
where "find the password box" is Tab-and-hope, and Tab-and-hope has already
typed into YouTube's search box and wiped it.

This module is a different situation, and the difference is not a matter of
degree. Inside Jalen's own Playwright browser a field is not guessed at — it
is *addressed*:

    page.locator('input[type="password"]')

There is no hoping involved. The DOM says which element is the password box,
the same way it says which element is a link. So the invariant that mattered —
**Jalen never chooses which field a secret is typed into** — is not being
relaxed here; it is being satisfied by a mechanism that can actually satisfy
it, instead of avoided because the old mechanism could not.

The rules that follow from that, and that the tests hold:

  * a password only ever goes into `input[type="password"]`, never into
    anything merely *called* password
  * only on a host the vault has an explicit approval for, matched exactly —
    `accounts.google.com.evil.tld` is not `accounts.google.com`
  * exactly one password field on the page, or it refuses and asks him
  * the value never appears in a return value, an audit line, or the model's
    context

WHAT HE ASKED FOR
-----------------
    "it cannot click fields, ask informatino from mee if needed, work without
     becoming idle and asking the question on the way while it is doind its
     work... not being able to copy paste files, or pdfs if necessary"

So: read the form, fill what it knows, ASK for what it does not, upload files
through the real file input rather than a file dialog, report validation
errors back, and never stall silently.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .webagent import BrowserUnavailable, _host, _never_touch, _Session

# Fields we will describe but NEVER fill from anything except the vault, and
# then only through fill_login_field. A model that can write into a password
# box by name is one prompt-injection away from writing into it on request.
_SECRET_TYPES = ("password",)

# How many fields to describe. A 200-field page read out loud is not help.
MAX_FIELDS = 40

# The script that reads the form. Runs in the page, returns plain data.
#
# Written as one evaluate() rather than forty Playwright round trips because
# a form scan that takes eight seconds is one he will talk over.
_SCAN = r"""
() => {
  const out = [];
  const seen = new Set();
  const els = document.querySelectorAll('input, textarea, select');
  for (let i = 0; i < els.length; i++) {
    const el = els[i];
    const type = (el.type || el.tagName).toLowerCase();
    if (type === 'hidden' || type === 'submit' || type === 'button') continue;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;

    // The best human-readable name for this field, most reliable first.
    let label = '';
    if (el.id) {
      const tag = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (tag) label = tag.innerText;
    }
    if (!label && el.closest('label')) label = el.closest('label').innerText;
    if (!label) label = el.getAttribute('aria-label') || '';
    if (!label) label = el.getAttribute('placeholder') || '';
    if (!label) label = el.getAttribute('name') || '';
    label = (label || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    if (!label) label = type + ' field ' + (i + 1);

    const key = label.toLowerCase() + '|' + type;
    if (seen.has(key)) continue;
    seen.add(key);

    out.push({
      index: i,
      label: label,
      type: type,
      // The two most reliable machine hints for WHAT a field is - a form
      // that sets autocomplete="given-name" is telling you outright. Kept
      // alongside the human label so profile.py can match on the strongest
      // signal available and fall back to the label when a site omits them.
      autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
      name: (el.getAttribute('name') || '').toLowerCase(),
      required: el.required === true || el.getAttribute('aria-required') === 'true',
      filled: !!(el.value && String(el.value).length),
      options: el.tagName.toLowerCase() === 'select'
        ? Array.from(el.options).slice(0, 12).map(o => o.text.trim())
        : [],
    });
  }
  return out;
}
"""

_ERRORS = r"""
() => {
  const out = [];
  const nodes = document.querySelectorAll(
    '[aria-invalid="true"], .error, .invalid-feedback, [role="alert"], .field-error');
  nodes.forEach(n => {
    const t = (n.innerText || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 200 && !out.includes(t)) out.push(t);
  });
  document.querySelectorAll('input, textarea, select').forEach(el => {
    if (el.validationMessage && !el.checkValidity()) {
      const m = el.validationMessage.trim();
      const label = el.getAttribute('aria-label') || el.getAttribute('name') || el.type;
      const line = label + ': ' + m;
      if (!out.includes(line)) out.push(line);
    }
  });
  return out.slice(0, 12);
}
"""


def _do(job, *, tab: str, timeout: float = 120.0):
    """
    Run a job on the browser thread. `tab` is required here, not defaulted:
    "read" for a job that only looks, "same" for one that CONTINUES the work
    on the tab it was started in - so if he closed that tab, the job is
    refused rather than run on whichever tab is open instead, which may be a
    different site. See _Session.do.
    """
    return _Session.get().do(job, timeout=timeout, tab=tab)


# The host is webagent's _host: parsed, not split. This module's own copy
# took "https://x.com:8443" as the host "x.com:8443".


# ---------------------------------------------------------------------------
# READING THE FORM
# ---------------------------------------------------------------------------
def inspect_form() -> str:
    """
    What is on the page, field by field — GREEN (reads, changes nothing).

    This is what makes "ask him for what is missing" possible: without a list
    of the fields and which of them are required and still empty, the only
    honest thing Jalen can say is "click a field and I'll type into it",
    which is what it used to say.
    """
    def job(page):
        refusal = _never_touch(page.url, "read its form")
        if refusal:
            return refusal
        return page.url, page.title(), page.evaluate(_SCAN)

    try:
        outcome = _do(job, tab="read")
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't read the page: {type(exc).__name__}: {exc}"
    if isinstance(outcome, str):
        return outcome
    url, title, fields = outcome

    if not fields:
        return (f"No form fields on {title or url}. If the form is behind a "
                f"'start' or 'apply' button, tell me and I'll click it first.")

    fields = fields[:MAX_FIELDS]
    empty_required = [f for f in fields if f["required"] and not f["filled"]]

    lines = [f"{title or url} — {len(fields)} field(s):", ""]
    for f in fields:
        mark = "x" if f["filled"] else " "
        star = " *required" if f["required"] else ""
        secret = "  (I'll only fill this from your vault)" if f["type"] in _SECRET_TYPES else ""
        options = f"  [{', '.join(f['options'][:6])}]" if f["options"] else ""
        lines.append(f"  [{mark}] {f['label']}  ({f['type']}){star}{options}{secret}")

    if empty_required:
        lines += ["", "Still needed: "
                  + ", ".join(f["label"] for f in empty_required[:8])]
    return "\n".join(lines)


def form_errors() -> str:
    """Validation complaints the page is showing — GREEN."""
    def job(page):
        return (_never_touch(page.url, "read its form")
                or page.evaluate(_ERRORS))

    try:
        problems = _do(job, tab="read")
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't read the page: {type(exc).__name__}"
    if isinstance(problems, str):
        return problems
    if not problems:
        return "The form isn't showing any errors."
    return "The form is complaining about:\n" + "\n".join(f"  - {p}" for p in problems)


# ---------------------------------------------------------------------------
# FILLING IT
# ---------------------------------------------------------------------------
def _match(fields: list, wanted: str) -> "dict | None":
    """
    Find the field he named. Exact, then prefix, then contains.

    Ordered rather than fuzzy on purpose: "email" and "confirm email" both
    contain "email", and typing an address into the wrong one of those is a
    form that silently fails validation later.
    """
    want = re.sub(r"\s+", " ", (wanted or "").strip().lower())
    if not want:
        return None
    labels = [(f, f["label"].lower()) for f in fields]
    for f, label in labels:
        if label == want:
            return f
    starts = [f for f, label in labels if label.startswith(want)]
    if len(starts) == 1:
        return starts[0]
    holds = [f for f, label in labels if want in label]
    if len(holds) == 1:
        return holds[0]
    return None


def fill_form_field(field: str, value: str) -> str:
    """
    Type a value into one named field — AMBER.

    Refuses password fields outright. A model that can write into a password
    box by name is one prompt-injection away from being asked to.
    """
    if not (field or "").strip():
        return "Which field?"

    def job(page):
        refusal = _never_touch(page.url, "type into it")
        if refusal:
            return "REFUSED:" + refusal
        fields = page.evaluate(_SCAN)
        target = _match(fields, field)
        if target is None:
            names = ", ".join(f["label"] for f in fields[:10])
            return f"NOMATCH:{names}"
        if target["type"] in _SECRET_TYPES:
            return "SECRET:"
        element = page.locator("input, textarea, select").nth(target["index"])
        if target["type"] == "select-one":
            element.select_option(label=value)
        else:
            element.fill(value)
        return f"OK:{target['label']}"

    try:
        outcome = _do(job, tab="same")
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't fill that: {type(exc).__name__}: {exc}"

    if outcome.startswith("REFUSED:"):
        return outcome[8:]
    if outcome.startswith("NOMATCH:"):
        return (f"There's no field called '{field}' on this page. I can see: "
                f"{outcome[8:]}")
    if outcome.startswith("SECRET:"):
        return ("That's a password field. I only fill those from your vault, "
                "and only on a site you've approved — say 'fill in my password "
                "for this site'.")
    return f"Filled {outcome[3:]}."


def upload_to_form(path: str, field: str = "") -> str:
    """
    Attach a real file to the form's file input — AMBER.

    Through the page's own `input[type=file]`, never through the Windows file
    dialog. The dialog is a native window Playwright cannot see; driving it
    means blind keystrokes into whatever happens to have focus, which is the
    Tab-and-hope failure this whole module exists to avoid.
    """
    target = Path(path.strip().strip('"')).expanduser()
    if not target.exists():
        return f"There's no file at {target}."
    if not target.is_file():
        return f"{target.name} is a folder, not a file."

    def job(page):
        refusal = _never_touch(page.url, "attach anything")
        if refusal:
            return "REFUSED:" + refusal
        inputs = page.locator('input[type="file"]')
        count = inputs.count()
        if count == 0:
            return "NONE:"
        chosen = 0
        if field and count > 1:
            fields = [f for f in page.evaluate(_SCAN) if f["type"] == "file"]
            match = _match(fields, field)
            if match is None:
                return "AMBIGUOUS:" + str(count)
            chosen = [f["label"] for f in fields].index(match["label"])
        elif count > 1:
            return "AMBIGUOUS:" + str(count)
        inputs.nth(chosen).set_input_files(str(target))
        return "OK:"

    try:
        outcome = _do(job, tab="same")
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't attach it: {type(exc).__name__}: {exc}"

    if outcome.startswith("REFUSED:"):
        return outcome[8:]
    if outcome.startswith("NONE:"):
        return ("There's no file upload on this page. If it's behind an "
                "'attach' or 'add file' button, tell me and I'll click it.")
    if outcome.startswith("AMBIGUOUS:"):
        return (f"There are {outcome[10:]} upload boxes on this page. Which "
                f"one — tell me its label and I'll use that.")
    return f"Attached {target.name}."


def submit_form() -> str:
    """
    Submit, then report what the page said back — AMBER.

    Reads the errors AFTER submitting, in the same round trip. A submit that
    reports success while the page is showing "email already registered" is
    the failure this project keeps having in other forms.
    """
    def job(page):
        refusal = _never_touch(page.url, "submit anything")
        if refusal:
            return {"refused": refusal}
        button = None
        for selector in ('button[type="submit"]', 'input[type="submit"]',
                         'button:has-text("Submit")', 'button:has-text("Continue")',
                         'button:has-text("Next")', 'button:has-text("Apply")',
                         'button:has-text("Save")'):
            found = page.locator(selector).first
            try:
                if found.count() and found.is_visible() and found.is_enabled():
                    button = found
                    break
            except Exception:
                continue
        if button is None:
            return {"none": True}
        before = page.url
        button.click()
        page.wait_for_timeout(2500)
        return {"errors": page.evaluate(_ERRORS), "moved": page.url != before,
                "url": page.url}

    try:
        outcome = _do(job, tab="same")
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't submit it: {type(exc).__name__}: {exc}"

    if outcome.get("refused"):
        return outcome["refused"]
    if outcome.get("none"):
        return "I couldn't find a submit button. What's it called?"
    errors = outcome.get("errors") or []
    if errors:
        return ("Submitted, and the form pushed back:\n"
                + "\n".join(f"  - {e}" for e in errors)
                + "\nTell me the corrections and I'll fix them.")
    if outcome.get("moved"):
        return f"Submitted — the page moved on to {outcome.get('url','')[:90]}."
    return ("I clicked submit and the page didn't complain, but it also "
            "didn't move. Worth a look before we assume it went through.")


# ---------------------------------------------------------------------------
# THE ONE THAT TOUCHES A SECRET
# ---------------------------------------------------------------------------
def fill_login_field(site: str = "") -> str:
    """
    Type the vault password into this page's password box — RED.

    THE INVARIANT, RESTATED AS CODE. Jalen does not choose a field here; the
    DOM does. Every one of these is a refusal, not a preference:

      * `input[type="password"]` only, never a field merely NAMED password
      * exactly one of them on the page, or it stops and asks
      * the host must have an explicit vault approval, matched EXACTLY, so
        accounts.google.com.evil.tld inherits nothing
      * the value is never returned, logged, or spoken

    The password is read into a local, typed into the page, and dropped with
    the frame. It is never a tool result — a tool result reaches the model,
    the transcript window and the audit log.

    ONE JOB, ON ONE PAGE, CHECKED AT THE TYPING. The host used to be read and
    approved in one browser job and the secret typed in a second. Anything
    between the two moved the password somewhere nobody had checked: the tab
    closing (the session then hands the next job whichever tab is open), or
    the page navigating itself. Reproduced against a faked browser - the
    password went to evil.example both ways. Now the approval, the vault read
    and the typing are a single job on the tab the work was in (tab="same":
    a replaced tab is refused, never used), and the host is read again
    immediately before typing and must still be the one that was approved.
    """
    from . import vault

    def job(page):
        url = page.url or ""
        host = _host(url)
        if not host:
            return "There's no page open to sign into."
        refusal = _never_touch(url, "type a password")
        if refusal:
            return refusal
        if vault.site_permission(url) != "always":
            return (f"I don't have your approval to use a saved password on "
                    f"{host}. Say 'always allow {host}' if you want me to, "
                    f"and I'll only ever use it on exactly that host.")

        # vault.get_secret is INTERNAL and deliberately not a registered
        # tool: a tool returns its result to the model, and a password must
        # never take that path. This module is code, not the model, so it
        # is the correct caller - the value goes straight from the vault
        # into the page.
        name = (site or host).strip()
        try:
            secret = vault.get_secret(name)
        except vault.VaultLocked:
            return ("Your vault is locked. Say 'unlock my vault' and I'll "
                    "open the passphrase box - you type it, I never hear it.")
        except KeyError:
            return (f"There's nothing saved under '{name}'. Sign in yourself "
                    f"this once and I'll offer to save it afterwards.")
        except Exception as exc:  # noqa: BLE001
            return f"I couldn't open the vault: {type(exc).__name__}"
        try:
            if not secret:
                return f"There's nothing saved under '{name}'."
            # Where the page is NOW, not where it was a moment ago.
            now = page.url or ""
            if (_host(now) != host or vault.site_permission(now) != "always"
                    or _never_touch(now, "type a password")):
                return (f"The page moved off {host} while I was getting your "
                        f"password, so I didn't type it anywhere. Open the "
                        f"sign-in page again and ask me once it's there.")
            boxes = page.locator('input[type="password"]')
            count = boxes.count()
            if count == 0:
                return "There's no password box on this page."
            if count > 1:
                return (f"There are {count} password boxes on this page — "
                        f"that usually means it's a sign-up form asking you "
                        f"to confirm. I won't guess which is which; type it "
                        f"yourself this once.")
            boxes.first.fill(secret)
            return ("Typed your password in. If there's a code or a "
                    "checkbox, that part's yours — tell me when to carry on.")
        finally:
            del secret          # out of scope with this frame, always

    try:
        return _do(job, tab="same")
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't see the page: {type(exc).__name__}"


REGISTRY: dict[str, Any] = {
    "inspect_form": inspect_form,
    "fill_form_field": fill_form_field,
    "upload_to_form": upload_to_form,
    "submit_form": submit_form,
    "form_errors": form_errors,
    "fill_login_field": fill_login_field,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
