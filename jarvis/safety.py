"""
The gate. Every action Jalen takes passes through classify() before it runs.

Design rule: this module decides, it does not ask. Asking is the caller's job
(voice, Telegram, or the console), because the question has to reach whichever
channel O'ktam is actually using. That keeps the policy in one testable place.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class Tier(str, Enum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    BLACK = "black"


@dataclass
class Verdict:
    tier: Tier
    tool: str
    summary: str
    reason: str
    requires_confirmation: bool
    announce: bool
    blocked: bool
    detail: dict[str, Any]

    @property
    def allowed_immediately(self) -> bool:
        return self.tier is Tier.GREEN


# Argument names that carry a location. "path", "file" and "dir" were the
# only ones inspected, so project_status(folder=...), open_in(target=...) and
# copy_file/move_file(destination=...) were never checked at all - and
# copying a file INTO ~/.ssh is how a key gets planted. Listed from every
# argument in TOOL_SPECS that the old three missed. "location" is left out
# on purpose: create_calendar_event(location="Password workshop") is a
# place, not a file, and a value that really is a path is still caught by
# _LOOKS_LIKE_A_PATH below.
_PATHISH_KEYS = ("path", "file", "dir", "folder", "target", "source", "dest")

# A value that IS a path, whatever its argument is called: a drive letter, a
# home-relative path, a %VARIABLE%, a UNC or long-path prefix, a relative
# ./ or ../, or a file: URL (open_url hands those straight to os.startfile)
# - anchored at the START, so a sentence that mentions C:/Windows halfway
# through is not one.
_LOOKS_LIKE_A_PATH = re.compile(
    r"^(?:[a-zA-Z]:[\\/]|~(?:[\\/]|$)|%[^%\s]+%|\\\\|//|\.{1,2}[\\/]|file:)",
    re.IGNORECASE)

# Any other URL is not a path, even in a path-ish argument:
# remember_alias(target="https://passwords.google.com") names a web page.
# Two or more scheme letters, so "C://Windows" is still a drive.
_WEB_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]+://")


# Argument names that carry a web destination - every one in TOOL_SPECS:
# open_url/web_read/site_permission(url), search_site/fill_login_field
# (site), and browse_to(url).
_SITEISH_KEYS = ("url", "site", "host", "domain", "link", "href")

# A value that IS a web address whatever its argument is called, e.g.
# remember_alias(target="https://paypal.com"). Whole value, no spaces - a
# sentence containing a link is prose.
_WHOLE_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]+://\S+$")


def _hostname(address: str) -> str:
    """
    The host a browser would actually go to, lowercased, trailing dot gone:
    https://someone:pw@PayPal.com.:443/x -> paypal.com. A bare "payme.uz"
    is a host too. Raises ValueError for an address urlsplit cannot read.
    """
    from urllib.parse import urlsplit

    s = address.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", s):
        s = "//" + s
    host = urlsplit(s).hostname or ""
    return host.rstrip(".").lower()


def _expanded_path(raw: str) -> str:
    """
    The argument as the tool will read it, before it is made absolute: the
    same expansion the tools do (filesystem._resolve: expandvars, then
    expanduser), plus what they get for free from the OS - ".." resolved,
    the \\\\?\\ long-path prefix and a file: scheme removed. Pure string
    operations; nothing here touches the filesystem.
    """
    s = str(raw).strip()
    if s[:5].lower() == "file:":
        from urllib.parse import unquote

        s = unquote(s[5:])
        s = re.sub(r"^[\\/]+(?=[a-zA-Z]:)", "", s)
    for prefix in ("\\\\?\\", "//?/", "\\\\.\\", "//./"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return os.path.normpath(os.path.expandvars(os.path.expanduser(s)))


def _canonical_path(raw: str) -> str:
    """The path a tool would actually open, as a comparable string."""
    s = os.path.normpath(os.path.abspath(_expanded_path(raw)))
    return s.replace("\\", "/").rstrip("/").lower()


# EXACTLY the spellings messaging._resolve turns into get_me() - his own
# Saved Messages - compared the same way it compares them (stripped,
# lowercased, nothing else). Identical on purpose, and asserted by running
# the real resolver in tests/test_preapproval_is_exact.py: if the gate
# approved a spelling the resolver did not map to him, the approval and the
# send would be about two different chats, which is the exact split the
# substring matching below used to create.
SELF_CHAT_ALIASES = ("me", "myself", "saved messages", "saved", "my notes")


def _is_self_chat(raw: Any) -> bool:
    return str(raw or "").strip().lower() in SELF_CHAT_ALIASES


def _simplify(name: str) -> str:
    """
    Lowercase, strip punctuation and collapse spaces.

    So that "AI engineering & Machine learning", "ai engineering and machine
    learning" and "AI Engineering &amp; Machine Learning" are one string. He
    names his own channel casually and inconsistently, and a destination
    allowlist that only matches an exact title is one he would have to fight.
    """
    import re as _re

    text = (name or "").lower().replace("&amp;", "and").replace("&", "and")
    text = _re.sub(r"[^a-z0-9 ]+", " ", text)
    return _re.sub(r"\s+", " ", text).strip()


class SafetyEngine:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        tiers = cfg.get("safety_tiers", {})
        self._tier_map: dict[str, Tier] = {}
        for tier in (Tier.GREEN, Tier.AMBER, Tier.RED, Tier.BLACK):
            for tool in (tiers.get(tier.value) or {}).get("tools", []) or []:
                self._tier_map[tool] = tier

        self._reasons = {
            t: (tiers.get(t.value) or {}).get("reason", "") for t in Tier
        }
        never = tiers.get("never_touch") or {}
        self._never_paths = [self._norm(p) for p in never.get("paths", [])]
        self._never_patterns = never.get("patterns", []) or []
        self._never_apps = [a.lower() for a in never.get("apps", []) or []]
        self._never_domains = never.get("domains", []) or []

        guard = tiers.get("injection_guard") or {}
        self.guard_enabled = guard.get("enabled", True)
        self._markers = [m.lower() for m in guard.get("suspicious_markers", []) or []]
        self._block_red_from_content = guard.get("block_red_tools_from_read_content", True)

        self.posture = cfg.get_path("safety.posture", "irreversible_only")
        self.paranoid = bool(cfg.get_path("safety.paranoid_first_week", True))

        # Destinations he has pre-approved for sending without being asked.
        # Normalised once here rather than on every classify() call, which
        # runs on every tool the brain touches.
        self._preapproved = [
            _simplify(name)
            for name in (cfg.get_path("telegram.personal.send_without_asking_to", []) or [])
        ]

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _norm(p: str) -> str:
        return str(Path(str(p)).as_posix()).rstrip("/").lower()

    def _touches_forbidden_path(self, args: dict[str, Any]) -> str | None:
        """
        Does any argument point at something on the never-touch list?

        IT USED TO PROTECT ONE SPELLING OF EACH PATH. The raw argument was
        compared, while every tool that touches a file expands it first
        (filesystem._resolve: expandvars + expanduser), so the gate and the
        tool disagreed about which file was being touched. Reproduced
        against this engine: C:\\Users\\user\\.ssh\\config was BLACK, and
        ~/.ssh/config, %USERPROFILE%/.ssh/config, a "..\\" detour and the
        \\\\?\\ long-path form were all GREEN. And only argument names
        containing path/file/dir were inspected, so folder= and target=
        walked straight past it, with origin=content too.

        Now a path is canonicalised the way the tools resolve it before it
        is compared, and any argument whose NAME suggests a location, or
        whose WHOLE value looks like a path, is inspected. Prose that merely
        mentions C:/Windows is left alone - a guard that fires on sentences
        gets switched off.
        """
        candidates: list[str] = []
        for key, value in (args or {}).items():
            if not isinstance(value, str) or not value.strip() or "\n" in value:
                continue
            v = value.strip()
            if _LOOKS_LIKE_A_PATH.match(v):
                candidates.append(value)
            elif (any(hint in key.lower() for hint in _PATHISH_KEYS)
                    and not _WEB_URL.match(v)):
                candidates.append(value)
        for raw in candidates:
            try:
                norm = _canonical_path(raw)
                written = _expanded_path(raw).replace("\\", "/").lower()
            except Exception:
                # Fail CLOSED. A path this cannot read is not a path it has
                # checked.
                return "a path I couldn't check safely, so I won't touch it"
            for blocked in self._never_paths:
                if norm == blocked or norm.startswith(blocked + "/"):
                    return f"path is on the never-touch list ({blocked})"
            # Every component, not only the final name: a protected name
            # used as a FOLDER ("Mother credentials" moved off the Desktop,
            # a "passwords" folder of plain .txt files) protects what is
            # inside it too. Only the components he WROTE (after expansion),
            # never the working directory a relative path is resolved
            # against - or Jalen started from a folder called
            # "credentials-app" would refuse every relative path.
            for part in written.split("/"):
                for pattern in self._never_patterns:
                    if fnmatch.fnmatch(part, pattern.lower()):
                        return f"filename matches protected pattern '{pattern}'"
        return None

    def _touches_forbidden_target(self, args: dict[str, Any]) -> str | None:
        blob = " ".join(str(v) for v in (args or {}).values()).lower()
        for app in self._never_apps:
            if app in blob:
                return f"targets a password manager ({app})"
        # Protected sites are matched on the HOSTNAME of a destination, not
        # as a substring of every argument joined together. The substring
        # turned "*.paypal.com" into ".paypal.com", so the bare paypal.com
        # people actually type was never protected - while a message that
        # merely SAID "I paid with click.uz" was refused. Found by the
        # adversarial review of browse_to, which lets his signed-in Chrome
        # go anywhere and makes this list the only hard stop left.
        for key, value in (args or {}).items():
            if not isinstance(value, str) or not value.strip():
                continue
            v = value.strip()
            if not (any(hint in key.lower() for hint in _SITEISH_KEYS)
                    or _WHOLE_URL.match(v)):
                continue
            try:
                host = _hostname(v)
            except ValueError:
                return "a web address I couldn't check safely, so I won't open it"
            if pattern := self._protected_pattern(host):
                return f"targets a protected domain ({pattern})"
        return None

    def _protected_pattern(self, host: str) -> str | None:
        """The never-touch domain pattern this host falls under, if any."""
        if not host:
            return None
        for domain in self._never_domains:
            pattern = str(domain).strip().lower().rstrip(".")
            if not pattern:
                continue
            if any(ch in pattern for ch in "*?["):
                # "*.paypal.com" means paypal.com AND its subdomains; a glob
                # alone would only match the subdomains.
                if fnmatch.fnmatchcase(host, pattern) or (
                        pattern.startswith("*.")
                        and fnmatch.fnmatchcase(host, pattern[2:])):
                    return domain
            elif host == pattern or host.endswith("." + pattern):
                return domain
        return None

    def protected_domain(self, address: str) -> str | None:
        """
        Is this web address on the never-touch domain list? For callers that
        learn a destination AFTER classify ran - browse_to re-checks where a
        redirect actually landed with this. Returns the matching pattern, or
        None. An address that cannot be parsed counts as protected: a caller
        asking "may I stay here?" gets no from something it cannot read.
        """
        try:
            host = _hostname(address or "")
        except ValueError:
            return "an address I couldn't read"
        return self._protected_pattern(host)

    def scan_for_injection(self, text: str) -> list[str]:
        """Return the suspicious phrases found in content Jalen has READ."""
        if not self.guard_enabled or not text:
            return []
        low = text.lower()
        return [m for m in self._markers if m in low]

    # --------------------------------------------------------------- classify
    def classify(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        summary: str = "",
        origin: str = "user",
        named_by_him: str = "",
    ) -> Verdict:
        """
        origin:
          "user"    - O'ktam said it (voice) or sent it (authenticated Telegram)
          "content" - derived from something Jalen read: email, web page, file.
                      These can never trigger RED tools. This is the
                      prompt-injection defence and it is not overridable.

        named_by_him:
          the destination HIS instruction named ("Saved Messages"), from
          taint.named(). Passed in rather than read here so this stays a
          function of its arguments. It widens exactly one thing - see the
          Saved Messages exception in step 2 - and nothing else.
        """
        args = args or {}
        summary = summary or self._describe(tool, args)
        detail = {"tool": tool, "args": self._redact(args), "origin": origin}

        # 1. Hard path/target blocks come before anything else.
        for check in (self._touches_forbidden_path, self._touches_forbidden_target):
            if reason := check(args):
                return Verdict(Tier.BLACK, tool, summary, reason, False, False, True, detail)

        base = self._tier_map.get(tool)
        if base is None:
            # Unknown tool: treat as AMBER, never silently green. Log it so the
            # tool can be classified properly later.
            base = Tier.AMBER
            detail["unclassified"] = True

        if base is Tier.BLACK:
            return Verdict(
                Tier.BLACK, tool, summary,
                self._reasons.get(Tier.BLACK, "Refused unconditionally."),
                False, False, True, detail,
            )

        # 2. Prompt-injection: content-derived requests cannot do irreversible things.
        if origin == "content" and self._block_red_from_content and base in (Tier.RED, Tier.AMBER):
            # EXCEPT INTO HIS OWN NOTEBOOK, WHEN HE SAID SO.
            #
            # Refusing this cost him his most-asked flow - "read Andres's
            # email and put a summary in my Saved Messages" - inside a single
            # turn (a BLACK on his own post at 2026-08-26T14:32:27).
            #
            # Three conditions, and the third is the one that matters:
            #   - a tool that writes text, not a file upload, where WHICH
            #     file is exactly what an injected instruction would pick
            #   - he pre-approved Saved Messages in config
            #   - HIS words named Saved Messages. Saved Messages is visible
            #     only to him, which is exactly what makes it worth abusing:
            #     text an email writes into his own notebook reads as if HE
            #     wrote it - a phishing link, a fake note to himself. So the
            #     destination has to have come from him, not from what Jalen
            #     read. An email that ASKS for Saved Messages is refused.
            #
            # Never the channel, which his community reads. And it sits
            # INSIDE this block rather than beside pre-approval, so
            # tests/test_adversarial.py's ordering invariant holds.
            if (tool in self._SAFE_TO_OWN_CHAT_UNDER_TAINT
                    and self._self_chat_preapproved
                    and _simplify(named_by_him) in SELF_CHAT_ALIASES
                    and _is_self_chat(args.get(self._DESTINATION_ARG.get(tool, ""), ""))):
                detail["own_saved_messages_under_taint"] = True
                return Verdict(
                    Tier.GREEN, tool, summary,
                    "your own Saved Messages - nothing I read can send it "
                    "anywhere else",
                    False, False, False, detail,
                )
            return Verdict(
                Tier.BLACK, tool, summary,
                "this came from something I read, not from you — I don't act on "
                "instructions found in content",
                False, False, True, detail,
            )

        # 3. Destinations he has pre-approved.
        #
        # He asked for this directly: Jalen should send to his Machine
        # Learning community and to his Saved Messages "without asking me,
        # without taking my permission". Both are his own — one is his
        # channel, one is his notebook — and a spoken confirmation before
        # every one of fifty posts is friction with no safety value.
        #
        # Deliberately NOT a blanket downgrade of send_telegram_message.
        # Everything else it can reach is another human being, and that is
        # what the RED tier exists for.
        #
        # And it sits AFTER the injection check on purpose. An email or a
        # message that says "post this to your channel" is still refused —
        # pre-approving a destination approves HIM sending there, not
        # anything he happened to read asking on his behalf. That ordering
        # is the whole safety property; moving this block above step 2 would
        # quietly turn his channel into an open relay for anyone who can get
        # text in front of Jalen.
        if base is Tier.RED and origin == "user" and self._is_preapproved(tool, args):
            detail["preapproved_destination"] = True
            return Verdict(
                Tier.GREEN, tool, summary,
                "a destination you pre-approved in config",
                False, False, False, detail,
            )

        # 4. Posture adjustments.
        tier = base
        if self.posture == "paranoid" or self.paranoid:
            if tier is Tier.AMBER:
                tier = Tier.RED
        elif self.posture == "autonomous":
            if tier is Tier.AMBER:
                tier = Tier.GREEN

        return Verdict(
            tier=tier,
            tool=tool,
            summary=summary,
            reason=self._reasons.get(tier, ""),
            requires_confirmation=tier is Tier.RED,
            announce=tier is Tier.AMBER,
            blocked=False,
            detail=detail,
        )

    # --------------------------------------------------- pre-approved sends
    # tool -> which argument carries the destination.
    #
    # send_posts and send_telegram_file were missing, so "send those fifty
    # posts to my channel" and a file to his own Saved Messages asked every
    # time - while the send_telegram_file spec told the model the gate
    # "asks first unless the destination is one he pre-approved". Safe to
    # add only now that matching is exact: under the old substring rule
    # this would have spread "Ed is pre-approved" to batches of fifty.
    _DESTINATION_ARG = {
        "send_telegram_message": "to",
        "save_telegram_draft": "to",
        "send_posts": "to",
        "send_telegram_file": "to",
    }

    # What may still go to his OWN Saved Messages after Jalen has read
    # somebody else's text. See step 2 of classify().
    _SAFE_TO_OWN_CHAT_UNDER_TAINT = frozenset({
        "send_telegram_message", "save_telegram_draft", "send_posts",
    })

    @property
    def _self_chat_preapproved(self) -> bool:
        return any(name in SELF_CHAT_ALIASES for name in self._preapproved)

    def _is_preapproved(self, tool: str, args: dict[str, Any]) -> bool:
        """
        True when this send is going somewhere he has already said yes to.

        EXACT, NOT "CONTAINS". This used to accept a substring in either
        direction - `target in allowed or allowed in target` - and an
        independent audit reproduced what that meant with the real config:
        "Ed", "Ai", "Mac", "Sa", "Mes", "Eng", "Sage" and even "a" went out
        with no confirmation, as did any group whose title merely CONTAINED
        a pre-approved name ("Saved Messages backup group"). Then the
        resolver picked the chat by its own rule, so the gate approved a
        WORD and the resolver sent to an ENTITY.

        So it now approves exactly what the resolver will send to:

          his own Saved Messages   the spellings _resolve() maps to get_me()
                                   (SELF_CHAT_ALIASES), and no others
          any other configured     its exact title, with case and punctuation
          destination              normalised - "AI engineering & Machine
                                   learning" and "ai engineering and machine
                                   learning" are one name

        A casual spoken name - "my ML community" - is not approved here,
        because the resolver cannot find a chat by that name either; the
        brain is told the channel's real title (system prompt, TELEGRAM
        SHORTHAND). Anything that does not match falls through to RED and is
        asked about, the right default for anything that reaches a person.
        """
        arg = self._DESTINATION_ARG.get(tool)
        if arg is None:
            return False
        raw = args.get(arg, "")
        target = _simplify(str(raw))
        if not target:
            return False
        if _is_self_chat(raw) and self._self_chat_preapproved:
            return True
        return any(allowed and target == allowed and allowed not in SELF_CHAT_ALIASES
                   for allowed in self._preapproved)

    # ------------------------------------------------------------- formatting
    # Argument names whose VALUE must never reach the audit log.
    #
    # "passphrase" was missing, and that was not a small gap: unlock_vault's
    # only argument is called `passphrase`, so every unlock wrote the vault's
    # master passphrase into data/audit.jsonl and audit.db in plain text —
    # the one secret the whole vault exists to protect, written to disk
    # beside it, in a file that is deliberately readable in Notepad.
    #
    # Matched as substrings, so "passphrase", "vault_passphrase" and
    # "passphrase_confirm" are all covered. Deliberately NOT a bare "pass":
    # it would redact "passenger", "passage" and "password_hint" alike, and a
    # redaction list that fires on innocent arguments trains you to ignore it.
    _SENSITIVE_ARG_MARKERS = (
        "password", "passphrase", "passcode", "token", "secret", "key", "api",
        "credential", "pin", "seed", "mnemonic", "otp", "2fa",
    )

    @staticmethod
    def _redact(args: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in (args or {}).items():
            if any(s in k.lower() for s in SafetyEngine._SENSITIVE_ARG_MARKERS):
                out[k] = "***redacted***"
            elif isinstance(v, str) and len(v) > 400:
                out[k] = v[:400] + "…"
            else:
                out[k] = v
        return out

    @staticmethod
    def _describe(tool: str, args: dict[str, Any]) -> str:
        pretty = tool.replace("_", " ")
        for key in ("to", "recipient", "path", "file_path", "url", "query", "command", "name"):
            if key in args and isinstance(args[key], str):
                value = args[key]
                if len(value) > 90:
                    value = value[:90] + "…"
                return f"{pretty}: {value}"
        return pretty


def confirmation_question(verdict: Verdict, template: str = "{summary}. Confirm?") -> str:
    return template.format(summary=verdict.summary)
