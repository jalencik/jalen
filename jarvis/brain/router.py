"""
The local intent router — the single most important cost decision in this build.

Why it exists: Agent SDK usage draws on your Claude Pro allowance. A voice
assistant you talk to all day will send hundreds of turns. If "pause the music"
costs a Claude round-trip, you will hit your limit by Thursday and Jarvis goes
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
    nobody means "in whatever directory Jarvis happens to be running from."
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
    Jarvis's process happens to be running and almost always fail. Reuse
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
        # literally named "sleep"). Jarvis's own commands go FIRST, because a
        # command aimed at Jarvis must never be mistaken for one aimed at an
        # app that happens to share a word.
        # ====================================================================

        # ---- talking to Jarvis itself --------------------------------------
        (R(r"^(mute|be quiet|shut up|silence)( yourself)?$", re.I),
         "jarvis_mute", n, "Muted."),
        (R(r"^(unmute|speak|you can talk)( now)?$", re.I),
         "jarvis_unmute", n, "Back."),
        (R(r"^(go to sleep|sleep|stand by|stop listening)$", re.I),
         "jarvis_sleep", n, "Sleeping. Say hey Jarvis to wake me."),
        (R(r"^(quit|exit|shut ?down|close|kill|turn off)( jarvis| yourself)?$", re.I),
         "jarvis_quit", n, "Shutting down. See you, Boss."),
        (R(r"^(pause|hold on|take a break)( jarvis)?$", re.I),
         "jarvis_pause", n, "Paused. Say hey Jarvis when you want me back."),
        (R(r"^(resume|carry on|start listening|i'?m back|wake up)( jarvis)?$", re.I),
         "jarvis_resume", n, "Listening again."),
        (R(r"^(restart|reboot|reload)( jarvis| yourself)?$", re.I),
         "jarvis_restart", n, "Restarting."),

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
        (R(r"^(?:play|put on) (.+)$", re.I),
         "open_target", lambda m: {"name": m.group(1).strip()}, None),
        (R(r"^(next|skip)( song| track| this)?$", re.I), "media_next", n, None),
        (R(r"^(previous|back|last) (song|track)$", re.I), "media_previous", n, None),
        (R(r"^volume (up|down)$", re.I),
         "volume_step", lambda m: {"direction": m.group(1).lower()}, None),
        (R(r"^(set )?volume (to )?(\d{1,3})\s*(percent|%)?$", re.I),
         "volume_set", lambda m: {"level": int(m.group(3))}, None),
        # NOTE: bare "mute" means "Jarvis be quiet", not "silence the speakers" —
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
        (R(r"^what(?:'s| is) the time\??$|^what time is it\??$", re.I),
         "get_time", n, None),
        (R(r"^what(?:'s| is) (?:the |today'?s )?date\??$|^what day is it\??$", re.I),
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

        # ---- Jarvis's own settings (mute/sleep/quit live at the top) --------
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

        # ---- files: cheap paths --------------------------------------------
        (R(r"^(find|search for|where is|locate) (?:my |the )?(?:file |document |folder |project )?(.+?)(?: (?:file|folder|project))?$", re.I),
         "search_files", lambda m: {"query": m.group(2).strip()}, None),


        # ---- conversation control ------------------------------------------
        (R(r"^(never ?mind|forget it|cancel that|nothing)$", re.I),
         "cancel", n, "Sure."),
        (R(r"^(thanks|thank you|cheers|nice one)( jarvis)?$", re.I),
         "acknowledge", n, "Any time."),
        (R(r"^(hello|hi|hey|good morning|good evening)( jarvis)?$", re.I),
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
    def _normalise(text: str) -> str:
        text = text.strip().lower()
        # Real speech opens with throat-clearing: "hey", "so", "umm" before
        # the actual request. Stripped FIRST, before the "hey jarvis" wake-
        # word rule right below, so "hi jarvis, open chrome" still reaches it
        # as a clean "jarvis, open chrome" — and before politeness-stripping,
        # so "hey can you open chrome" reaches THAT as "can you open chrome"
        # rather than never matching because "hey" sat in front of it.
        # Requires trailing content (`[,\s]+`, not `$`): a bare "hey" or
        # "well" on its own is a real word ("hey" alone still means greet)
        # and must not be eaten.
        text = re.sub(r"^(?:hey|hi|yo|so|ok|okay|um+|uh+|well|actually)[,\s]+", "", text)
        text = re.sub(r"^(hey |ok |okay )?jarvis[,\s]+", "", text)
        text = re.sub(r"[.!?]+$", "", text)
        text = re.sub(r"\s+", " ", text)
        # strip a trailing address ("pause the music, boss" / "..., Jarvis").
        # "jarvis" matters: only the LEADING wake word was stripped above, so
        # "open chrome, Jarvis" reached open_app as name="chrome, jarvis" and
        # tried to launch an app by that name.
        text = re.sub(r"[,\s]+(boss|please|mate|man|jarvis|thanks|thank you)$", "", text)
        # strip leading politeness ("can you open chrome" / "please open chrome")
        text = re.sub(r"^(can|could|would) you (please )?|^please |^i want you to ", "", text)
        # a filler word wedged between the politeness and the actual verb
        # ("can you LIKE open chrome") — never part of any real command.
        text = re.sub(r"^(?:like|just|kinda|sorta) ", "", text)
        return text.strip()

    def route(self, text: str) -> Intent | None:
        """Return an Intent if this is a known command, else None -> send to Claude."""
        if not self.enabled or not text:
            return None
        norm = self._normalise(text)

        for pattern, tool, build, reply in self._rules:
            match = pattern.match(norm)
            if match:
                self.hits += 1
                return Intent(tool=tool, args=build(match), reply=reply)

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
