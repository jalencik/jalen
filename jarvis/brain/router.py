"""
The local intent router — the single most important cost decision in this build.

Why it exists: Agent SDK usage draws on your Claude Pro allowance. A voice
assistant you talk to all day will send hundreds of turns. If "pause the music"
costs a Claude round-trip, you will hit your limit by Thursday and Jalen goes
silent — which is the one failure mode that makes people stop using an
assistant for good.

So: the ~40 things you say most often are matched here, in about 50 ms, for
zero tokens. Everything genuinely novel goes to Claude. Expect this to absorb
60-80% of daily turns.

`router.log_misses` writes everything that fell through to
data/router_misses.log. Read it weekly. When you see the same phrase three
times, add a rule. The router should get better every week without you touching
the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# ----------------------------------------------------------------------------
# What he calls it.
#
# One source of truth, used by BOTH the leading strip ("Jalen, open chrome")
# and the trailing one ("open chrome, Jalen"). Before this existed the name
# was written literally, in two places, as "jarvis" — so when the assistant
# was renamed, EVERY name-prefixed command stopped routing at once and went
# to the LLM instead: "jalen quit" matched nothing, cost a round-trip, and
# came back as conversation rather than an exit. Measured before the fix:
# "jarvis quit" routed in 50ms, "jalen quit" routed never.
#
# A LIST CANNOT COVER THIS, SO IT IS A SHAPE INSTEAD.
#
# He said it plainly: "I am not being able to speak jalen properly and it is
# not actually properly hearing that from me... many people speak it
# different." An enumerated list only ever covers the spellings someone
# thought of, and every one it misses is a command that silently goes to the
# LLM or nowhere.
#
# So the name is matched by its SHAPE. "Jalen" is j + a vowel + an l + a
# vowel + n, and every plausible transcription — Jalen, Jaylen, Jaelen,
# Jailen, Jalin, Jalon, Jaleen, Jaylan, Jhalen, Jaylene — is that shape with
# different vowels. One pattern covers all of them and the ones nobody has
# produced yet.
#
# Kept deliberately tight at the edges. It requires a leading J, so "Galen"
# and "Alan" cannot match: those are ordinary names, and matching them would
# strip a real person out of a sentence ("tell Alan I'm late" -> "tell I'm
# late"). It also bounds the vowel runs, so it cannot wander into unrelated
# words.
#
# THE STRESS PROBLEM, in his words: "many people give udareniya not to a in
# jalen, but they give it to e". English speakers say JA-len; he and most
# Russian and Uzbek speakers say ja-LEN. Under second-syllable stress the
# FIRST vowel reduces to a schwa, and Whisper writes a reduced vowel as
# almost anything — jalen, jelen, julen, jilen, jolen — while the stressed
# second vowel gets written long: jaleen, jalene, jaline, jaleyn.
#
# The opening consonant moves too. Ж and Дж transliterate as zh/dzh/dz, and
# an aspirated J is heard as jh. All of those are covered below and every one
# is exercised in tests/test_name_pronunciations.py.
#
# `(?:zh|dzh|dz|jh|j)` rather than a bare j, `[aeiouy]{1,3}` for the reduced
# first vowel, `[aeiouy]{0,3}` for the lengthened second, and a final
# consonant that may be heard as n, m or ng — "Jalem" and "Jaleng" are what
# a nasal at the end of an unstressed syllable becomes.
#
# The `h?` after the first vowel is for "Jahlen" — an aspirated first
# syllable, which is how the reduced vowel is often written out.
_JALEN = r"(?:dzh|dz|zh|jh|j)[aeiouy]{1,3}h?l{1,2}[aeiouy]{0,3}(?:n{1,2}g?|m)(?:e|'s|s)?"

# JARVIS WORKS EVERYWHERE, at his explicit request: "alongside it jarvis
# should work as well, Jarvis should work everywhere as well."
#
# This was briefly removed on an earlier instruction to never hear the old
# name, and the audit log for 21 Aug shows the cost inside the hour —
# "Hey Jarvis, quit." matched nothing, went to the LLM, and did not quit.
# Hearing a word and saying it are different things: the old name is gone
# from everything Jalen SAYS and stays understood in everything he HEARS.
# "Jaros" and "Jarvers" are in scope because Whisper produced them, from
# him, in that same session.
_JARVIS = (
    r"(?:dzh|dz|zh|jh|j)[aeiou]{1,2}r+[vw]?[aeiouy]{0,2}r?[sz]e?(?:'s)?"
    r"|(?:dzh|dz|zh|jh|j)[aeiou]{1,2}r+[oae]s"
    # "jarvice" — the -vis ending heard as -vice. Seen from Whisper, and the
    # [sz] branch above cannot reach it because of the intervening c.
    r"|(?:dzh|dz|zh|jh|j)[aeiou]{1,2}r+v[aeiouy]{0,2}ce"
)

# Real names the shape would otherwise swallow. A deny-list is the honest
# tool here: "Julian" and "Jolene" genuinely ARE j-vowel-l-vowel-n, so no
# amount of tightening separates them from "Jalen" without also losing real
# transcriptions of his name. Naming the two exceptions costs nothing and
# keeps a message addressed to Julian going to Julian.
_NOT_THE_NAME = (
    "julian", "juliana", "julianne", "jolene", "jalapeno", "javelin",
)
# "jolen" is deliberately NOT excluded, though it is a Jolene variant. In
# this context — spoken at a voice assistant, on his machine — it is far
# likelier to be a transcription of his name than a reference to a song.

# The trailing word boundary is load-bearing: without it "julian" is only
# excluded when the utterance ENDS there, so "julian quit" would strip
# anyway. It was lost once already — a shell here-document turned the
# backslash-b into a literal 0x08 byte, which compiles fine and matches
# nothing, so the deny-list silently did nothing at all.
_NAME = (
    "(?!(?:" + "|".join(_NOT_THE_NAME) + r")\b)"
    "(?:" + _JALEN + "|" + _JARVIS + ")"
)

# The spellings above are also listed explicitly, purely so tests and future
# readers can see concrete examples of what the shapes are meant to cover.
# Nothing matches against this list — the patterns do the work.
NAME_ALIASES = (
    "jalen", "jaylen", "jaylin", "jalin", "jaelen", "jailen",
    "jalon", "jaleen", "jalene", "jayleen", "jaylan", "jhalen",
    "jarvis", "jaros", "jarvers", "jervis", "jarvus",
)

# Was this sentence actually addressed to Jalen?
#
# "It should not respond to its own voice, or any other noise that is
# happening or disrupting the flow... it should only respond to messages
# starting with either Jalen or Hey Jalen."
#
# The microphone cannot tell his voice from the television from Jalen's own
# speech coming back through the speakers, and there is no acoustic echo
# cancellation here. Voice activity detection answers "is that speech",
# which is the wrong question — the television is also speech.
#
# So the gate moved from the ACOUSTICS to the WORDS. After transcription we
# already have the sentence; a sentence that does not begin with his name is
# not for us. That is cheap, needs no model, and cannot be fooled by volume.
#
# Deliberately checked on the RAW transcript, before the politeness stripper
# runs — that function exists to remove the name, so asking it afterwards
# would always say no.
_ADDRESSED = None


def addressed_to_jalen(text: str) -> bool:
    """
    True if the sentence opens with his name, in any pronunciation.

    "Jalen, what's the time" / "Hey Jalen open Chrome" / plain "Jalen?" all
    count. "what's the time" does not, however clearly it was spoken.
    """
    global _ADDRESSED
    if _ADDRESSED is None:
        _ADDRESSED = re.compile(
            r"^\s*(?:hey |hi |yo |ok |okay )?" + _NAME + r"(?:[.,!?;:\s]|$)",
            re.I,
        )
    return bool(_ADDRESSED.match(text or ""))


# What may follow the name and still count as "he was addressing me".
#
# This was [,\s]+ — comma or whitespace. Speech recognition punctuates with
# FULL STOPS, so "Jalen. Hey Jalen. Quit." (his words, 15:54:40) stripped
# nothing, matched nothing, went to the LLM, and did not quit. A sentence
# boundary after someone's name is the most natural thing in the world and
# it made every name-prefixed command fail.
_AFTER_NAME = r"[.,!?;:\s]+"

# Commands that END in a word the dangling-preposition guard watches for, but
# which are complete as they stand — the trailing word is a phrasal-verb
# particle, not a preposition that lost its object. Kept as an explicit,
# short list rather than a cleverer rule: telling a particle from a
# preposition properly needs a parser, and these are the only ones the router
# actually routes.
_COMPLETE_PHRASAL_COMMANDS = frozenset({
    "hold on",      # pause
    "carry on",     # resume
    "go on",        # resume
    "move on",
})

# ----------------------------------------------------------------------------
# "CLAUDE CODE", AS SPEECH RECOGNITION ACTUALLY WRITES IT.
#
# From the audit log, 21 Aug, four wasted minutes in a row: he asked to hand
# a task to Claude Code and Whisper transcribed it as "CloudCork" every
# time. Jalen searched his Desktop for an app by that name, found nothing,
# said so, and logged a weakness — all correct behaviour for a word it had
# no reason to recognise, and all useless to him.
#
#   16:06:08  "hand this task off to CloudCork"
#   16:07:56  "open cloud core and paste a prompt"
#   16:10:45  "That's the cloud. Could you please open that app"
#
# "Claude" is a French name that English speech recognition does not expect,
# so it lands on "cloud" almost every time, and "code" becomes "cork",
# "core" or "cord". These are not typos he can avoid by speaking more
# clearly — they are what the transcriber produces.
_CLAUDE_CODE = (
    r"(?:cl(?:aude|oud)\s*(?:code|cork|core|cord|coat|kode)"
    r"|claude code|claude|cowork|co-?work)"
)


# ----------------------------------------------------------------------------
# IS THIS ARGUMENT ACTUALLY A NAME?
#
# The single worst failure this router has produced, and he described it
# exactly: "it is rewriting my thing that I told him rather than executing
# it." From the audit log, 21 Aug 11:41 —
#
#   he said:  "I would like you to go to my Gmail and find one random email
#              about my machine learning community. I would like you to make
#              an extensive research about the project..."   (48 words)
#   router:   focus_window(name="my Gmail and find one random email about my
#              machine learning")
#   Jalen:    "I can't find a window called my Gmail and find one random
#              email about my machine learning community. I would like you
#              to make an extensive research about..."
#
# It read his own instruction back to him. Several catch-all rules end in a
# greedy "(.+)$" — "go to X", "open X", "switch to X" — and a greedy capture
# will happily swallow an entire paragraph and hand it over as an app name.
# The request never reached the brain, which would have handled it, because
# the router answered first and answered wrongly.
#
# A name is short and has no clauses in it. Anything else is an instruction
# that merely STARTS with a verb the router knows, and belongs to the brain.
# Failing this check makes the rule decline, so matching continues down the
# table and ultimately falls through — slower, and right, instead of instant
# and wrong.
# ----------------------------------------------------------------------------
MAX_NAME_WORDS = 6
MAX_NAME_CHARS = 60

# Connectors that turn one phrase into two clauses. "chrome and find the
# paper I was reading" is not a window; "rock and roll" would be a legitimate
# name, so this is checked with spaces around it and only alongside the
# length limits, never on its own.
_CLAUSE_JOINERS = re.compile(
    r"\b(?:and then|and also|and|then|after that|so that|because|"
    r"could you|would you|i want|i need|i'd like|i would like|please)\b",
    re.I,
)


def _which_ai(m: "re.Match") -> str:
    """Whichever of the alternatives matched. 'chat gpt' -> 'chatgpt'."""
    for group in m.groups():
        if group:
            return group.lower().replace(" ", "")
    return ""


def looks_like_a_name(value: str) -> bool:
    """True when this could plausibly be an app, window, file or folder name."""
    text = (value or "").strip()
    if not text or len(text) > MAX_NAME_CHARS:
        return False
    if len(text.split()) > MAX_NAME_WORDS:
        return False
    # A full stop mid-string means he said more than one sentence.
    if re.search(r"[.!?]\s+\S", text):
        return False
    return not _CLAUSE_JOINERS.search(text)


# Argument names that must pass looks_like_a_name(). Keyed on the ARGUMENT,
# not the tool, because the property belongs to the argument: anything a rule
# calls "name" or "app" is a name by definition. Free-text arguments —
# "query", "text", "message" — are deliberately absent; a long search query
# or a long message is perfectly normal.
_NAME_ARGS = frozenset({"name", "app"})


def _web_search_url(query: str) -> str:
    """
    Build a Google search URL for a spoken query.

    "search the web for X" / "google X" need somewhere real to land: open_url
    is an actual tool (jarvis/tools/system.py), and a search-engine URL is
    what fulfils "look this up" without a search API key or new dependency.
    """
    return "https://www.google.com/search?q=" + quote_plus(query)


# ----------------------------------------------------------------------------
# Known websites: "open youtube" / "go to chess.com" / "open instagram".
#
# Resolved the same way launcher.py resolves apps -- a deterministic name
# table, checked BEFORE the generic open_target/focus_window catch-alls
# further down can swallow the phrase. Without this, "open youtube" fell
# through to open_target, which resolves apps/files/folders -- there is no
# app, file or folder named "youtube" on the machine, so it either reported
# "I couldn't find" or, worse, fuzzy-matched something unrelated. A known
# site name has exactly one sane meaning: open the website.
#
# Names are the ones the user actually says. Bare name and ".com" form both
# map to the same URL so "chess" and "chess.com" behave identically.
# ----------------------------------------------------------------------------
_SITE_TABLE: dict[str, str] = {
    "youtube": "https://youtube.com",
    "youtube.com": "https://youtube.com",
    "chess": "https://chess.com",
    "chess.com": "https://chess.com",
    "instagram": "https://instagram.com",
    "instagram.com": "https://instagram.com",
    "gmail": "https://mail.google.com",
    "gmail.com": "https://mail.google.com",
    "email": "https://mail.google.com",
    "github": "https://github.com",
    "github.com": "https://github.com",
    "claude": "https://claude.ai",
    "claude.ai": "https://claude.ai",
    "chatgpt": "https://chatgpt.com",
    "chatgpt.com": "https://chatgpt.com",
    "telegram web": "https://web.telegram.org",
    "whatsapp web": "https://web.whatsapp.com",
    "linkedin": "https://linkedin.com",
    "linkedin.com": "https://linkedin.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "x.com": "https://x.com",
    "reddit": "https://reddit.com",
    "reddit.com": "https://reddit.com",
}

# Longest names first: "telegram web" must be tried as a whole before any
# shorter alternative could grab a prefix of it.
_SITE_NAMES_SORTED = sorted(_SITE_TABLE, key=len, reverse=True)
_SITE_ALTERNATION = "|".join(re.escape(s) for s in _SITE_NAMES_SORTED)

# Fallback for a domain the table doesn't know by name: "go to
# some-startup.io" is unambiguous even without a table entry -- it's a
# domain, so open it directly rather than sending it to app/file search.
_DOMAIN_RE = r"[a-z0-9][a-z0-9-]*\.(?:com|org|net|io|co|dev|gg|app|ai)"


def _site_lookup(m: re.Match) -> dict:
    key = m.group(1).strip().lower()
    return {"url": _SITE_TABLE.get(key, key)}


def _default_to_desktop(name: str) -> str:
    """
    "make me a new folder called Projects" names a THING, not a location —
    nobody means "in whatever directory Jalen happens to be running from."
    A bare name defaults to the Desktop, same as double-clicking "New
    Folder" there; anything that already looks like a real path (a drive
    letter, a leading slash, or an explicit ~) is left exactly as spoken.
    """
    name = name.strip().strip('"').strip("'")
    if re.match(r"^([a-zA-Z]:[\\/]|[\\/]|~)", name):
        return name
    return "~/Desktop/" + name


def _resolve_doc(raw: str) -> str:
    """
    "read changes.pdf" / "what's in my CV" name a document the way a person
    would — a bare filename or a nickname, not a full path. read_document
    itself only resolves a literal path (same contract as read_file), so
    without this it would look for "changes.pdf" relative to wherever
    Jalen's process happens to be running and almost always fail. Reuse
    launcher.find_files — the exact search open_target already relies on —
    to turn that spoken name into a real path first; fall back to the raw
    name (letting read_document report "no such file" honestly) when
    nothing matches rather than guessing.
    """
    from ..tools.launcher import find_files

    hits = find_files(raw)
    return hits[0] if hits else raw


# Document targets safe to route locally without risking a generic question
# ("what's in the news?") being misread as a file lookup: either a handful
# of common document nouns, or anything ending in a real document extension.
_DOC_TARGET = (
    r"(?:cv|resume|cover letter|transcript|report|notes)"
    r"|(?:[\w][\w .,'()-]*\.(?:pdf|docx?|txt|pptx?|xlsx?|csv|md|rtf))"
)


@dataclass
class Intent:
    tool: str
    args: dict[str, Any]
    reply: str | None = None      # spoken immediately, no LLM involved
    confidence: float = 1.0


# Each rule: (compiled regex, tool name, arg builder, spoken reply template)
Rule = tuple[re.Pattern, str, Callable[[re.Match], dict], str | None]


def _rules() -> list[Rule]:
    R = re.compile
    n = lambda m: {}  # noqa: E731

    return [
        # ====================================================================
        # ORDERING RULE: rules are matched top-to-bottom, first match wins, so
        # SPECIFIC phrases must precede CATCH-ALLS. Several rules below end in
        # a greedy "(.+)$" — "open X", "switch to X", "find X" — and each one
        # silently swallows anything more specific placed after it. Three real
        # bugs came from exactly this: "open the folder X" was eaten by
        # open_app, and "go to sleep" was eaten by focus_window (as a window
        # literally named "sleep"). Jalen's own commands go FIRST, because a
        # command aimed at Jalen must never be mistaken for one aimed at an
        # app that happens to share a word.
        # ====================================================================

        # ---- talking to Jalen itself ---------------------------------------
        # The trailing "( <name>| yourself)?" on several of these is belt AND
        # braces: _normalise() already strips a trailing address, so "quit
        # jalen" arrives here as plain "quit". It stays because the two
        # mechanisms guard different things — the normaliser handles the name
        # said as an ADDRESS ("quit, Jalen"), this handles it said as the
        # OBJECT ("quit Jalen") — and because a self-command failing to match
        # is the one failure the user cannot route around: if "quit" doesn't
        # work, the only remaining exit is killing the process by hand.
        (R(r"^(mute|be quiet|shut up|silence)( yourself)?$", re.I),
         "jalen_mute", n, "Muted."),
        # "talk" is here because "you can talk" NEVER REACHED THIS RULE. The
        # politeness stripper above removes a leading "you can", so the rule's
        # own "you can talk" alternative was dead on arrival — normalisation
        # had already turned it into "talk", which matched nothing. The
        # alternative is kept anyway (it costs nothing and documents the
        # intent) but "talk" is what actually fires.
        #
        # Found by the new-user sweep, not by anyone using it: a rule that
        # cannot match is invisible until someone enumerates the phrases.
        (R(r"^(unmute|speak|talk|you can talk|you can speak)( now)?$", re.I),
         "jalen_unmute", n, "Back."),
        # "not now" is deliberately absent. _normalise() strips a trailing
        # "now" as filler — it is there so "open chrome now" reaches open_app
        # — which turns "not now" into a bare "not". Matching "not" on its own
        # would be worse than missing the phrase.
        (R(r"^(go to sleep|sleep|stand by|stop listening|go away|leave me alone"
           r"|later|maybe later)$", re.I),
         "jalen_sleep", n, "Sleeping. Say hey Jalen to wake me."),
        # QUIT MUST MATCH EVERY WAY A PERSON ENDS A CONVERSATION.
        #
        # Enumerated rather than guessed, because a quit word that does not
        # match is the one failure with no workaround left: if "quit" fails,
        # the remaining exit is killing the process by hand. "goodbye" and
        # "see you" were missing until someone said them out loud.
        #
        # These are safe to be generous with precisely BECAUSE of the address
        # gate — a bare "bye" now only reaches here if he said the name or
        # the wake word, so it can no longer be triggered by a film.
        (R(r"^(quit|exit|shut ?down|close|kill|turn off|log ?off|log ?out"
           r"|good ?bye|bye|bye bye|see you|see ya|goodnight|good night"
           r"|(?:that'?s|that is) all|that will be all"
           r"|(?:we'?re|we are) done|(?:i'?m|i am) (?:done|finished))"
           r"( " + _NAME + r"| yourself| for now| for today)?$", re.I),
         "jalen_quit", n, "See you, Boss."),
        (R(r"^turn yourself off$|^switch yourself off$|^power (yourself )?down$",
           re.I),
         "jalen_quit", n, "See you, Boss."),
        (R(r"^(pause|hold on|take a break)( " + _NAME + r")?$", re.I),
         "jalen_pause", n, "Paused. Say hey Jalen when you want me back."),
        (R(r"^(resume|carry on|start listening|i'?m back|wake up)( " + _NAME + r")?$", re.I),
         "jalen_resume", n, "Listening again."),
        (R(r"^(restart|reboot|reload)( " + _NAME + r"| yourself)?$", re.I),
         "jalen_restart", n, "Restarting."),
        # A bare name with nothing after it. STT produces this constantly:
        # the wake word fires, he starts to speak, and the endpointer closes
        # on just "Jalen". Without a rule it is a full LLM round-trip to
        # answer a summons — the single cheapest turn there is, priced like
        # the most expensive one.
        (R(r"^(?:hey |ok |okay )?" + _NAME + r"$", re.I),
         "jalen_ack", n, "Yes, Boss?"),

        # ---- the technician ------------------------------------------------
        # Symptom-first, because that is how he says it: he does not ask for
        # a diagnostic, he says the wifi keeps cutting out. Each of these
        # routes straight to the read-only check for that area, so asking
        # what is wrong costs nothing and he keeps asking.
        (R(r"^(?:why (?:does|is) )?(?:my )?wi-?fi (?:keeps? )?"
           r"(?:dropping|disconnect(?:s|ing)?|cutting out|drop out|so bad|slow)\??$"
           r"|^(?:check|diagnose|what'?s wrong with) (?:my )?wi-?fi\??$", re.I),
         "diagnose_wifi", lambda m: {}, None),
        (R(r"^(?:diagnose|check|scan|what'?s wrong with) "
           r"(?:my |the |this )?(?:computer|machine|laptop|pc|system)\??$"
           r"|^(?:what'?s wrong|run a diagnostic|health check)\??$", re.I),
         "diagnose", lambda m: {}, None),
        (R(r"^(?:diagnose|check) (?:my |the )?(wifi|storage|updates?|security|performance)\??$", re.I),
         "diagnose", lambda m: {"area": m.group(1).rstrip("s")}, None),
        (R(r"^(?:stop|don'?t let) (?:my )?wi-?fi (?:from )?(?:sleeping|powering off|turning off)$"
           r"|^fix (?:my )?wi-?fi(?: power saving)?$", re.I),
         "fix_wifi_power_saving", lambda m: {"allow_power_off": False}, None),
        (R(r"^(?:let|allow) (?:my )?wi-?fi (?:to )?sleep(?: again)?$", re.I),
         "fix_wifi_power_saving", lambda m: {"allow_power_off": True}, None),
        (R(r"^(?:how (?:much|many)|check) temp(?:orary)? files\??$"
           r"|^temp files\??$", re.I),
         "temp_file_report", lambda m: {}, None),
        (R(r"^(?:clear|clean|delete|empty) (?:the |my )?temp(?:orary)? files\??$", re.I),
         "clear_temp_files", lambda m: {}, None),
        (R(r"^(?:open |show |check )?windows update(?: settings)?\??$", re.I),
         "open_windows_update", lambda m: {}, None),
        (R(r"^(?:open |show |check )?(?:windows )?(?:security|defender|antivirus)"
           r"(?: settings)?\??$", re.I),
         "open_windows_security", lambda m: {}, None),
        (R(r"^(?:open |show )?startup (?:apps|programs|settings)\??$", re.I),
         "open_startup_settings", lambda m: {}, None),

        # ---- browser windows -----------------------------------------------
        # "Close the YouTube window" used to match close_app, which takes the
        # FIRST window whose title contains the word — a coin toss on a
        # machine with six Chrome windows open, and the one it picks might be
        # an hour of research.
        # A BARE "window" IS AN OS WINDOW, NOT A BROWSER WINDOW.
        #
        # This rule used to match `windows?` unqualified, and it sits ABOVE
        # get_window_list — so "what windows are open", which is plainly a
        # question about the desktop, was answered with a list of Chrome
        # tabs. Found by tests/benchmark_phrasing.py, which is excluded from
        # the normal run and had been failing quietly.
        #
        # "windows" now only reaches here when something says it is a
        # browser: "browser windows", "chrome windows". Tabs and pages are
        # unambiguous and stay unqualified.
        (R(r"^(?:what|which) (?:tabs?|pages?) (?:are |do i have )?open\??$"
           r"|^(?:what|which) (?:browser|chrome|edge|firefox) "
           r"(?:tabs?|windows?|pages?) (?:are |do i have )?open\??$"
           r"|^(?:list|show) (?:my )?(?:tabs?|pages?)\??$"
           r"|^(?:list|show) (?:my )?(?:browser|chrome|edge|firefox) "
           r"(?:tabs?|windows?|pages?)\??$", re.I),
         "list_browser_tabs", lambda m: {}, None),
        # The excluded words are why this sits here rather than anywhere
        # convenient. "close the window" means Alt+F4 on whatever is focused
        # and "switch to the last window" means Alt+Tab — both already exist,
        # and without this guard the page-name capture swallowed them as
        # page="the" and page="last", so the two commands stopped working
        # entirely. A page name that is a pronoun is not a page name.
        (R(r"^close (?:the |my )?"
           r"(?!(?:the|this|that|current|active|last|previous|next|other)\b)"
           r"(.+?) (?:tab|window|page)$", re.I),
         "close_browser_tab", lambda m: {"page": m.group(1).strip()}, None),
        (R(r"^(?:switch|go) to (?:the |my )?"
           r"(?!(?:the|this|that|current|active|last|previous|next|other)\b)"
           r"(.+?) (?:tab|window|page)$", re.I),
         "focus_browser_tab", lambda m: {"page": m.group(1).strip()}, None),
        # "Read it all." The spoken-length cap is a default for answers he
        # did not ask to hear, not a ceiling on what he is allowed to hear.
        (R(r"^(?:read (?:it |that |the )?(?:all|whole thing|rest|everything)"
           r"|read (?:it|that) (?:out |aloud )?(?:in full|to the end)"
           r"|finish reading|keep reading|continue reading"
           r"|read the whole (?:thing|answer|post|email))$", re.I),
         "jalen_read_all", n, None),
        # Resizing the orb by voice, because the mouse cannot reach it. It is
        # click-through while idle — deliberately, since a 420-pixel circle in
        # the middle of the screen that ate clicks would be intolerable — and
        # a click-through window receives no scroll events, so the mouse
        # wheel only works while the orb is idle. Voice and Ctrl+Alt+B /
        # Ctrl+Alt+S work in every state.
        # "orb" is required, not optional. A bare "smaller" would match this
        # and hijack a follow-up meant for something else — and the follow-up
        # window is open for twelve seconds after every reply.
        # "orb" or "yourself" is REQUIRED, never a bare "bigger". The
        # follow-up window is open for twelve seconds after every reply, and
        # a bare "smaller" would hijack an answer meant for something else.
        (R(r"^(?:make |set )?(?:the |your ?)?(?:orb|yourself|you)\s+"
           r"(bigger|larger|smaller|tinier|huge|big|small|tiny|normal|medium)"
           r"(?: size)?$"
           r"|^(?:make (?:the |your )?orb (bigger|larger|smaller|tinier)"
           r"|(bigger|larger|smaller|tinier) orb"
           r"|orb (bigger|larger|smaller|tinier))$"
           r"|^(?:orb|yourself) (?:back to )?normal(?: size)?$"
           r"|^normal (?:orb )?size$", re.I),
         "jalen_orb_size",
         lambda m: {"change": next((g for g in m.groups() if g), "normal")},
         None),
        # Moving it. Named corners only — "top left" means the same thing on
        # a laptop panel and a 4K monitor, which pixel coordinates do not.
        (R(r"^(?:move|put|place|send) (?:the |your ?)?(?:orb|yourself|you) "
           r"(?:to |in |into |at )?(?:the )?"
           r"(top ?left|top ?right|bottom ?left|bottom ?right|upper ?left"
           r"|upper ?right|lower ?left|lower ?right|middle|cent[er]{2})"
           r"(?: corner)?$"
           r"|^orb (?:to (?:the )?)?(top ?left|top ?right|bottom ?left"
           r"|bottom ?right|middle|cent[er]{2})$", re.I),
         "jalen_orb_move",
         lambda m: {"position": next((g for g in m.groups() if g), "")},
         None),
        # Jalen checking Jalen. Routed locally because the answer is a
        # subprocess and a summary, not a conversation - and because he asks
        # it precisely when something feels wrong, which is the worst moment
        # for it to need the network.
        (R(r"^(?:run|do) (?:your|the) (?:own )?tests?$"
           r"|^test yourself$"
           r"|^(?:are|is) (?:your|the) tests? passing\??$"
           r"|^check yourself$", re.I),
         "run_own_tests", lambda m: {}, None),
        (R(r"^(?:are you (?:ok|okay|alright|healthy|well))\??$"
           r"|^how are you (?:doing|feeling)\??$"
           r"|^(?:your|self) health$", re.I),
         "own_health", n, None),
        (R(r"^(?:run |do )?(?:your |a )?(?:self.?)?diagnos(?:e|tics?)(?: yourself)?$"
           r"|^diagnose yourself$"
           r"|^what.?s wrong with you\??$", re.I),
         "self_diagnose", n, None),
        (R(r"^open (?:your|the) (?:own )?(?:code|source|project)(?: in vs ?code)?$"
           r"|^open yourself in vs ?code$", re.I),
         "open_own_project", n, None),
        # Background coding jobs. Only the STATUS is routed locally: it is a
        # short factual list and he will ask it while waiting, possibly with
        # the network down.
        #
        # "review the coding job" is deliberately NOT here. That tool returns
        # the agent's full output plus a git diff and ends by asking for a
        # judgement - routed locally, all of it would simply be read aloud.
        # Judging it is the brain's job, and the brain has the tool.
        (R(r"^(?:list|show|what) (?:are )?(?:the |my )?coding jobs?\??$"
           r"|^(?:is|are) (?:the |my )?(?:agent|coding job)s? (?:done|finished)\??$"
           r"|^what(?:'s| is) claude (?:code )?(?:doing|working on)\??$", re.I),
         "list_coding_jobs", lambda m: {}, None),
        # Signing in to the web chats. Routed locally because it is a state
        # question with a short factual answer, and because he asks it
        # exactly when a delegation has just failed - which is the worst
        # moment for it to need the network.
        #
        # The DELEGATION itself is deliberately NOT routed here. It needs a
        # structured work order - objective, criteria, constraints - and a
        # regex cannot build one. The brain has the tool and the context.
        (R(r"^sign (?:me )?(?:in|into) (?:to )?(chat ?gpt|gemini)$"
           r"|^(?:am i |are you )?signed in(?: to)? (chat ?gpt|gemini)\??$"
           r"|^log (?:me )?in(?:to)? (?:to )?(chat ?gpt|gemini)$", re.I),
         "web_sign_in_state",
         lambda m: {"agent": _which_ai(m)}, None),
        (R(r"^(?:sign|register) me up (?:to |for )?(chat ?gpt|gemini)$"
           r"|^create (?:me )?(?:an? )?(chat ?gpt|gemini) account$", re.I),
         "open_signup", lambda m: {"agent": _which_ai(m)}, None),
        (R(r"^close (?:the )?browser$|^shut (?:the )?browser$", re.I),
         "close_browser", n, None),
        (R(r"^(?:list|show|what) (?:are )?(?:the |my )?web (?:chats?|delegations?)\??$"
           r"|^what have you asked (?:chat ?gpt|gemini)\??$", re.I),
         "list_web_chats", lambda m: {}, None),
        # "Why are you so slow" was unanswerable for months because nothing
        # measured a turn. Now it is a question with a number for an answer.
        (R(r"^(?:how (?:fast|quick|slow) (?:was that|were you|are you)|"
           r"(?:what'?s your |show (?:me )?(?:your )?)?(?:timing|latency)"
           r"(?: report| stats)?|why (?:are you|were you) so slow)\??$", re.I),
         "jalen_timing", n, None),
        # The self-improvement loop he asked for, reachable without spending
        # a Claude turn to read a local file.
        (R(r"^(?:what (?:can'?t|cannot) you do(?: yet)?|"
           r"what are your (?:gaps|weaknesses|limits)|"
           r"(?:show|read) (?:me )?(?:your )?weaknesses|"
           r"what should i fix)\??$", re.I),
         "review_weaknesses", lambda m: {"limit": 5}, None),

        # ---- media (spec E42) ----------------------------------------------
        # Named media APPS resolve to "launch it", before the generic media-key
        # rules below can swallow them. "play spotify" with Spotify closed used
        # to tap the play/pause key into the void and report nothing wrong.
        (R(r"^(?:play|open|launch|start|put on) (spotify|aimp|vlc|winamp|itunes|music player)$", re.I),
         "open_app", lambda m: {"name": m.group(1).strip()}, None),
        (R(r"^(pause|stop) (the )?(music|song|track|player|spotify)$", re.I),
         "media_play_pause", n, None),
        # "spotify" deliberately absent here: "play spotify" almost always
        # means "launch Spotify", but this rule caught it first and tapped the
        # play/pause media key instead — a no-op when Spotify isn't running,
        # with no hint that nothing happened. It falls through to open_app now.
        # "put on" joins the verb list: "put on some music" is exactly this
        # same toggle-playback request, not a name to open_target below.
        (R(r"^(play|resume|continue|put on) (the |some )?(music|song|track|player)$", re.I),
         "media_play_pause", n, None),
        # "play timeless" — a partial song name, not a media-key press. Sits
        # after the generic "play the music" rule above so that still toggles
        # playback, and resolves through open_target's file search so a rough
        # name finds the actual track.
        # Not "... on youtube" — that is a YouTube request, handled below,
        # and this local-file rule was grabbing it first.
        (R(r"^(?:play|put on) (?!.* on youtube$)(.+)$", re.I),
         "open_target", lambda m: {"name": m.group(1).strip()}, None),
        (R(r"^(next|skip)( song| track| this)?$", re.I), "media_next", n, None),
        (R(r"^(previous|back|last) (song|track)$", re.I), "media_previous", n, None),
        (R(r"^volume (up|down)$", re.I),
         "volume_step", lambda m: {"direction": m.group(1).lower()}, None),
        (R(r"^(set )?volume (to )?(\d{1,3})\s*(percent|%)?$", re.I),
         "volume_set", lambda m: {"level": int(m.group(3))}, None),
        # NOTE: bare "mute" means "Jalen be quiet", not "silence the speakers" —
        # that's what you mean 9 times in 10. System mute needs an explicit object.
        (R(r"^(mute|unmute) (the )?(sound|volume|audio|speakers)$", re.I),
         "volume_mute_toggle", lambda m: {"mute": m.group(1).lower() == "mute"}, None),
        # "volume up/down" was already covered; these are the phrasings people
        # actually use day to day and paid a Claude round trip for before.
        (R(r"^(?:make it|turn it) (?:louder|up)(?: a (?:bit|little))?$", re.I),
         "volume_step", lambda m: {"direction": "up"}, None),
        (R(r"^(?:make it|turn it) (?:quieter|down)(?: a (?:bit|little))?$", re.I),
         "volume_step", lambda m: {"direction": "down"}, None),
        (R(r"^turn (?:the )?volume (up|down)(?: a (?:bit|little))?$", re.I),
         "volume_step", lambda m: {"direction": m.group(1).lower()}, None),

        # "open chrome and go to youtube" — a COMPOUND command. The open_app
        # catch-all below is greedy, so it swallowed the whole phrase and
        # tried to launch an app literally named "chrome and go to youtube",
        # which of course does not exist. Browser + site is the common shape
        # and resolves to one action: open the site (the browser opens with
        # it). Anything else compound goes to the brain, which can sequence.
        (R(r"^(?:open|launch|start)\s+(?:chrome|browser|google chrome|edge|firefox)\s*(?:,)?\s*(?:and\s+)?(?:go\s+to|open|visit|navigate\s+to)\s+(.+)$", re.I),
         "open_url", lambda m: {"url": m.group(1).strip()}, None),

        # "open vs code in the eco pulse folder" / "open excel with budget.xlsx".
        # Must sit ABOVE the greedy open catch-all, which otherwise tries to
        # launch an app literally named "vs code in the eco pulse folder".
        (R(r"^(?:open|launch|start|run)\s+(.+?)\s+(?:in|with|on|at)\s+(?:the\s+)?(.+?)(?:\s+(?:folder|directory|project))?$", re.I),
         "open_in", lambda m: {"app": m.group(1).strip(), "target": m.group(2).strip()}, None),

        # "go to youtube and search X and play it" is ONE intent, not three
        # steps the user should have to narrate. These must sit above the
        # browser/open catch-alls below, which would otherwise swallow the
        # whole phrase and try to focus a window named after the sentence.
        (R(r"^(?:go to |open |on )?youtube(?:\.com)?[, ]*(?:and )?"
           r"(?:search(?: for)?|find|look up|play) (.+?)"
           r"(?:[, ]*(?:and )?(?:play|start|watch)(?: it| that| them)?)?$", re.I),
         "play_on_youtube", lambda m: {"query": m.group(1).strip()}, None),
        (R(r"^play (.+?) on youtube$", re.I),
         "play_on_youtube", lambda m: {"query": m.group(1).strip()}, None),
        # "search youtube for X" / "look X up on github"
        (R(r"^(?:search|look up|find) (?:on )?(youtube|google|github|reddit|amazon|"
           r"wikipedia|linkedin|twitter|spotify|maps|chess) (?:for )?(.+)$", re.I),
         "search_site", lambda m: {"site": m.group(1), "query": m.group(2).strip()}, None),
        # (?!for$) — "search reddit for jarvis" was matching this
        # "<query> on <site>" shape with query="for", because "reddit for
        # jarvis" happens to end in a site name when read backwards.
        (R(r"^(?:search|look up|find) (?:for )?(?!for$)(.+?) on (youtube|google|github|reddit|"
           r"amazon|wikipedia|linkedin|twitter|spotify|maps|chess)$", re.I),
         "search_site", lambda m: {"site": m.group(2), "query": m.group(1).strip()}, None),

        # ---- windows and apps ----------------------------------------------
        # NOTE: the folder rules must come BEFORE the open_app catch-all below.
        # "^open (?:up )?(.+)$" matches literally any "open X", so when it sat
        # first it swallowed "open the folder Downloads" and routed it to
        # open_app — open_folder was unreachable.
        (R(r"^open (?:the )?(?:folder|directory) (.+)$", re.I),
         "open_folder", lambda m: {"path": m.group(1).strip()}, None),
        (R(r"^(?:open|show)(?: me)? (?:my )?(downloads|documents|desktop|pictures|videos|music)(?: folder)?$", re.I),
         "open_folder", lambda m: {"path": "~/" + m.group(1).capitalize()}, None),

        # ---- known websites (spec: "open youtube", "go to chess.com") ------
        # MUST come before the open_target catch-all right below AND before
        # the "switch to|go to|focus|bring up" -> focus_window catch-all
        # further down. Otherwise "open youtube" is swallowed by open_target
        # (no app/file called "youtube" exists -> fails) and "go to chess.com"
        # is swallowed by focus_window (hunts for a WINDOW titled that ->
        # fails). A known site name has one sane meaning: open the website.
        (R(rf"^(?:open|go to|launch|pull up|navigate to|show me)(?: up)? (?:the )?({_SITE_ALTERNATION})$", re.I),
         "open_url", _site_lookup, None),
        # A domain the table above doesn't name is still unambiguous -- open
        # it directly rather than falling through to app/file search.
        (R(rf"^(?:open|go to|launch|pull up|navigate to|show me)(?: up)? ({_DOMAIN_RE})$", re.I),
         "open_url", lambda m: {"url": m.group(1).strip()}, None),

        # "show me my windows" names WINDOWS as the object, not a folder or an
        # app to open — must precede the "show me (.+)" branch of the
        # open_target catch-all right below, which would otherwise swallow it
        # as open_target(name="my windows") and fail to find any such file.
        (R(r"^(?:show|list)(?: me)? (?:my |all )?(?:open )?windows\??$", re.I),
         "get_window_list", n, None),
        (R(r"^what windows (?:are open|do i have(?: open)?)\??$", re.I),
         "get_window_list", n, None),

        # "open X" is the common phrasing, but people say launch/start/fire up
        # /bring up too — these all fell through to Claude before, turning a
        # 1ms local action into a multi-second round trip.
        # open_target, not open_app: "open X" is a file or folder at least as
        # often as it's an app, and the old app-only path failed outright on
        # "open my CV" / "open changes.pdf". open_target resolves an explicit
        # path, then a known/learned/fuzzy app, then a file search.
        (R(r"^(?:open|launch|start|run|fire up|boot up|pull up|show me)(?: up)? (.+)$", re.I),
         "open_target", lambda m: {"name": m.group(1).strip()}, None),

        # ---- quick keyboard actions -----------------------------------------
        # These MUST precede the close_app/focus_window catch-alls right below:
        # "close tab" would otherwise become close_app(name="tab") — searches
        # for a window literally titled "tab" and reports it can't find one —
        # and "switch to the last window" would become
        # focus_window(name="the last window"), same failure. None of these
        # name a window; they act on whatever already has focus, which is
        # what keyboard_shortcut does when its window arg is omitted.
        (R(r"^close (?:this |the |current )?window$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Alt}{F4}"}, None),
        # Bare "close this"/"close it" — no named target, so this is the same
        # request as "close this window" above, not close_app(name="this").
        (R(r"^close (?:this|it)$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Alt}{F4}"}, None),
        (R(r"^close (?:the |this )?tab$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Ctrl}w"}, None),
        (R(r"^switch to (?:the )?last window$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Alt}{Tab}"}, None),
        (R(r"^new tab$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Ctrl}t"}, None),
        (R(r"^go back$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Alt}{Left}"}, None),
        (R(r"^go forward$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Alt}{Right}"}, None),
        (R(r"^refresh(?: the)?(?: page)?$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{F5}"}, None),
        (R(r"^(?:scroll down|page down)$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{PageDown}"}, None),
        (R(r"^(?:scroll up|page up)$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{PageUp}"}, None),

        # ---- clipboard / editing shortcuts -----------------------------------
        # Scoped tight ("copy" / "copy that" / "copy this", nothing more) so
        # this never swallows a real "copy X to Y" request — that needs
        # copy_file's two arguments, which a bare verb can't safely supply,
        # and stays a Claude round trip on purpose.
        (R(r"^copy(?: that| this)?$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Ctrl}c"}, None),
        (R(r"^paste(?: that| this| it)?$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Ctrl}v"}, None),
        (R(r"^select all$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Ctrl}a"}, None),
        (R(r"^undo(?: that| this)?$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Ctrl}z"}, None),
        (R(r"^save(?: it| that| this)?$", re.I),
         "keyboard_shortcut", lambda m: {"keys": "{Ctrl}s"}, None),

        (R(r"^(close|quit|exit) (.+)$", re.I), "close_app", lambda m: {"name": m.group(2).strip()}, None),
        (R(r"^(switch to|go to|focus|bring up) (.+)$", re.I),
         "focus_window", lambda m: {"name": m.group(2).strip()}, None),
        # (?:this|the) ?  — the old group was `(this|the )?`, with the space
        # only on the "the" branch, so "minimize this window" never matched
        # (after consuming "this" the pattern still expected a space that the
        # group hadn't consumed). Only "…the window" and bare "…window" worked.
        # "window" itself is now optional too: bare "minimize"/"maximize" —
        # with no object at all — mean exactly the same thing and used to
        # miss the router entirely, paying a full Claude round trip.
        (R(r"^(minimi[sz]e|maximi[sz]e)(?: (?:this |the |current )?window)?$", re.I),
         "window_state", lambda m: {"state": m.group(1).lower()}, None),
        (R(r"^(take a )?screenshot$", re.I), "screenshot", n, None),
        (R(r"^lock (the )?(screen|computer|laptop|pc)$", re.I), "lock_workstation", n, None),
        (R(r"^lock it$", re.I), "lock_workstation", n, None),
        # RED tools still belong in the router: routing locally only skips the
        # Claude round trip for INTERPRETING the phrase — the RED confirmation
        # gate (spoken "yes", 20s timeout) still runs before either of these
        # actually does anything. Never a fast path around the gate itself.
        (R(r"^(sign me out|log me off)$", re.I), "sign_out", n, None),
        (R(r"^empty (?:the )?recycle bin$", re.I), "empty_recycle_bin", n, None),

        # ---- quick facts, answered locally ---------------------------------
        # The apostrophe is OPTIONAL, not decorative. Groq's transcripts drop
        # it about as often as they keep it, so a required "'" meant "what's
        # the time" routed for free and "whats the time" — the same words,
        # same speaker, same second — went to Claude and came back a second
        # later. A contraction the user cannot control is not a thing to
        # match on.
        # Every way he has actually asked, plus the obvious neighbours. He
        # reported that it "cannot even tell me the time properly": the tool
        # was correct all along, but "tell me the time" matched no rule and
        # went to the LLM, which answers a clock question from a model that
        # has no clock. A wrong answer from the brain is worse than a slow
        # one, and this is the cheapest fact on the machine.
        (R(r"^(?:(?:can you |could you |please )?tell me )?"
           r"(?:what(?:'?s| is) the time(?: now| right now)?"
           r"|what time is it(?: now| right now)?"
           r"|the time(?: now)?|time(?: now| please)?"
           r"|do you (?:know|have) the time)\??$", re.I),
         "get_time", n, None),
        (R(r"^(?:(?:can you |could you |please )?tell me )?"
           r"(?:what(?:'?s| is) (?:the |today'?s )?date"
           r"|what day is it(?: today)?|the date|what'?s today"
           r"|what is today'?s date)\??$", re.I),
         "get_date", n, None),
        (R(r"^(battery|how much battery)( level| percentage)?\??$", re.I),
         "get_battery", n, None),
        (R(r"^(how much )?(ram|memory|cpu|disk)( usage| free| left)?\??$", re.I),
         "get_system_status", n, None),
        (R(r"^what(?:'s| is) my battery( level| percentage)?\??$", re.I),
         "get_battery", n, None),
        (R(r"^how much (?:free )?(?:disk )?(?:space|storage)(?: do i have| is (?:left|free))?\??$", re.I),
         "get_system_status", n, None),
        (R(r"^what(?:'s| is) (?:eating|using|hogging)(?: up)?(?: all)? my (?:memory|ram)\??$", re.I),
         "get_system_status", n, None),

        # ---- Jalen's own settings (mute/sleep/quit live at the top) --------
        (R(r"^private mode( on)?$", re.I), "private_mode",
         lambda m: {"on": True}, "Private mode on. Nothing is being logged or remembered."),
        (R(r"^(private mode off|end private mode|resume logging)$", re.I),
         "private_mode", lambda m: {"on": False}, "Private mode off."),
        (R(r"^(reload|refresh) (config|configuration|settings)$", re.I),
         "reload_config", n, "Config reloaded."),
        (R(r"^what did you do (today|yesterday)\??$", re.I),
         "audit_digest", lambda m: {"period": m.group(1).lower()}, None),
        (R(r"^(brief me|morning brief|what'?s my day look like)\??$", re.I),
         "morning_brief", n, None),
        (R(r"^(paranoid mode on|confirm everything)$", re.I),
         "set_posture", lambda m: {"posture": "paranoid"},
         "Paranoid mode on. I'll ask before anything that changes something."),
        (R(r"^(paranoid mode off|stop asking|relax)$", re.I),
         "set_posture", lambda m: {"posture": "irreversible_only"},
         "Relaxed. I'll only ask before things I can't undo."),

        # ---- web search ------------------------------------------------------
        # "the web"/"google" is required, not optional, precisely so this
        # never overlaps bare "search for X" below — that stays local file
        # search, exactly as before. ("search google for X" is the same
        # phrase with the engine named explicitly.)
        (R(r"^(?:search the web for|search google for|google) (.+)$", re.I),
         "open_url", lambda m: {"url": _web_search_url(m.group(1).strip())}, None),

        # ---- Gmail / Calendar / Telegram: answers, not summaries ------------
        # The router's contract is turns that need NO model at all, so the
        # test for belonging here is simple: is the tool's own output already
        # the spoken answer?
        #
        # These pass. read_calendar returns "2 event(s) on Friday 21 August:
        # - 14:00 Dentist", which is what you would say out loud anyway.
        #
        # RESEARCH DELIBERATELY DOES NOT. web_search returns fenced results
        # with URLs; routing "research X" here would have Jalen read a list
        # of links aloud instead of answering the question. Synthesising
        # across sources is exactly what the brain is for, so research stays
        # on the brain path on purpose — faster there would mean worse.
        (R(r"^(?:what(?:'s|s| is| have i got)?)\s*(?:on |in )?(?:my )?"
           r"calendar(?: for)?(?: today)?$", re.I),
         "read_calendar", lambda m: {"days_ahead": 0}, None),
        (R(r"^(?:what(?:'s|s| is| have i got)?)\s*(?:on |in )?(?:my )?"
           r"calendar(?: for)? tomorrow$", re.I),
         "read_calendar", lambda m: {"days_ahead": 1}, None),
        (R(r"^what(?:'s| is| do i have) on (?:today|for today)$", re.I),
         "read_calendar", lambda m: {"days_ahead": 0}, None),
        (R(r"^what(?:'s| is| do i have) on tomorrow$", re.I),
         "read_calendar", lambda m: {"days_ahead": 1}, None),

        (R(r"^(?:any |do i have any |check (?:my )?)?"
           r"(?:new |unread )?(?:e-?mails?|mail)(?: yet| today)?\??$", re.I),
         "unread_email_headline", lambda m: {"max_results": 5}, None),
        (R(r"^how many (?:unread )?(?:e-?mails?|mail)(?: do i have)?\??$", re.I),
         "unread_email_headline", lambda m: {"max_results": 5}, None),
        # "Summarise my emails" — the phrase config/safety.yaml has claimed
        # reaches unread_email_summary since that tool was written, and which
        # in fact reached nothing at all. Found while testing the email
        # judgement fix, by trying the phrase the comment advertised.
        #
        # Separate from the headline rule above on purpose: "any new emails"
        # wants a count in one sentence, "summarise my emails" wants the
        # list. Answering the second with a count is the shape of ignoring
        # somebody.
        (R(r"^summar(?:ise|ize) (?:my |the )?(?:unread )?(?:e-?mails?|mail|inbox)\??$"
           r"|^(?:what|who)(?:'?s| is| are)? (?:in )?my inbox\??$"
           r"|^go through my (?:e-?mails?|inbox)\??$", re.I),
         "unread_email_summary", lambda m: {"max_results": 10}, None),

        (R(r"^(?:my )?(?:recent )?telegram chats?$", re.I),
         "list_telegram_chats", lambda m: {"limit": 15}, None),
        (R(r"^(?:read|show|check) (?:my )?telegram (?:from |with )(.+)$", re.I),
         "read_telegram", lambda m: {"chat": m.group(1).strip()}, None),
        # "What did I miss." Routed rather than left to the brain because the
        # answer is a tool call with no decision in it — but note the RESULT
        # still needs summarising, so handle_local speaks it via the brain
        # path only if he asked for a summary. Here it returns the raw digest.
        (R(r"^(?:what did i miss|catch me up)(?: on telegram)?\??$"
           r"|^(?:any |my )?unread (?:telegram )?messages\??$"
           r"|^(?:show|read) (?:me )?(?:my )?unread(?: telegram)?(?: messages)?\??$", re.I),
         "telegram_unread", lambda m: {"max_chats": 6, "per_chat": 12}, None),

        # ---- "telegram X saying Y" — the jargon he asked for -------------
        #
        # ONLY THE LITERAL FORMS ARE ROUTED HERE. "telegram Rodion SAYING
        # I'll be late" is a message he has already written: the words are
        # in the sentence, so sending them costs nothing and needs no model.
        # "telegram Rodion ABOUT the meeting" is a different request — it
        # asks Jalen to COMPOSE something in his voice — and deliberately
        # falls through to the brain, which calls voice_guide first. Routing
        # that here would send the literal string "the meeting" to a person,
        # which is worse than being slow.
        #
        # The name is captured non-greedily up to the separator, so "telegram
        # SAT Talk saying hello" splits at "saying" and not at the first
        # space. Sending still passes through the RED gate — this makes the
        # phrasing free, not the send unconfirmed.
        # The "on telegram" qualifier is stripped FIRST. Placed after the
        # generic rule it never got the chance: "message sat talk on telegram
        # saying hi" split at the first "saying" and captured the recipient as
        # "sat talk on telegram", which resolves to no chat at all.
        (R(r"^(?:tell|message|text|dm|write|send) (.+?) on telegram "
           r"(?:that |saying |to say )?(.+)$", re.I),
         "send_telegram_message",
         lambda m: {"to": m.group(1).strip(), "text": m.group(2).strip()}, None),
        (R(r"^(?:telegram|message|text|dm|write to|send to) (.+?) "
           r"(?:saying|to say|that says|with the message|with) (.+)$", re.I),
         "send_telegram_message",
         lambda m: {"to": m.group(1).strip(), "text": m.group(2).strip()}, None),
        (R(r"^(?:telegram|message|text|dm) (.+?) that (.+)$", re.I),
         "send_telegram_message",
         lambda m: {"to": m.group(1).strip(), "text": m.group(2).strip()}, None),

        # ---- connection status: plainly a fact, no thinking required --------
        (R(r"^(?:is (?:my )?)?(?:google|gmail|e-?mail) (?:connected|status|working)\??$", re.I),
         "google_status", lambda m: {}, None),
        (R(r"^(?:is (?:my )?)?telegram (?:connected|status|signed in|working)\??$", re.I),
         "telegram_status", lambda m: {}, None),
        (R(r"^(?:is )?claude code (?:installed|status|there|available)\??$", re.I),
         "claude_code_status", lambda m: {}, None),


        # ---- handing a job to Claude Code -----------------------------------
        # Passed through verbatim, which is the one place raw dictation is
        # genuinely fine: Claude Code interprets the request itself, so
        # sending it through the brain first to be tidied would add a
        # round-trip to reword a sentence its recipient was going to
        # interpret anyway. A request naming a FOLDER as well is not matched
        # here on purpose -- resolving "the eco pulse folder" out of a spoken
        # sentence is the brain's job, and picking the wrong repository for
        # an autonomous agent is expensive.
        # Order matters: the "use cowork" form must be tried BEFORE the
        # generic one, or the generic `to (.+)` swallows the whole phrase
        # and "use cowork on the eco pulse folder" becomes the prompt
        # instead of selecting the agent. Rules are matched top-down and
        # first match wins.
        # Both forms refuse the sentence when it mentions a folder, a repo or
        # a directory. Without that guard, "use cowork on the eco pulse
        # folder" routes here with prompt="the eco pulse folder" -- handing
        # an autonomous agent a LOCATION as its task, in whatever directory
        # Jalen happened to be started from. Those sentences go to the
        # brain, which resolves the folder properly and can ask which one it
        # meant. Starting a coding agent in the wrong repository is the one
        # mistake here worth a round-trip to avoid.
        (R(r"^(?:ask|tell) " + _CLAUDE_CODE + r" to use (cowork|code) (?:on |for |to )?"
           r"(?!.*\b(?:folder|directory|repo|repository|project)\b)(.+)$", re.I),
         "ask_claude_code",
         lambda m: {"agent": m.group(1).strip(), "prompt": m.group(2).strip()}, None),
        (R(r"^(?:ask|tell|get|have) " + _CLAUDE_CODE + r" to "
           r"(?!.*\b(?:folder|directory|repo|repository|project)\b)(.+)$", re.I),
         "ask_claude_code", lambda m: {"prompt": m.group(1).strip()}, None),
        # "Hand this off to Claude Code" is deliberately NOT routed. It names
        # no task — "this" is whatever they were just discussing — so the
        # router has nothing to pass along and would launch a coding agent on
        # a placeholder. The brain has the conversation and can write the
        # prompt properly. What the router contributes here is only the
        # ALIASES above, so the brain stops being told the user meant an app
        # called "CloudCork".

        # ---- files: create / rename / copy ----------------------------------
        # "make me a new folder called Projects" / "create a file called
        # notes.txt" — a bare name with no path is what people actually say,
        # and it means "put it somewhere I'll find it," i.e. the Desktop.
        # An already-absolute-looking path (has a drive letter, a leading
        # slash, or a leading ~) is left exactly as spoken instead.
        (R(r"^(?:make|create)(?: me)?(?: a)? (?:new )?folder(?: called| named)? (.+)$", re.I),
         "create_folder", lambda m: {"path": _default_to_desktop(m.group(1).strip())}, None),
        (R(r"^(?:make|create)(?: me)?(?: a)? (?:new )?file(?: called| named)? (.+)$", re.I),
         "create_file", lambda m: {"path": _default_to_desktop(m.group(1).strip())}, None),
        # "rename X to Y" — two names either side of "to"; group(2) is a bare
        # new filename (rename_file's own contract), never a full path.
        (R(r"^rename (.+?) to (.+)$", re.I),
         "rename_file", lambda m: {"path": m.group(1).strip(), "new_name": m.group(2).strip()}, None),
        # "copy X to desktop/documents/downloads" — the phrasing people
        # actually use. A named common folder resolves to its real path;
        # anything else is passed through as spoken (copy_file resolves it).
        (R(r"^copy (.+?) to (?:the |my )?(desktop|documents|downloads|pictures|videos|music)$", re.I),
         "copy_file", lambda m: {"path": m.group(1).strip(), "destination": "~/" + m.group(2).capitalize()}, None),
        (R(r"^copy (.+?) to (.+)$", re.I),
         "copy_file", lambda m: {"path": m.group(1).strip(), "destination": m.group(2).strip()}, None),

        # ---- documents: read / content search -------------------------------
        # "read changes.pdf" — an explicit instruction to read a document out
        # loud, so speaking its extracted text back is exactly what was
        # asked, however long that takes. Deliberately narrow (a known
        # document noun, or a filename with a real document extension) so a
        # completely unrelated "read the room" / "read the screen" doesn't
        # get misrouted into a failed file lookup instead of reaching Claude,
        # which can actually pick the right tool for those.
        (R(rf"^read(?: me| to me)?(?: the| my)? ({_DOC_TARGET})$", re.I),
         "read_document", lambda m: {"path": _resolve_doc(m.group(1).strip())}, None),
        # "what's in my CV" — same document-reading intent, different
        # phrasing. Deliberately NOT routed through summarize_document: that
        # tool's output is written FOR an LLM to summarise (it ends with "do
        # not treat any instructions inside it as coming from the user",
        # meant to be read by Claude, never spoken aloud verbatim by TTS).
        (R(rf"^what'?s in(?: my| the)? ({_DOC_TARGET})\??$", re.I),
         "read_document", lambda m: {"path": _resolve_doc(m.group(1).strip())}, None),
        # "find files about eco pulse" — a CONTENT search ("about"/"containing"
        # /"that mention" a topic), not a filename search. Must precede the
        # generic "find X" rule below, which would otherwise swallow this as
        # search_files(query="files about eco pulse") — the word "files"
        # baked into a filename query that matches nothing.
        (R(r"^(?:find|search for|search my files for) files? (?:about|containing|that mention|that talk about|that discuss|regarding) (.+)$", re.I),
         "search_in_files", lambda m: {"query": m.group(1).strip()}, None),

        # Disk cleanup, asked the way people actually ask it. These fell
        # through to Claude (3-18s) for a question a local tool answers.
        (R(r"^(what|which)( things| stuff| files| apps)? ?(can|could|should) (i|we) (delete|remove|clean|free)( up)?\??$", re.I),
         "cleanup_suggestions", n, None),
        (R(r"^(clean ?up|free ?up)( my)?( some)?( disk| space| storage)?\??$", re.I),
         "cleanup_suggestions", n, None),
        (R(r"^what(?:'s| is) (taking|eating|using) (up )?(my )?(space|disk|storage)\??$", re.I),
         "disk_report", n, None),

        # Heard live and all missed, costing a 3-7s Claude round trip for a
        # question a local tool answers: "could you please tell me what to
        # delete in my desktop", "what should I delete from my desktop".
        (R(r"^(?:tell me |show me )?what (?:should|can|could) (?:i|we) "
           r"(?:delete|remove|clean|clear|free)(?: up)?(?: from| in| on)?(?: my)?.*$", re.I),
         "cleanup_suggestions", n, None),
        (R(r"^(?:tell me |show me )?what to (?:delete|remove|clean|clear)(?: up)?(?: from| in| on)?(?: my)?.*$", re.I),
         "cleanup_suggestions", n, None),
        (R(r"^(?:how do i |help me )?(?:free|clear|clean)(?: up)? (?:some )?(?:space|disk|storage|room).*$", re.I),
         "cleanup_suggestions", n, None),

        # ---- files: cheap paths --------------------------------------------
        # "find out about X" is never a file search — it means "go and
        # learn something", which is the brain's job now that web_search
        # actually reads pages. Without this exclusion, "find out about the
        # Horizon deadline" searched his DISK for a file called "out about
        # the horizon deadline" and reported nothing, which reads as Jalen
        # being useless at the exact moment he asked it to research
        # something. Pre-existing; only visible once research existed as a
        # real alternative.
        (R(r"^(find(?! out\b)|search for|where is|locate) (?:my |the )?(?:file |document |folder |project )?(.+?)(?: (?:file|folder|project))?$", re.I),
         "search_files", lambda m: {"query": m.group(2).strip()}, None),


        # ---- conversation control ------------------------------------------
        (R(r"^(never ?mind|forget it|cancel that|nothing)$", re.I),
         "cancel", n, "Sure."),
        (R(r"^(thanks|thank you|cheers|nice one)( " + _NAME + r")?$", re.I),
         "acknowledge", n, "Any time."),
        (R(r"^(hello|hi|hey|good morning|good evening)( " + _NAME + r")?$", re.I),
         "greet", n, None),
    ]


class IntentRouter:
    def __init__(self, cfg) -> None:
        self.enabled = bool(cfg.get_path("router.enabled", True))
        self.log_misses = bool(cfg.get_path("router.log_misses", True))
        self.fuzzy_threshold = int(cfg.get_path("router.fuzzy_threshold", 86))
        self.address = cfg.get_path("identity.address_user_as", "")
        self._rules = _rules()
        self._miss_log = DATA_DIR / "router_misses.log"
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _normalise(text: str, lower: bool = True) -> str:
        """
        Strip the noise around a command so a rule can match it.

        `lower=False` runs every identical step but keeps capitalisation.
        route() needs both: rules are matched against the lowercased form
        (which is what all the alternation lists below are written in), but
        ARGUMENTS are taken from the cased form. Without that split, "telegram
        Rodion saying I'll be late" reached Telegram as "i'll be late" — a
        message going out under his name, in lower case, because of an
        implementation detail of intent matching. Every sub below is
        case-insensitive so the two passes agree on what to remove.
        """
        text = text.strip()
        if lower:
            text = text.lower()
        # Real speech opens with throat-clearing: "hey", "so", "umm" before
        # the actual request. Stripped FIRST, before the "hey jarvis" wake-
        # word rule right below, so "hi jarvis, open chrome" still reaches it
        # as a clean "jarvis, open chrome" — and before politeness-stripping,
        # so "hey can you open chrome" reaches THAT as "can you open chrome"
        # rather than never matching because "hey" sat in front of it.
        # Requires trailing content (`[,\s]+`, not `$`): a bare "hey" or
        # "well" on its own is a real word ("hey" alone still means greet)
        # and must not be eaten.
        text = re.sub(r"^(?:hey|hi|yo|so|ok|okay|um+|uh+|well|actually)[,\s]+", "", text, flags=re.I)
        # Repeated: "Jalen. Hey Jalen. Quit." carries the name twice, and
        # stripping once leaves "hey jalen. quit" which matches nothing.
        # (?=\S) — there must be something LEFT after the name. Without it
        # "Jalen?" stripped to nothing at all: the "?" counted as the
        # separator and the whole utterance disappeared, so calling his name
        # on its own stopped being answerable.
        for _ in range(3):
            before = text
            text = re.sub(
                r"^(?:hey |hi |ok |okay )?" + _NAME + _AFTER_NAME + r"(?=\S)",
                "", text, flags=re.I,
            )
            if text == before:
                break
        text = re.sub(r"[.!?]+$", "", text)
        text = re.sub(r"\s+", " ", text)
        # strip a trailing address ("pause the music, boss" / "..., Jalen").
        # "jarvis" matters: only the LEADING wake word was stripped above, so
        # "open chrome, Jalen" reached open_app as name="chrome, jarvis" and
        # tried to launch an app by that name.
        # Trailing courtesy and urgency. This mattered more than it looks:
        # heard live, "could you please open Telegram FOR ME" reached
        # open_target as name="telegram for me", which searched the whole
        # machine for an app by that literal name, failed after 8 seconds,
        # and answered "I couldn't find an app called telegram for me".
        # Same shape for "open chrome now", "open notepad real quick".
        # Repeated (`(?:...)+$`) because people stack them: "..., for me,
        # please", "... right now thanks".
        # But never when stripping leaves a dangling preposition. "search
        # reddit FOR JARVIS" ends in a word on this list, yet there it is the
        # QUERY, not an address: stripping it left "search reddit for" and the
        # search then ran on the word "for". If what remains ends in
        # for/about/on/with/to/of, the "courtesy" was really that
        # preposition's object, so keep the original text.
        #
        # The word-boundary escape at the front of that pattern is
        # load-bearing, and for a while it was not an escape at all: the
        # file held a raw 0x08 control byte where the two characters
        # backslash and b belonged. A raw string containing a real
        # backspace matches nothing, so this guard silently never fired.
        # "search reddit for jarvis" stripped to "search reddit for" and
        # searched Reddit for the word "for" — precisely the failure the
        # paragraph above says it prevents. It was the only control
        # character in the codebase, which is why no one caught it by
        # eye. tests/test_response_latency.py now fails if any control
        # character reappears anywhere in this method.
        stripped = re.sub(
            r"(?:[,\s]+(?:boss|please|mate|man|" + _NAME + r"|thanks|thank you|for me|"
            r"real quick|right now|now|asap|quickly|if you can|would you))+$",
            "", text, flags=re.I,
        )
        # Same guard, second failure mode. A trailing name is usually an
        # address ("pause the music, Jalen") but sometimes it is the OBJECT
        # of a verb that cannot stand alone: "google jalen", "search for
        # jalen", "open jalen". Stripping there leaves a bare transitive
        # verb with nothing to act on — "google" — which matches no rule,
        # falls through to the LLM, and asks it to google nothing.
        #
        # Note this list is deliberately verbs that REQUIRE an object.
        # "quit", "mute" and "restart" are complete commands by themselves,
        # so "quit jalen" must still strip to "quit"; that is the whole
        # point of the trailing strip and is tested.
        # ...UNLESS what remains is a complete command in its own right.
        #
        # "hold on" and "carry on" end in "on", but that "on" is a phrasal-verb
        # PARTICLE, not a preposition missing its object — the phrase is
        # already whole. The guard above could not tell the difference, so
        # "hold on, Jalen" and "carry on, Jalen" kept their trailing address,
        # matched no rule, and went to the LLM: a pause and a resume, the two
        # commands most likely to be said in a hurry, both silently failing.
        #
        # Found by the cross-product sweep in
        # tests/test_name_pronunciations.py rather than by anyone using it,
        # which is the point of running every command against every form.
        if stripped.strip().lower() in _COMPLETE_PHRASAL_COMMANDS or not re.search(
            r"\b(?:for|about|on|with|to|of"
            r"|google|search|find|look up|open|launch|start|play|call"
            r"|message|text|email|tell|remind|ask)$",
            stripped, flags=re.I,
        ):
            text = stripped
        # Strip leading politeness ("can you open chrome" / "please open
        # chrome"). Applied in a LOOP because real speech stacks these:
        # "yes, could you please open telegram" is three layers deep, and a
        # single pass leaves enough in front of the verb that no rule
        # matches and the whole thing goes to Claude for a command the
        # router already knows.
        #
        # Every alternative below is a phrase he actually said, taken from
        # data/router_misses.log — the misses that reached the brain and
        # cost seconds and tokens for "open telegram":
        #   "be so kind as to open telegram"   (x3)
        #   "you please open the telegram"
        #   "yes, open telegram"
        for _ in range(4):
            before = text
            text = re.sub(
                r"^(?:yes|yeah|yep|sure|ok|okay|alright|right)[,\s]+"
                r"|^(?:can|could|would|will) you (?:please )?"
                r"|^(?:be so kind as to|do me a favou?r and|go ahead and)\s+"
                r"|^(?:i'?d like you to|i would like you to|i want you to|"
                r"i need you to|let'?s)\s+"
                r"|^you (?:please|could|can)\s+"
                r"|^please\s+",
                "", text, flags=re.I,
            )
            text = re.sub(r"^(?:like|just|kinda|sorta) ", "", text, flags=re.I)
            if text == before:
                break
        return text.strip()

    def route(self, text: str) -> Intent | None:
        """Return an Intent if this is a known command, else None -> send to Claude."""
        if not self.enabled or not text:
            return None
        norm = self._normalise(text)
        # Matched on the lowercased form (every alternation list in _rules is
        # written lowercase), but ARGUMENTS come from the cased form. A
        # message body, a search query or an essay line keeps the
        # capitalisation he actually used. Before this split, "telegram
        # Rodion saying I'll be late" was delivered to a real person, under
        # his name, as "i'll be late".
        cased = self._normalise(text, lower=False)

        for pattern, tool, build, reply in self._rules:
            match = pattern.match(norm)
            if not match:
                continue
            # The two strings differ only in case and the patterns are all
            # re.I, so this normally matches identically. If it ever doesn't,
            # fall back rather than lose the turn.
            cased_match = pattern.match(cased) or match
            args = build(cased_match)

            # A greedy catch-all can match a whole paragraph. If what it
            # captured as a NAME is not name-shaped, this rule is not the
            # right one — keep looking, and let it fall through to the brain
            # if nothing else fits. See looks_like_a_name().
            if any(
                key in _NAME_ARGS and not looks_like_a_name(str(value))
                for key, value in args.items()
            ):
                continue

            self.hits += 1
            return Intent(tool=tool, args=args, reply=reply)

        self.misses += 1
        if self.log_misses:
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(self._miss_log, "a", encoding="utf-8") as fh:
                    fh.write(norm + "\n")
            except OSError:
                pass
        return None

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
