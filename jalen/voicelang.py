"""
Which language a voice message is in, and so which voice reads it.

He is written to, and writes, in English, Uzbek and Russian, and Jalen has one
text-to-speech voice (en-US-AndrewNeural). Russian or Uzbek words read by an
English voice come out as noise, and the person who receives it is a person.
So send_voice_message picks the voice by language, and the question he is asked
before it goes out names the language when it is not English.

TWO SOURCES, and only these two:

  * the language the brain names (`language="uz"`, "Russian", ...), because
    Uzbek is written in Latin letters and nothing here can tell it from
    English by looking at the letters;
  * the script, when none is named: mostly Cyrillic is Russian, and Cyrillic
    with a letter only Uzbek uses (U+040E, U+045E, U+049A, U+049B, U+0492,
    U+0493, U+04B2, U+04B3) is Uzbek.

Anything else is English, which is what every voice message used to be.
Whether the Uzbek voice reads Uzbek written in Cyrillic is NOT MEASURED (no
synthesis was run, only edge_tts.list_voices()); if it cannot, the render
fails and nothing is sent, which is the safe way to be wrong.

Pure functions, no imports beyond the standard library: safety.py needs them to
word the question and messaging.py needs them to pick the voice, and neither
may import the other's side.
"""
from __future__ import annotations

import unicodedata

LANGUAGES = {"en": "English", "uz": "Uzbek", "ru": "Russian"}

_ALIASES = {
    "en": "en", "eng": "en", "english": "en", "en-us": "en", "en-gb": "en",
    "uz": "uz", "uzb": "uz", "uzbek": "uz", "uz-uz": "uz", "o'zbek": "uz",
    "ru": "ru", "rus": "ru", "russian": "ru", "ru-ru": "ru",
}

# The letters Cyrillic Uzbek has and Russian does not: ў қ ғ ҳ, both cases.
_UZBEK_CYRILLIC = frozenset("ЎўҚқҒғҲҳ")


def normalise(language) -> str:
    """"Uzbek", "UZ", "uz-UZ" -> "uz"; anything it does not know -> "" (not named)."""
    key = " ".join(str(language or "").lower().split())
    return _ALIASES.get(key, "")


def detect(text: str) -> str:
    """The language the SCRIPT says: "ru", "uz", or "en" (Latin letters and everything else)."""
    letters = [ch for ch in str(text or "") if ch.isalpha()]
    if not letters:
        return "en"
    cyrillic = [ch for ch in letters if unicodedata.name(ch, "").startswith("CYRILLIC")]
    if len(cyrillic) * 2 < len(letters):
        return "en"
    return "uz" if any(ch in _UZBEK_CYRILLIC for ch in cyrillic) else "ru"


def resolve(text: str, language="") -> str:
    """The language to speak `text` in: the one named, else the one its script shows."""
    return normalise(language) or detect(text)


def name(code: str) -> str:
    """"uz" -> "Uzbek". Said in the question and in the reply."""
    return LANGUAGES.get(code, "")
