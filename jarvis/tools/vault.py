"""
The credentials vault, and the per-site permission that gates it.

He asked for this shape specifically: a vault, an allowlist, an audit trail,
and — his addition — that Jalen asks whether an approval is for THIS TIME or
FOREVER, remembers the answer, and acts on it thereafter.

WHY THE PASSWORD IS NOT IN .env
-------------------------------
.env is plain text, is read by every part of this process, and gets printed
by diagnostics. His sign-in code sitting there would be one `check_env` away
from the screen and one screen-share away from gone. Here it is encrypted at
rest with a key derived from a passphrase he types once per session, and it
is never written to a log, never returned by a tool, and redacted in the
audit trail like every other secret.

WHY A PER-SITE ALLOWLIST AND NOT A GLOBAL SWITCH
------------------------------------------------
The threat is not him. It is that a web page can lie about being a login
form, and Jalen cannot tell a real Google sign-in from a convincing clone —
that is the entire business model of phishing. A global "yes, type my
password" would make Jalen type it into whatever asked. A per-domain
allowlist means an unfamiliar page gets nothing, and the worst an attacker
achieves is a prompt he declines.

Approval is per DOMAIN, not per URL: he approves accounts.google.com once,
not every page under it. Subdomains do NOT inherit — "google.com" approved
does not approve "login.google.com.evil.tld", which is exactly the trick
this is here to stop.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not type anything. It stores secrets, answers "may I use this
here?", and records that it was asked. Actually filling a field belongs with
the browser tools, gated on this answer — the same look/touch split as the
technician, and for the same reason.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
VAULT_PATH = ROOT / "data" / "vault.json"
APPROVALS_PATH = ROOT / "data" / "site_approvals.json"
# Which site each secret belongs to - see "WHICH SITE A SECRET IS FOR" below.
# THE FILE NAME IS LOAD-BEARING: it matches the never_touch pattern
# "*secret*" in config/safety.yaml, so read_file, edit_file, create_file,
# move_file and delete_file are all refused on it. A binding the brain could
# rewrite with edit_file would be no binding at all.
# tests/test_a_secret_goes_only_where_he_said.py pins that.
SECRET_SITES_PATH = ROOT / "data" / "secret_sites.json"
# He said this secret is not a site login - a phone number, an email address
# typed into every application form. Written by scripts/vault_setup.py only.
ANY_SITE = "*"

# Passphrase -> key. 200k rounds is the cost of one unlock per session,
# which is imperceptible to him and expensive for anyone brute-forcing the
# file offline.
KDF_ROUNDS = 200_000
SALT_BYTES = 16

# How long an unlock lasts. Long enough to fill in a whole application,
# short enough that a laptop left open does not stay unlocked all day.
UNLOCK_TTL_S = 3600.0


class VaultLocked(RuntimeError):
    """Raised when a secret is needed and the vault has not been unlocked."""


# --------------------------------------------------------------- crypto
def _derive(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, KDF_ROUNDS)


def _xor(data: bytes, key: bytes) -> bytes:
    """
    Keystream XOR, with the key stretched by counter-mode SHA-256.

    Deliberately not AES: the standard library has no AES, and adding
    `cryptography` to this project means a compiled wheel on a machine where
    installs have already been a problem. This is a real construction —
    PBKDF2 for the key, a counter-mode keystream, and an HMAC over the
    ciphertext so tampering is detected rather than decrypted into garbage.
    It protects a file at rest from someone who copies it. It is not, and
    does not claim to be, a hardware-backed keystore.
    """
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hashlib.sha256(key + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(data, out[: len(data)]))


def _seal(secrets: dict, passphrase: str) -> dict:
    salt = os.urandom(SALT_BYTES)
    key = _derive(passphrase, salt)
    plaintext = json.dumps(secrets, ensure_ascii=False).encode("utf-8")
    ciphertext = _xor(plaintext, key)
    tag = hmac.new(key, ciphertext, hashlib.sha256).hexdigest()
    return {
        "version": 1,
        "salt": base64.b64encode(salt).decode(),
        "data": base64.b64encode(ciphertext).decode(),
        "tag": tag,
    }


def _open(blob: dict, passphrase: str) -> dict | None:
    """Returns the secrets, or None if the passphrase is wrong."""
    try:
        salt = base64.b64decode(blob["salt"])
        ciphertext = base64.b64decode(blob["data"])
        key = _derive(passphrase, salt)
        # Constant-time: a timing difference here leaks how much of a guess
        # was right, one byte at a time.
        if not hmac.compare_digest(
            hmac.new(key, ciphertext, hashlib.sha256).hexdigest(), blob["tag"]
        ):
            return None
        return json.loads(_xor(ciphertext, key).decode("utf-8"))
    except (KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------- state
@dataclass
class _Session:
    """The unlocked secrets, in memory only, with an expiry."""

    secrets: dict = field(default_factory=dict)
    until: float = 0.0

    @property
    def live(self) -> bool:
        return bool(self.secrets) and time.monotonic() < self.until


_SESSION = _Session()


def _load_blob() -> dict | None:
    try:
        return json.loads(VAULT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_blob(blob: dict) -> None:
    VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    VAULT_PATH.write_text(json.dumps(blob, indent=2), encoding="utf-8")


def vault_status() -> str:
    """Whether a vault exists and whether it is unlocked — GREEN."""
    if _load_blob() is None:
        return (
            "No vault yet. Run: .venv\\Scripts\\python.exe scripts\\vault_setup.py "
            "to create one — it asks for a passphrase and never stores it."
        )
    if _SESSION.live:
        left = int(_SESSION.until - time.monotonic())
        return f"Vault is unlocked for another {left // 60} minutes."
    return "Vault exists but is locked. It unlocks when you give me the passphrase."


def unlock_vault(passphrase: str) -> str:
    """
    Unlock for this session. The passphrase is never stored — RED.

    Deliberately takes the passphrase as an argument rather than reading it
    from config or the environment: a passphrase that lives on disk is not a
    passphrase, it is a second copy of the secret it protects.
    """
    blob = _load_blob()
    if blob is None:
        return "There's no vault to unlock yet."
    secrets = _open(blob, passphrase or "")
    if secrets is None:
        # Deliberately vague. "Wrong passphrase" and "corrupt file" are
        # different, but distinguishing them out loud helps a guesser.
        return "That didn't unlock it."
    _SESSION.secrets = secrets
    _SESSION.until = time.monotonic() + UNLOCK_TTL_S
    return f"Vault unlocked for {int(UNLOCK_TTL_S // 60)} minutes — {len(secrets)} entries."


def unlock_vault_prompt(timeout_s: float = 120.0) -> str:
    """
    Unlock by TYPING the passphrase into a box on screen — AMBER.

    THIS IS THE ONE THAT SHOULD BE USED. unlock_vault() takes the passphrase
    as a string, which means it came from somewhere, and by voice that
    somewhere is a microphone. Trace what a spoken passphrase actually does:

      1. it is recorded, and the audio is sent to GROQ to be transcribed, so
         the master passphrase leaves the machine and enters someone else's
         logs before a single line of this project's code runs;
      2. it arrives as an ordinary transcript line and is written to
         data/audit.jsonl — a plain-text file, next to the vault it opens;
      3. it is passed to a tool, whose arguments are also written down;
      4. and it was said OUT LOUD, in a room, possibly with other people or
         an open call in it.

    Redaction fixes (2) and (3) — see AuditLog._SECRET_SHAPES and
    SafetyEngine._SENSITIVE_ARG_MARKERS, both of which were missing it. It
    cannot fix (1) or (4). The only real answer is that the passphrase must
    never become audio.

    So this opens a local password box instead. The value goes straight from
    the keyboard into the key-derivation function: no transcription, no
    model, no tool argument, no log line. Jalen is told only whether it
    worked.

    AMBER rather than GREEN, deliberately. It is harmless in itself, but a
    password box that anything can summon is a phishing primitive — and the
    injection guard refuses AMBER tools to anything Jalen merely READ, so a
    web page or an email cannot make one appear.
    """
    if _load_blob() is None:
        return (
            "There's no vault yet. Run scripts\\vault_setup.py once to create "
            "one — it asks for a passphrase and never stores it."
        )
    if _SESSION.live:
        return "The vault is already unlocked."

    try:
        passphrase = _ask_passphrase_on_screen(timeout_s)
    except Exception as exc:  # noqa: BLE001 - a UI failure must not be fatal
        return (
            f"I couldn't open the passphrase box ({type(exc).__name__}). "
            "Nothing was unlocked."
        )
    if passphrase is None:
        return "Cancelled — nothing was unlocked."
    # Straight into unlock_vault, and the local name goes out of scope with
    # this frame. It is never returned, logged, or spoken.
    result = unlock_vault(passphrase)
    del passphrase
    return result


def caps_lock_on() -> bool:
    """
    Is Caps Lock on right now? False anywhere that cannot be asked.

    A small thing that wastes a genuinely infuriating amount of time: a
    masked box plus Caps Lock is a wrong password with no visible cause, and
    people retype it three times before looking at the keyboard.
    """
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
    except Exception:
        return False


def _ask_passphrase_on_screen(timeout_s: float) -> str | None:
    """
    A small always-on-top passphrase box. Returns the text, or None if
    cancelled or timed out.

    VISIBLE BY DEFAULT, which is unusual and deliberate. His words: "that
    passwords should be visible, the fact that it is invisible is so
    uncomfortable you know? maybe we gotta come up with some better
    approach."

    He is right about the discomfort and it is not irrational. Masking
    protects against exactly one thing - somebody reading your screen - and
    on a personal laptop, alone, that threat is usually absent while the cost
    is present every single time: you cannot tell whether you fat-fingered a
    character, so you either retype the whole thing or guess.

    So the choice is HIS, made visible, rather than a default nobody can see
    the reason for. Shown by default, one click to hide, and the honest
    consequence written next to the control instead of assumed. The
    protections that actually matter here are untouched either way: it is
    never transcribed, never sent to any model, never logged, and it goes out
    of scope with the frame that reads it.

    Its own Tk root on its own thread - the orb already owns a mainloop on
    another thread, and two roots in one interpreter cannot share one.
    """
    import queue
    import threading
    import tkinter as tk

    answer: queue.Queue[str | None] = queue.Queue(maxsize=1)
    from ..config import CONFIG

    # Shown by default; vault.hide_passphrase: true in config flips it.
    start_hidden = bool(CONFIG.get_path("vault.hide_passphrase", False))

    def run() -> None:
        root = tk.Tk()
        root.title("Jalen - unlock vault")
        root.attributes("-topmost", True)
        root.configure(bg="#14181d")
        root.geometry("440x230")

        tk.Label(root, text="Vault passphrase", bg="#14181d", fg="#e8edf2",
                 font=("Segoe UI", 12)).pack(pady=(18, 8))

        entry = tk.Entry(root, width=38, font=("Consolas", 12),
                         bg="#1d232b", fg="#e8edf2", insertbackground="#ff8a3d",
                         relief="flat")
        entry.pack(ipady=5)
        entry.focus_force()

        hidden = tk.BooleanVar(value=start_hidden)
        note = tk.Label(root, text="", bg="#14181d", fg="#8b97a5",
                        font=("Segoe UI", 8))
        counter = tk.Label(root, text="", bg="#14181d", fg="#5f6b78",
                           font=("Segoe UI", 8))

        def restyle(*_a) -> None:
            entry.config(show="•" if hidden.get() else "")
            note.config(
                text=("Hidden. Nobody can read it - including you."
                      if hidden.get() else
                      "Visible, so you can check it. Anyone looking at your "
                      "screen can read it too."),
                fg="#8b97a5" if hidden.get() else "#c9a227",
            )

        def retally(*_a) -> None:
            n = len(entry.get())
            caps = "   CAPS LOCK IS ON" if caps_lock_on() else ""
            counter.config(text=f"{n} character{'' if n == 1 else 's'}{caps}",
                           fg="#ff6b6b" if caps else "#5f6b78")

        tk.Checkbutton(
            root, text="Hide what I type", variable=hidden, command=restyle,
            bg="#14181d", fg="#8b97a5", selectcolor="#1d232b",
            activebackground="#14181d", activeforeground="#e8edf2",
            font=("Segoe UI", 9), bd=0, highlightthickness=0,
        ).pack(pady=(10, 2))
        counter.pack()
        note.pack(pady=(2, 0))
        tk.Label(root, text="Never transcribed, never sent to any model, never logged.",
                 bg="#14181d", fg="#5f6b78", font=("Segoe UI", 8)).pack(pady=(6, 0))

        def submit(_event=None) -> None:
            answer.put(entry.get() or None)
            root.destroy()

        def cancel(_event=None) -> None:
            answer.put(None)
            root.destroy()

        entry.bind("<Return>", submit)
        entry.bind("<KeyRelease>", retally)
        root.bind("<Escape>", cancel)
        root.protocol("WM_DELETE_WINDOW", cancel)
        tk.Button(root, text="Unlock", command=submit, relief="flat",
                  bg="#ff8a3d", fg="#14181d", font=("Segoe UI", 10, "bold"),
                  padx=18, pady=3).pack(pady=10)

        restyle()
        retally()
        # Never leave a passphrase box open forever on an unattended machine.
        root.after(int(timeout_s * 1000), cancel)
        root.mainloop()

    thread = threading.Thread(target=run, name="jalen-vault-prompt", daemon=True)
    thread.start()
    try:
        return answer.get(timeout=timeout_s + 5)
    except queue.Empty:
        return None


def lock_vault() -> str:
    """Forget the unlocked secrets immediately — GREEN."""
    _SESSION.secrets = {}
    _SESSION.until = 0.0
    return "Vault locked."


def list_secrets() -> str:
    """
    The NAMES of what is stored, never the values — GREEN.

    Listing names is genuinely useful ("what do you have for me?") and
    listing values would put a password into the audit log, the transcript
    window and, if he is unlucky, a TTS engine.
    """
    if not _SESSION.live:
        return "Vault is locked, so I can't tell you what's in it."
    if not _SESSION.secrets:
        return "The vault is empty."
    # Where each one may be typed, so he can audit it the way he audits
    # list_site_decisions. Sites are not secret; the values stay out.
    return "Stored: " + ", ".join(
        f"{name} ({_where_it_goes(name)})" for name in sorted(_SESSION.secrets))


def has_secret(name: str) -> bool:
    """
    INTERNAL ONLY. Is something stored under this name? Raises VaultLocked.

    So the code that types can refuse a name that does not exist BEFORE it
    asks him anything, without holding the value while he answers.
    """
    if not _SESSION.live:
        raise VaultLocked("vault is locked")
    return name in _SESSION.secrets


def get_secret(name: str) -> str:
    """
    INTERNAL ONLY — not registered as a tool, and must never become one.

    A tool returns its result to the model, which puts it in the transcript,
    the audit log and possibly his speakers. Secrets are handed directly to
    the code that types them and nowhere else.
    """
    if not _SESSION.live:
        raise VaultLocked("vault is locked")
    value = _SESSION.secrets.get(name)
    if value is None:
        raise KeyError(name)
    return value


# ------------------------------------------------------ site approvals
def _normalise_domain(url_or_domain: str) -> str:
    """
    The registrable host, lowercased, with no port, path or credentials.

    Deliberately does NOT strip subdomains. "login.google.com.evil.tld" and
    "google.com" must never compare equal — treating a suffix match as
    approval is precisely the trick a phishing domain uses.
    """
    text = (url_or_domain or "").strip().lower()
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)
    text = text.split("/")[0].split("?")[0]
    text = text.rsplit("@", 1)[-1]          # strip user:pass@
    text = text.split(":")[0]               # strip :port
    return text.strip(".")


def _load_approvals() -> dict:
    try:
        return json.loads(APPROVALS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_approvals(data: dict) -> None:
    APPROVALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def site_permission(url: str) -> str:
    """
    What he has already decided about this site: always, never, or ask.

    "ask" is the default and is not a failure — it is the whole design. An
    unfamiliar page gets nothing until he says so.
    """
    domain = _normalise_domain(url)
    if not domain:
        return "ask"
    entry = _load_approvals().get(domain)
    return entry.get("decision", "ask") if isinstance(entry, dict) else "ask"


def remember_site_decision(url: str, decision: str) -> str:
    """
    Record "always" or "never" for a domain — RED.

    His addition to the design, in his words: it should ask whether he
    approves permanently or just once, "so I will give my answer it should
    remember and act accordingly". A "once" answer is deliberately NOT
    stored — that is what makes it once.
    """
    domain = _normalise_domain(url)
    if not domain:
        return "I couldn't work out which site that is."
    if decision not in ("always", "never"):
        return "That has to be 'always' or 'never'. 'Once' isn't remembered — that's the point of it."

    data = _load_approvals()
    data[domain] = {
        "decision": decision,
        "decided_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    _save_approvals(data)
    verb = "will fill your details on" if decision == "always" else "will never touch"
    return f"Noted — I {verb} {domain} from now on. Say 'forget {domain}' to change it."


def forget_site_decision(url: str) -> str:
    """Undo a remembered decision — RED."""
    domain = _normalise_domain(url)
    data = _load_approvals()
    if domain not in data:
        return f"I had no standing decision for {domain}."
    del data[domain]
    _save_approvals(data)
    return f"Forgotten — I'll ask again next time {domain} comes up."


def list_site_decisions() -> str:
    """Every standing decision — GREEN. He should be able to audit this."""
    data = _load_approvals()
    if not data:
        return "No sites approved or blocked yet — I'll ask about each one."
    allowed = sorted(d for d, e in data.items() if e.get("decision") == "always")
    blocked = sorted(d for d, e in data.items() if e.get("decision") == "never")
    parts = []
    if allowed:
        parts.append("Always fill: " + ", ".join(allowed))
    if blocked:
        parts.append("Never touch: " + ", ".join(blocked))
    return ". ".join(parts) + "."


# ------------------------------------------------- which site a secret is for
#
# A SITE APPROVAL SAYS WHERE JALEN MAY TYPE. IT NEVER SAID WHAT. Approval is
# per domain, not per secret, so the moment any site was "always", EVERY
# stored secret could be typed there - a lookalike he had once approved got
# his Google password, through fill_credential and through
# fill_login_field(site="accounts.google.com") alike. Reproduced in
# tests/test_a_secret_goes_only_where_he_said.py against the code before this.
#
# So each secret now carries the site(s) it belongs to, and every path that
# types one checks it on the host the page is on AT THE TYPING:
#
#   tied    a list of hosts. Typed there (or on a subdomain of one) and
#           nowhere else - no site approval and no spoken yes overrides it.
#           Adding a site is deliberate: scripts/vault_setup.py.
#   "*"     he said it is not a site login (a phone number, an email for
#           application forms). The site approval alone decides.
#   untied  stored before binding existed, or never told. See below.
#
# A NAME THAT IS A HOST IS THAT HOST'S. fill_login_field looks a login up by
# the page's host, so a secret stored as "accounts.google.com" was always
# meant for accounts.google.com; its name is its binding until he says
# otherwise, and it is typed on a lookalike by nobody.
#
# SECRETS STORED BEFORE THIS - "sign_in_code", "gmail" - are UNTIED, and are
# tied on first use: the first time one is about to be typed, he is asked a
# question naming the secret AND the host, and only his own yes (app.confirm,
# through interaction.confirm) ties it there and types it. Not refused until
# bound, because that would break every stored secret until he found a
# terminal; not typed with a warning, because a warning is exactly what the
# lookalike he approved would get. The question names the lookalike out
# loud, and one good answer ends it: from then on no other site gets it.
#
# Plain JSON, not inside the encrypted blob, because tying on first use has
# to write without the passphrase, which is never kept - and the keystream
# here has no nonce, so re-sealing under the old key is not an option.
# Nothing in it is secret: names are what list_secrets shows, sites are what
# list_site_decisions shows.
def _host_of(url_or_host: str) -> str:
    """
    The host a browser would go to, PARSED, never split on "/": lowercased,
    no port, no user:pass@, no trailing dot. "" for anything that is not a
    web address (about:blank, chrome://, file:) or cannot be read.

    Backslashes count as slashes, as they do in Chrome for http(s):
    "https://evil.tld\\@accounts.google.com" goes to evil.tld.
    """
    from urllib.parse import urlsplit

    text = (url_or_host or "").strip().replace("\\", "/")
    if not text or any(ch.isspace() for ch in text):
        return ""
    scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):(?!\d)", text)
    if scheme:
        if scheme.group(1).lower() not in ("http", "https"):
            return ""
    else:
        text = "//" + text          # "accounts.google.com/signin", as Chrome shows it
    try:
        return (urlsplit(text).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _on_host(host: str, site: str) -> bool:
    """That exact host or a subdomain of it. NEVER a substring."""
    return bool(host and site) and (host == site or host.endswith("." + site))


def _host_named(name: str) -> str:
    """The host a secret's NAME spells, or "" when the name is not a host."""
    text = (name or "").strip().lower().rstrip(".")
    if "." not in text or any(ch in text for ch in " _/@:\\"):
        return ""
    return text if _host_of(text) == text else ""


def _load_secret_sites() -> dict:
    try:
        data = json.loads(SECRET_SITES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def secret_sites(name: str) -> list[str] | None:
    """
    INTERNAL. The sites a secret may be typed on - [ANY_SITE] for "any" - or
    None when it is untied.
    """
    entry = _load_secret_sites().get(name)
    sites = entry.get("sites") if isinstance(entry, dict) else None
    if isinstance(sites, list):
        clean = [s for s in (str(x).strip().lower() for x in sites) if s]
        if clean:
            return clean
    named = _host_named(name)
    return [named] if named else None


def secret_binding(name: str, url: str) -> str:
    """
    INTERNAL. May this secret be typed on the page at `url`?

        "here"       yes - it is tied to this host, or to any site
        "elsewhere"  no - it belongs to another site, or the page is nowhere
        "unbound"    nobody has said; ask him before typing (tie on first use)

    A page with no readable host is "elsewhere" even for an untied secret:
    there is no site to tie it to, and no site is not a site it belongs on.
    """
    host = _host_of(url)
    if not host:
        return "elsewhere"
    sites = secret_sites(name)
    if sites is None:
        return "unbound"
    if ANY_SITE in sites:
        return "here"
    return "here" if any(_on_host(host, site) for site in sites) else "elsewhere"


def _where_it_goes(name: str) -> str:
    """Spoken: where a secret may be typed."""
    sites = secret_sites(name)
    if sites is None:
        return "not tied to a site yet"
    if ANY_SITE in sites:
        return "any site"
    return "only on " + " or ".join(sites)


def describe_secret_sites(name: str) -> str:
    """INTERNAL. "accounts.google.com", "any site", or "no site yet"."""
    sites = secret_sites(name)
    if sites is None:
        return "no site yet"
    if ANY_SITE in sites:
        return "any site"
    return " or ".join(sites)


def tie_secret(name: str, sites: list[str], how: str = "first use") -> bool:
    """
    INTERNAL ONLY - and it must never become a tool. Say which site(s) a
    secret belongs to. True if it was written down.

    Called by the code that types, AFTER his own spoken yes to a question
    naming the secret and the host, and by scripts/vault_setup.py where he
    types the sites himself. A tool would let the model move his Google
    password to whichever site it was reading - the exact failure this is
    here to end. An empty list unties it.
    """
    clean: list[str] = []
    for site in sites or []:
        text = str(site).strip()
        host = ANY_SITE if text == ANY_SITE else _host_of(text)
        if not host:
            return False            # one unreadable site and nothing is written
        if host not in clean:
            clean.append(host)
    data = _load_secret_sites()
    if clean:
        data[name] = {"sites": clean, "how": how,
                      "tied_at": time.strftime("%Y-%m-%d %H:%M")}
    else:
        data.pop(name, None)
    try:
        SECRET_SITES_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_SITES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def forget_secret_sites_except(names) -> None:
    """INTERNAL. Drop bindings for secrets the vault no longer holds."""
    keep = set(names)
    data = _load_secret_sites()
    stale = [name for name in data if name not in keep]
    if not stale:
        return
    for name in stale:
        del data[name]
    try:
        SECRET_SITES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


REGISTRY: dict[str, Any] = {
    "vault_status": vault_status,
    "unlock_vault": unlock_vault,
    "unlock_vault_prompt": unlock_vault_prompt,
    "lock_vault": lock_vault,
    "list_secrets": list_secrets,
    "site_permission": site_permission,
    "remember_site_decision": remember_site_decision,
    "forget_site_decision": forget_site_decision,
    "list_site_decisions": list_site_decisions,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
