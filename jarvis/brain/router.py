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

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


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
        (R(r"^(play|resume|continue) (the )?(music|song|track|player)$", re.I),
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

        # ---- windows and apps ----------------------------------------------
        # NOTE: the folder rules must come BEFORE the open_app catch-all below.
        # "^open (?:up )?(.+)$" matches literally any "open X", so when it sat
        # first it swallowed "open the folder Downloads" and routed it to
        # open_app — open_folder was unreachable.
        (R(r"^open (?:the )?(?:folder|directory) (.+)$", re.I),
         "open_folder", lambda m: {"path": m.group(1).strip()}, None),
        (R(r"^(?:open|show)(?: me)? (?:my )?(downloads|documents|desktop|pictures|videos|music)(?: folder)?$", re.I),
         "open_folder", lambda m: {"path": "~/" + m.group(1).capitalize()}, None),
        # "open X" is the common phrasing, but people say launch/start/fire up
        # /bring up too — these all fell through to Claude before, turning a
        # 1ms local action into a multi-second round trip.
        # open_target, not open_app: "open X" is a file or folder at least as
        # often as it's an app, and the old app-only path failed outright on
        # "open my CV" / "open changes.pdf". open_target resolves an explicit
        # path, then a known/learned/fuzzy app, then a file search.
        (R(r"^(?:open|launch|start|run|fire up|boot up|pull up|show me)(?: up)? (.+)$", re.I),
         "open_target", lambda m: {"name": m.group(1).strip()}, None),
        (R(r"^(close|quit|exit) (.+)$", re.I), "close_app", lambda m: {"name": m.group(2).strip()}, None),
        (R(r"^(switch to|go to|focus|bring up) (.+)$", re.I),
         "focus_window", lambda m: {"name": m.group(2).strip()}, None),
        # (?:this|the) ?  — the old group was `(this|the )?`, with the space
        # only on the "the" branch, so "minimize this window" never matched
        # (after consuming "this" the pattern still expected a space that the
        # group hadn't consumed). Only "…the window" and bare "…window" worked.
        (R(r"^(minimi[sz]e|maximi[sz]e) (?:this |the )?window$", re.I),
         "window_state", lambda m: {"state": m.group(1).lower()}, None),
        (R(r"^(take a )?screenshot$", re.I), "screenshot", n, None),
        (R(r"^lock (the )?(screen|computer|laptop|pc)$", re.I), "lock_workstation", n, None),

        # ---- quick facts, answered locally ---------------------------------
        (R(r"^what(?:'s| is) the time\??$|^what time is it\??$", re.I),
         "get_time", n, None),
        (R(r"^what(?:'s| is) (?:the |today'?s )?date\??$|^what day is it\??$", re.I),
         "get_date", n, None),
        (R(r"^(battery|how much battery)( level| percentage)?\??$", re.I),
         "get_battery", n, None),
        (R(r"^(how much )?(ram|memory|cpu|disk)( usage| free| left)?\??$", re.I),
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
