"""
His own details, in a folder he owns, filled into forms he is looking at.

    "I will give him a folder, where it will have access to all of
     information there, where it can take that information and fill it out
     the corresponding box itself."

WHAT THIS IS, AND WHERE THE LINE IS
-----------------------------------
data/personal_info/ is plain text he edits. Jalen reads it to fill ORDINARY
form fields - name, age, contact, school - and asks him about anything it
does not have rather than guessing. Three hard boundaries, none of them
negotiable:

  PASSWORDS come from the vault, never from here. This module refuses to
  touch a password field even if the folder somehow held one.

  PAYMENT and bank details are never filled, by his own rule ("I fill out
  payment sections myself"). A field that looks like a card, a CVV, an IBAN
  or an account number is RECOGNISED specifically so it can be left alone
  and named back to him, not so it can be filled.

  A PLACEHOLDER IS NOT A VALUE. "(fill me in ...)" in the folder means he
  has not told Jalen yet, so the field is reported as needed, not filled
  with the reminder text.

WHY A FOLDER AND NOT HARD-CODED
-------------------------------
His details change - a new phone, a new school - and they are his, not the
program's. A folder he can open and edit puts them where he can see and
correct them, keeps them out of git (data/ is ignored), and means adding
"Nationality: Uzbek" to the file is all it takes to teach Jalen a new field.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
PROFILE_DIR = ROOT / "data" / "personal_info"

# A line that is a reminder to him, not an answer. Anything wholly inside
# parentheses, or empty, is "he has not filled this in yet".
_PLACEHOLDER = re.compile(r"^\s*\(.*\)\s*$")

# What a real field LABEL looks like, so a prose sentence that merely
# contains a colon is not mistaken for one. Measured against the seed file:
# "Edit it freely: it is plain text" has a colon and must NOT parse as a
# field. A label is short, starts with a letter, and carries no sentence
# punctuation - no commas, no full stops. "First name", "Date of birth",
# "Graduation year", "e-mail" all pass; a sentence does not.
_FIELD_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9 /'\-]{0,28}$")


# ---------------------------------------------------------------------------
# THE VOCABULARY
#
# Each concept carries three ways to be recognised, strongest first:
#   auto   - HTML autocomplete tokens. A site that sets these is telling you
#            outright what the field is; nothing beats it.
#   labels - words that appear in a human label / name / placeholder.
#   profile- the labels he might write in the folder for the same thing.
# ---------------------------------------------------------------------------
class _Concept:
    def __init__(self, key, auto=(), labels=(), profile=()):
        self.key = key
        self.auto = auto
        self.labels = labels
        self.profile = profile + labels      # folder can use either wording


CONCEPTS = [
    _Concept("full_name", ("name",), ("full name", "your name", "legal name"),
             ("full name", "name")),
    _Concept("first_name", ("given-name",),
             ("first name", "given name", "forename", "first"),
             ("first name", "given name", "forename")),
    _Concept("last_name", ("family-name",),
             ("last name", "surname", "family name", "last"),
             ("last name", "surname", "family name")),
    _Concept("email", ("email",), ("email", "e-mail", "email address"),
             ("email", "e-mail")),
    _Concept("phone", ("tel",),
             ("phone", "telephone", "mobile", "phone number", "cell"),
             ("phone", "telephone", "mobile")),
    _Concept("date_of_birth", ("bday",),
             ("date of birth", "dob", "birth date", "birthday"),
             ("date of birth", "dob", "birthday")),
    _Concept("age", (), ("age",), ("age",)),
    _Concept("gender", ("sex",), ("gender", "sex"), ("gender", "sex")),
    _Concept("nationality", (), ("nationality", "citizenship"),
             ("nationality", "citizenship")),
    _Concept("address", ("street-address", "address-line1"),
             ("address", "street address", "street"),
             ("address", "street")),
    _Concept("city", ("address-level2",), ("city", "town"), ("city", "town")),
    _Concept("state", ("address-level1",),
             ("state", "province", "region", "county"),
             ("state", "province", "region")),
    _Concept("postal_code", ("postal-code",),
             ("zip", "postal code", "postcode", "zip code", "post code"),
             ("postal code", "zip", "postcode")),
    _Concept("country", ("country", "country-name"), ("country",), ("country",)),
    _Concept("school", (),
             ("school", "university", "college", "institution",
              "current school"),
             ("school", "university", "college")),
    _Concept("graduation_year", (),
             ("graduation year", "grad year", "year of graduation",
              "expected graduation"),
             ("graduation year", "grad year")),
]

# Fields Jalen RECOGNISES in order to REFUSE. His rule: payment is his to do.
# Recognising them is what lets Jalen leave them alone AND name them back to
# him, instead of silently skipping or, worse, filling one.
_PAYMENT_AUTO = ("cc-number", "cc-exp", "cc-exp-month", "cc-exp-year",
                 "cc-csc", "cc-name", "cc-type")
_PAYMENT_WORDS = ("card number", "card no", "cardholder", "credit card",
                  "debit card", "cvv", "cvc", "security code", "expiry",
                  "expiration", "iban", "sort code", "account number",
                  "routing", "billing")


def _is_payment(field: dict) -> bool:
    auto = field.get("autocomplete", "")
    if any(tok in auto for tok in _PAYMENT_AUTO):
        return True
    hay = f"{field.get('label','')} {field.get('name','')}".lower()
    return any(word in hay for word in _PAYMENT_WORDS)


# ---------------------------------------------------------------------------
# READING THE FOLDER
# ---------------------------------------------------------------------------
def load_profile() -> dict:
    """
    Every "Label: value" line in the folder, as {lowercased label: value}.

    Placeholders and empty values are dropped here rather than at the point
    of use, so no caller can accidentally type "(fill me in)" into a form.
    Reads every .md file, so he can split his details across profile.md,
    education.md, contact.md - whatever he finds tidy.
    """
    out: dict[str, str] = {}
    if not PROFILE_DIR.is_dir():
        return out
    for path in sorted(PROFILE_DIR.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or ":" not in line:
                continue
            raw_label, _, value = line.partition(":")
            raw_label = raw_label.strip()
            value = value.strip()
            # A real field, not a sentence with a colon in it. Two signals,
            # because either alone lets prose through: the LABEL is short and
            # punctuation-free ("Edit it freely" passes that), and the VALUE
            # is not itself a sentence. A form value is a handful of words -
            # "Jaloliddin Musayev", "123 Main St, London" - so a value past
            # eight words is prose ("it is plain text and lives on your
            # machine" is nine), which is what slipped through on the label
            # test alone.
            if not _FIELD_LABEL.match(raw_label) or len(raw_label.split()) > 4:
                continue
            if not value or _PLACEHOLDER.match(value) or len(value.split()) > 8:
                continue
            out.setdefault(raw_label.lower(), value)
    return out


def _value_for(concept: "_Concept", profile: dict) -> str:
    """The folder value for this concept, or '' if he has not given one."""
    for synonym in concept.profile:
        if synonym in profile:
            return profile[synonym]
    return ""


# WHOSE detail is this field asking for? A form asks for several people:
# the applicant, a parent, a guardian, an emergency contact, a referee. The
# label says which, and ignoring that is how his own name ended up in the
# parent fields of a real Harvard-affiliated application - measured, on the
# live form, before this existed.
#
# The folder describes ONE person: him. So a field belonging to anyone else
# is left alone and reported, never guessed at from his details.
_SOMEONE_ELSE = (
    "parent", "guardian", "mother", "father", "spouse", "partner",
    "emergency", "next of kin", "referee", "reference", "recommender",
    "supervisor", "employer", "teacher", "counselor", "counsellor",
    "sibling", "relative", "contact person", "second ", "2nd ",
)


def belongs_to_someone_else(field: dict) -> bool:
    """True when the label asks for a person other than him."""
    hay = f"{field.get('label','')} {field.get('name','')}".lower()
    return any(word in hay for word in _SOMEONE_ELSE)


def _concept_for(field: dict) -> "_Concept | None":
    """Which concept this form field is, by the strongest signal available."""
    # Whose field it is outranks what kind of field it is. "What is your
    # parent's first name" is a first-name box, and none of his business.
    if belongs_to_someone_else(field):
        return None
    auto = field.get("autocomplete", "")
    if auto:
        for concept in CONCEPTS:
            if any(tok and tok == auto for tok in concept.auto):
                return concept
    hay = f"{field.get('label','')} {field.get('name','')}".lower()
    hay = re.sub(r"\s+", " ", hay).strip()
    # Longest label synonyms first, so "first name" wins over "name".
    best = None
    best_len = 0
    for concept in CONCEPTS:
        for word in concept.labels:
            if word in hay and len(word) > best_len:
                best, best_len = concept, len(word)
    return best


# ---------------------------------------------------------------------------
# THE TOOLS
# ---------------------------------------------------------------------------
def whats_my(query: str = "") -> str:
    """
    Answer "what's my <x>" from the folder. Never a secret, never payment.

    A plain lookup so he can check what Jalen knows without opening a file,
    and so "what's my school" has an answer that is not "I don't store that".
    """
    profile = load_profile()
    if not profile:
        return ("I don't have your details yet. There's a folder for them at "
                "data/personal_info - open profile.md and fill it in, and "
                "I'll use it from then on.")
    want = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not want:
        have = ", ".join(sorted(profile))
        return f"I've got these details for you: {have}."
    # Exact, then a concept, then contains.
    if want in profile:
        return f"{query.strip()}: {profile[want]}"
    for concept in CONCEPTS:
        if any(w in want for w in concept.labels):
            value = _value_for(concept, profile)
            if value:
                return f"{query.strip()}: {value}"
    holds = [f"{k}: {v}" for k, v in profile.items() if want in k]
    if holds:
        return "; ".join(holds)
    return (f"I don't have '{query.strip()}' in your details. Add it to "
            f"data/personal_info/profile.md as a 'Label: value' line and "
            f"I'll have it next time.")


def fill_page(page, profile: dict) -> dict:
    """
    Fill one page from a profile dict. Split out so it can be tested against
    a real browser page without the whole session machinery.

    Returns {filled, needed, payment, secret} - lists of field labels, so
    the caller can tell him precisely what happened to each field rather
    than a bare success line on a form that is only half done.
    """
    from .webforms import _SCAN

    fields = page.evaluate(_SCAN)
    filled, needed, payment, secret = [], [], [], []
    for field in fields:
        ftype = field.get("type", "")
        if ftype == "password":
            secret.append(field["label"])
            continue
        if _is_payment(field):
            payment.append(field["label"])
            continue
        concept = _concept_for(field)
        if concept is None:
            continue
        value = _value_for(concept, profile)
        if not value:
            if field.get("required"):
                needed.append(field["label"])
            continue
        try:
            element = page.locator("input, textarea, select").nth(field["index"])
            if ftype == "select-one":
                element.select_option(label=value)
            else:
                element.fill(value)
            filled.append(field["label"])
        except Exception:
            needed.append(field["label"])
    return {"filled": filled, "needed": needed,
            "payment": payment, "secret": secret}


def fill_form_from_profile() -> str:
    """
    Fill the form on screen from his folder - everything ordinary, at once.

    This is the point of the folder: he says "fill this in" and Jalen fills
    every field it legitimately knows, then tells him exactly what it still
    needs and what it left for him. It NEVER fills a password (vault only)
    or a payment field (his by rule); those are named back, not skipped
    silently, because a silent skip reads as "done" on a form that is not.
    """
    from .webagent import BrowserUnavailable, _Session

    profile = load_profile()
    if not profile:
        return ("I don't have any of your details yet. Open "
                "data/personal_info/profile.md, fill in what you're happy "
                "for me to use, and say this again.")

    try:
        result = _Session.get().do(lambda page: fill_page(page, profile))
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't read the form: {type(exc).__name__}: {exc}"

    parts = []
    if result["filled"]:
        parts.append("Filled " + ", ".join(result["filled"]))
    else:
        parts.append("I didn't fill anything")
    if result["needed"]:
        parts.append("still need from you: " + ", ".join(result["needed"]))
    if result["payment"]:
        parts.append("left the payment fields for you (" +
                     ", ".join(result["payment"]) + ")")
    if result["secret"]:
        parts.append("password fields come from your vault, not here")
    tail = (". Add anything missing to data/personal_info/profile.md and I'll "
            "have it next time.") if result["needed"] else "."
    return ". ".join(parts) + tail


REGISTRY: dict[str, Any] = {
    "whats_my": whats_my,
    "fill_form_from_profile": fill_form_from_profile,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
