"""
Opening things — apps, files, folders — the way a person actually asks.

Built from a real transcript of O'ktam using the assistant. What failed:

    "open my CV"          -> open_app("my cv")  -> "I couldn't find an app..."
    "open changes.pdf"    -> found the file, then had NO WAY TO OPEN IT.
                             It opened the containing folder and said
                             "just double-click it" — 30 seconds wasted.
    "open my Igram"       -> open_app("my igram") -> not found
                             (AyuGram is installed; nothing matched it)

Three root causes, all fixed here:

1. There was no "open this file" tool at all. Windows has done this in one
   call since forever (os.startfile uses the file's default program), but
   nothing exposed it, so the model improvised badly.
2. Nothing matched approximately. Speech-to-text mishears, people abbreviate,
   and "Telegram" is AyuGram on this machine. An exact-match-only lookup
   fails on all three.
3. "Open X" was always assumed to mean an APP. Most of the time it means
   a file or folder.

`open_target` resolves in the order a person means it: exact app, then a
path, then a learned nickname, then a fuzzy app match, then a file search.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import CONFIG, ROOT
from .system import IS_WINDOWS, _APP_ALIASES, _title_pattern

# Learned nicknames: "call this one my beats folder" -> path/app. Persisted so
# they survive a restart, because a nickname you have to re-teach is useless.
ALIASES_PATH = ROOT / "data" / "aliases.json"

# difflib ratio below which a fuzzy app match is treated as "not confident".
# Used for every fuzzy pass below: against the known-name pool (aliases +
# family members + index) and, last resort, against the installed index
# alone. That pool holds short, generic tokens ("code", "cmd", "vlc") that
# a looser cutoff matches by accident — verified live at 0.6: "claude" ->
# "code", "discord" -> "vscode", "opera" -> "Computer", "zoom" -> "Zotero",
# each scored 0.60-0.62 and would have confidently opened the WRONG app.
# 0.75 still passes every real typo tested ("wrod"->"word" is the tightest,
# at exactly 0.75) while rejecting all five of those.
FUZZY_MIN = 0.75

# Apps that are the same THING to a user but share no useful spelling.
# "Open Telegram" on this machine must open AyuGram — a Telegram client —
# and no amount of string similarity gets you there ("telegram" vs "ayugram"
# scores below any safe cutoff). This is knowledge, not fuzziness: each entry
# lists interchangeable names, and whichever member is actually installed
# wins. Extend freely; a wrong guess here opens the wrong app, so members
# must genuinely be substitutes for one another.
_APP_FAMILIES: list[set[str]] = [
    {"telegram", "ayugram", "telegram desktop", "unigram", "kotatogram", "64gram"},
    {"chrome", "google chrome", "chromium", "browser"},
    {"vs code", "vscode", "visual studio code", "code"},
    {"explorer", "file explorer", "files", "finder"},
    {"terminal", "windows terminal", "powershell", "cmd", "command prompt"},
    {"photos", "photo viewer", "image viewer"},
    {"music", "media player", "windows media player", "aimp", "vlc"},
]


def _family_candidates(key: str) -> list[str]:
    for family in _APP_FAMILIES:
        if key in family:
            return [member for member in family if member != key]
    return []


# Bare words that must always mean the user's own folder, never an app —
# checked in open_target before any app resolution runs at all. Windows
# ships real apps whose names contain these words ("Remote Desktop
# Connection"), so leaving them to fuzzy/substring matching is a live
# collision, not a hypothetical one.
_SPECIAL_FOLDERS: dict[str, str] = {
    "desktop": "Desktop",
    "documents": "Documents",
    "downloads": "Downloads",
    "pictures": "Pictures",
    "videos": "Videos",
    "music": "Music",
}

# Gaps in the shipped _APP_ALIASES table: apps with no Start Menu .lnk on
# this machine at all (the modern Calculator ships no shortcut, so no
# amount of fuzzy/substring matching against the index can ever find it),
# checked exactly like _APP_ALIASES. Kept local rather than added to
# system.py's table since that file belongs to a different area.
_LOCAL_ALIASES: dict[str, str] = {
    "calc": "calc.exe",
}


def _word_boundary_match(needle: str, haystack: str) -> bool:
    """
    True when `needle` appears in `haystack` as a separate word, not glued
    inside a longer one. "word" matches "word 2016" (space-separated) but
    not "wordpad" (glued straight to "pad") — the difference between
    opening Microsoft Word and opening WordPad for "open word", which a
    plain substring check can't tell apart.
    """
    return re.search(r"(?:^|[\s\-_(])" + re.escape(needle) + r"(?:$|[\s\-_)])", haystack) is not None


def _best_contains(key: str, index: dict[str, str]) -> str | None:
    """
    Best of the index entries where `key` is a substring (or vice versa).
    Word-boundary matches win over ones where the key is merely embedded
    ("word" in "word 2016" beats "word" in "wordpad"); ties, and the
    boundary-less fallback, go to the shortest name — least extra junk.
    """
    contains = [n for n in index if key in n or n in key]
    if not contains:
        return None
    boundary = [n for n in contains if _word_boundary_match(key, n) or _word_boundary_match(n, key)]
    pool = boundary or contains
    return min(pool, key=len)


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance — how many single-character edits apart."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def _typo_matches(key: str, pool) -> list[str]:
    """
    Fuzzy match, but only where the candidate is plausibly a TYPO of what
    was said — not merely a similar-looking different word.

    Plain similarity is length-blind, so it matched "chromosome" -> chrome
    and "telegraph" -> telegram. Both would have silently opened the wrong
    app, which is worse than not matching at all. Similarity alone can't
    separate them: telegraph/telegram scores 0.824, sitting between real
    typos wrod/word (0.750) and telegran/telegram (0.875).

    Edit distance can. Measured on real cases:
        telegran   -> telegram    1 edit    typo
        chrom      -> chrome      1 edit    typo
        exploerer  -> explorer    1 edit    typo
        wrod       -> word        2 edits   typo (short word, transposition)
        telegraph  -> telegram    2 edits   DIFFERENT WORD
        chromosome -> chrome      4 edits   DIFFERENT WORD

    One edit always counts; two only on short words (<=5 chars), where
    transpositions are common and there's less room to be a real word.
    """
    out = []
    for cand in difflib.get_close_matches(key, list(pool), n=5, cutoff=FUZZY_MIN):
        allowed = 2 if max(len(key), len(cand)) <= 5 else 1
        if _edit_distance(key, cand) <= allowed:
            out.append(cand)
            continue
        # An ABBREVIATION is not a typo and fails the edit-distance test:
        # "igram" -> "ayugram" is 3 edits, yet it's exactly how someone
        # shortens a name in speech. It IS safe when the spoken form is a
        # contiguous tail (or head) of the real name and long enough to be
        # distinctive — "igram" ends "ayugram"; "graph" would also end
        # "telegraph", but "telegraph" is never reached here because it is
        # itself a real word that matched nothing. Four characters is the
        # floor: shorter fragments ("cal", "co") match far too much.
        if len(key) >= 4 and (cand.endswith(key) or cand.startswith(key)):
            out.append(cand)
    return out


def _known_name_pool(index: dict[str, str]) -> set[str]:
    """Every name resolution actually understands: installed apps, shipped
    aliases, and family members — the full universe a typo should be
    corrected against, not just whatever happens to be in the Start Menu."""
    names = set(index) | set(_APP_ALIASES) | set(_LOCAL_ALIASES)
    for family in _APP_FAMILIES:
        names |= family
    return names


def _resolve_key(key: str, index: dict[str, str]) -> tuple[str | None, str | None]:
    """Exact alias, exact index, family sibling, then boundary-aware
    substring — the deterministic part of resolution, no fuzziness."""
    builtin = _APP_ALIASES.get(key) or _LOCAL_ALIASES.get(key)
    if builtin:
        if builtin.startswith(("ms-settings:", "http")) or shutil.which(builtin) or Path(builtin).exists():
            return builtin, key

    if key in index:
        return index[key], key

    for sibling in _family_candidates(key):
        if sibling in index:
            return index[sibling], sibling
        sibling_builtin = _APP_ALIASES.get(sibling) or _LOCAL_ALIASES.get(sibling)
        if sibling_builtin and (shutil.which(sibling_builtin) or Path(sibling_builtin).exists()):
            return sibling_builtin, sibling

    best = _best_contains(key, index)
    if best:
        return index[best], best
    return None, None


_app_index_cache: dict[str, str] | None = None
_app_index_built_at = 0.0
APP_INDEX_TTL_S = 300.0

# Bounds for the filename search. A voice assistant that goes quiet for 9
# seconds reads as broken, so the budget is what actually matters here —
# better a fast good-enough answer than a slow exhaustive one.
SEARCH_TIME_BUDGET_S = 2.0
SEARCH_MAX_ENTRIES = 60_000
SEARCH_MAX_DEPTH = 6


# ------------------------------------------------------------------ aliases
def _load_aliases() -> dict[str, str]:
    try:
        return json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_aliases(data: dict[str, str]) -> None:
    ALIASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALIASES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def remember_alias(name: str, target: str) -> str:
    """Teach a nickname: "remember my beats folder is C:/..." — GREEN."""
    name = (name or "").strip().lower()
    target = (target or "").strip()
    if not name or not target:
        return "I need both a name and what it points to."
    aliases = _load_aliases()
    aliases[name] = target
    _save_aliases(aliases)
    return f"Got it — \"{name}\" means {target}."


def list_aliases() -> str:
    aliases = _load_aliases()
    if not aliases:
        return "I haven't been taught any nicknames yet."
    return "\n".join(f"{k} -> {v}" for k, v in sorted(aliases.items()))


# ---------------------------------------------------------------- app index
def _build_app_index() -> dict[str, str]:
    """
    Every app this machine can actually launch, from the Start Menu — the
    same list the Start button shows. Built once and cached: it's ~180
    shortcuts here, and rebuilding it per command would add latency to the
    one path that most needs to be fast.
    """
    index: dict[str, str] = {}
    if not IS_WINDOWS:
        return index
    for base in (
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
    ):
        root = Path(base)
        if not root.is_dir():
            continue
        try:
            for lnk in root.rglob("*.lnk"):
                stem = lnk.stem.lower()
                # Skip uninstallers and docs — nobody says "open uninstall X",
                # and they pollute fuzzy matching with near-identical names.
                if any(bad in stem for bad in ("uninstall", "readme", "help", "license")):
                    continue
                index.setdefault(stem, str(lnk))
        except OSError:
            continue
    return index


def app_index() -> dict[str, str]:
    global _app_index_cache, _app_index_built_at
    if _app_index_cache is None or time.monotonic() - _app_index_built_at > APP_INDEX_TTL_S:
        _app_index_cache = _build_app_index()
        _app_index_built_at = time.monotonic()
    return _app_index_cache


def resolve_app(name: str) -> tuple[str | None, str | None]:
    """
    (target, matched_name). Exact alias, then exact index hit, then substring,
    then fuzzy. Returns (None, None) when nothing is close enough — the caller
    must then say so rather than pretending.
    """
    spoken = (name or "").strip().lower()
    if not spoken:
        return None, None

    # A nickname the user taught wins over everything, and is matched EXACTLY
    # as taught — before any filler-stripping. Otherwise "my beats" gets
    # stripped to "beats" and a nickname the user deliberately chose can
    # never be found again, which defeats the whole point of teaching it.
    learned = _load_aliases()
    if spoken in learned:
        return learned[spoken], spoken

    # "open my telegram", "the vs code app" — filler words are never part of
    # an app's real name and otherwise defeat every lookup below.
    key = re.sub(r"^(my|the|a|an)\s+|\s+(app|application|program)$", "", spoken).strip()
    if not key:
        return None, None
    if key in learned:
        return learned[key], key

    index = app_index()

    # A built-in alias is only usable if that program is actually HERE. The
    # shipped table maps "telegram" -> Telegram.exe, but this machine has
    # AyuGram instead — so the alias resolved to something nonexistent and
    # "open Telegram" failed while the real client sat one fuzzy match away.
    # Verify first; fall through to what's installed when it doesn't hold.
    # Substring both ways: "igram" is inside "ayugram"; "visual studio code"
    # contains the spoken "vs code" only after fuzzy, but "code" is inside it.
    target, matched = _resolve_key(key, index)
    if target:
        return target, matched

    # Nothing exact/substring matched — the spoken name itself might be
    # misspelled ("telegran" for "telegram", "exploerer" for "explorer").
    # Correct it against every name resolution actually understands (Start
    # Menu entries, shipped aliases, family members) rather than only the
    # installed index, so a typo'd ALIAS or FAMILY name gets fixed too —
    # then resolve the CORRECTED name through the real rules above rather
    # than opening the fuzzy match directly, so a bad guess still can't
    # skip alias-verification or the installed-sibling substitution.
    pool = _known_name_pool(index)
    close = _typo_matches(key, pool)
    if close and close[0] != key:
        target, matched = _resolve_key(close[0], index)
        if target:
            return target, matched

    # Last resort: fuzzy straight against the installed index — covers a
    # typo'd app with no alias/family entry at all (a one-off Start Menu
    # program only known by its exact installed name).
    close = _typo_matches(key, index)
    if close:
        return index[close[0]], close[0]

    # FIND THE APP INSIDE THE SENTENCE.
    #
    # Everything above assumes the spoken text IS a name, give or take
    # filler words removed by a hard-coded list. That approach loses: a list
    # only covers phrasings someone predicted. Measured against 14 ordinary
    # phrasings that simply weren't on it — "open telegram when you get a
    # chance", "open chrome if you don't mind", "open telegram buddy",
    # "open the telegram thing" — 11 of 14 failed, because the entire tail
    # became part of the name being searched for.
    #
    # So stop enumerating what ISN'T a name and go looking for what IS one.
    # Every known app name is checked as a WORD SEQUENCE inside what was
    # said; longest match wins, so "telegram web" beats "telegram" when both
    # appear. No word list, so it degrades gracefully on phrasings nobody
    # anticipated — which is the actual requirement.
    #
    # Only reached after every exact/substring/typo path has missed, so it
    # can't override a precise match. Whole words only: "opera" must not
    # match inside "operations", which is how a substring scan quietly opens
    # the wrong app.
    words = re.findall(r"[a-z0-9.+#]+", spoken)
    if len(words) > 1:
        best: tuple[int, str] | None = None
        for candidate in _known_name_pool(index):
            cand_words = re.findall(r"[a-z0-9.+#]+", candidate)
            if not cand_words:
                continue
            span = len(cand_words)
            for i in range(len(words) - span + 1):
                if words[i:i + span] == cand_words:
                    if best is None or span > best[0]:
                        best = (span, candidate)
                    break
        if best is not None:
            target, matched = _resolve_key(best[1], index)
            if target:
                return target, matched
    return None, None


def _canonical_choice_name(path: str) -> str:
    """
    Collapse Windows' own auto-generated copy naming ("X - Shortcut",
    "X - Shortcut (2)") to the same choice, so two shortcuts to the same
    thing don't manufacture a fake ambiguity between identical launchers.
    """
    stem = Path(path).stem.lower()
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    stem = re.sub(r"\s*-\s*shortcut$", "", stem)
    return stem


# ------------------------------------------------------------------- open
def _startfile(target: str) -> None:
    """Open with the file's own default program — the thing that was missing."""
    if IS_WINDOWS:
        os.startfile(target)  # noqa: S606
    else:  # pragma: no cover - dev convenience only
        subprocess.Popen(["xdg-open", target])


def open_in(app: str, target: str) -> str:
    """
    Launch an app already pointed at a file or folder — GREEN.

    "open VS Code in the eco pulse folder", "open Excel with budget.xlsx".
    Before this existed the request had nowhere to go: the greedy open rule
    swallowed the whole phrase and tried to launch an app literally named
    "vs code in the eco pulse folder", and the brain's own fallback was to
    silently drop half the request and answer as if it had done all of it.

    Windows launches an app with a document by passing the path as the first
    argument — the same thing "Open with" does.
    """
    app_target, matched = resolve_app(app)
    if app_target is None:
        return f"I couldn't find an app called {app} on this machine."

    where = Path(os.path.expandvars(os.path.expanduser((target or "").strip().strip('"'))))
    if not where.exists():
        hits = find_files(target)
        if not hits:
            return f"I couldn't find {target}."
        # Prefer a folder when the phrasing said "folder", else the best hit.
        folders = [h for h in hits if Path(h).is_dir()]
        chosen = folders[0] if ("folder" in (target or "").lower() and folders) else hits[0]
        where = Path(chosen)

    launcher_path = app_target
    if launcher_path.endswith(".lnk"):
        # A .lnk can't take arguments directly; resolve it to the real exe.
        resolved = _resolve_lnk_target(launcher_path)
        if resolved:
            launcher_path = resolved

    # A bare name like "code" is a shell wrapper (code.cmd), not an
    # executable Popen can launch directly — it fails with WinError 2.
    # shutil.which resolves it to the real file, extension included.
    if not Path(launcher_path).exists():
        which = shutil.which(launcher_path)
        if which:
            launcher_path = which

    try:
        # .cmd/.bat wrappers (VS Code ships code.cmd) need a shell to run.
        if launcher_path.lower().endswith((".cmd", ".bat")):
            subprocess.Popen(f'"{launcher_path}" "{where}"', shell=True, close_fds=True)
        else:
            subprocess.Popen([launcher_path, str(where)], shell=False, close_fds=True)
    except Exception as exc:
        return f"Couldn't open {matched or app} at {where.name}: {exc}"

    if _appeared(app, matched, launcher_path):
        return f"Opening {matched or app} at {where.name}."
    return f"I started {matched or app} but nothing came up."


def _resolve_lnk_target(lnk_path: str) -> str | None:
    """The .exe a Start Menu shortcut points at, so it can take arguments."""
    try:
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
        target = shell.CreateShortcut(lnk_path).TargetPath
        return target if target and Path(target).exists() else None
    except Exception:
        return None


def open_target(name: str) -> str:
    """
    Open whatever the user meant: an app, a file, or a folder — AMBER.

    Resolution order matches how people actually speak. An explicit path wins
    (it's unambiguous), then a known/learned/fuzzy app name, then a search of
    the usual places for a matching file or folder.
    """
    raw = (name or "").strip().strip('"')
    if not raw:
        return "Open what?"

    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    if expanded.exists():
        try:
            _startfile(str(expanded))
            what = "folder" if expanded.is_dir() else "file"
            return f"Opened {what} {expanded.name}."
        except Exception as exc:
            return f"Couldn't open {expanded.name}: {exc}"

    # A bare special-folder name always means the folder, checked BEFORE app
    # resolution. The router's own dedicated rule already handles the common
    # phrasings ("open my desktop"), but anything that reaches open_target
    # by a phrasing that rule doesn't cover ("pull up my desktop") used to
    # fall into resolve_app's fuzzy matching and confidently open "Remote
    # Desktop Connection" instead — verified live. "desktop"/"documents" are
    # real words that collide with real installed app names; they must
    # never be left to fuzzy matching.
    folder_key = re.sub(r"^(my|the|a|an)\s+|\s+folder$", "", raw.strip().lower()).strip()
    special_folder = _SPECIAL_FOLDERS.get(folder_key)
    if special_folder:
        target_dir = Path.home() / special_folder
        try:
            _startfile(str(target_dir))
            return f"Opened {special_folder}."
        except Exception as exc:
            return f"Couldn't open {special_folder}: {exc}"

    target, matched = resolve_app(raw)
    if target:
        try:
            if target.endswith(".lnk") or target.startswith(("ms-settings:", "http")):
                _startfile(target)
            else:
                subprocess.Popen([target], shell=False, close_fds=True)
        except Exception as exc:
            return f"Couldn't open {raw}: {exc}"
        if _appeared(raw, matched, target):
            # Say which app was actually chosen when it wasn't a literal match,
            # so a wrong fuzzy guess is obvious immediately instead of silently
            # opening the wrong thing.
            if matched and matched != raw.lower():
                return f"Opening {matched}."
            return f"Opening {raw}."
        return f"I started {matched or raw} but nothing came up."

    # An explicit "folder"/"directory" narrows the search to directories, so
    # "open the SAT TOP folder" isn't buried under unrelated PDFs and specs
    # that happen to contain the same two words.
    wants_dir = re.search(r"\bfolder\b|\bdirectory\b", raw, re.I) is not None
    hits = find_files(raw, dirs_only=wants_dir)
    if wants_dir and not hits:
        hits = find_files(raw)  # nothing matched as a folder — don't over-restrict
    if not hits:
        return f"I couldn't find an app, file or folder called {raw}."

    # A real Desktop shortcut named exactly what was asked for outranks any
    # number of unrelated files that merely happen to contain the same
    # word(s) — verified live: "open claude" matched two "Claude - Shortcut"
    # copies AND several unrelated project folders named "claude-api"; the
    # shortcut is unambiguously what "open" means here, so it wins outright
    # instead of joining a "which one?" list with things that were never
    # really candidates.
    query_terms = " ".join(
        t for t in raw.lower().replace("_", " ").replace("-", " ").split()
        if t not in {"my", "the", "a", "an", "file", "folder", "please", "open"}
    )
    shortcut_hits = [h for h in hits if h.lower().endswith(".lnk") and _canonical_choice_name(h) == query_terms]
    if shortcut_hits:
        hits = shortcut_hits

    # Only ask when the choice is genuinely ambiguous. Asking "which one?"
    # about two files that share a NAME (the same document in Desktop and
    # Downloads) is noise — either satisfies the request. Likewise, naming a
    # file outright ("changes.pdf") is already unambiguous. Ask only when the
    # candidates are actually different things.
    #
    # "different things" also excludes Windows' own shortcut-copy naming:
    # "Slack - Shortcut.lnk" and a second copy both mean the same app, so
    # they collapse to one canonical choice rather than manufacturing a
    # fake "which one?" between two names for the identical launcher.
    distinct = {_canonical_choice_name(h) for h in hits}
    asked_for = Path(raw).name.lower()
    exact = [h for h in hits if Path(h).name.lower() == asked_for]

    chosen: str | None = None
    if exact:
        chosen = exact[0]
    elif len(distinct) == 1:
        # Among duplicate names for the same thing, a real .lnk shortcut is
        # what "open" should launch — not an install package or archive
        # that happens to share the name (verified live: "slack" matched
        # both "Slack - Shortcut.lnk" and a downloaded "Slack.msix"; the
        # shorter filename won the old length-only sort and opened the
        # installer package instead of the app).
        lnk_hits = [h for h in hits if h.lower().endswith(".lnk")]
        chosen = lnk_hits[0] if lnk_hits else hits[0]

    if chosen:
        try:
            _startfile(chosen)
            extra = f" (found {len(hits)}, opened the one in {Path(chosen).parent.name})" if len(hits) > 1 else ""
            return f"Opened {Path(chosen).name}.{extra}"
        except Exception as exc:
            return f"Couldn't open {Path(chosen).name}: {exc}"

    listing = "; ".join(Path(h).name for h in hits[:5])
    return f"I found {len(hits)}: {listing}. Which one?"


def _appeared(spoken: str, matched: str | None, target: str, timeout: float = 6.0) -> bool:
    stem = Path(target).stem.lower()
    names = {n for n in (spoken.lower(), (matched or "").lower(), stem) if n}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            import psutil

            for proc in psutil.process_iter(["name"]):
                pname = (proc.info.get("name") or "").lower()
                if any(n and (pname.startswith(n[:6]) or n in pname) for n in names):
                    return True
        except Exception:
            pass
        try:
            import uiautomation as auto

            for n in names:
                if auto.WindowControl(searchDepth=1, RegexName=_title_pattern(n)).Exists(0.3, 0.15):
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


# ------------------------------------------------------------------ search
def _search_roots() -> list[Path]:
    configured = CONFIG.get_path("index.content_index_paths", []) or []
    roots = [Path(os.path.expandvars(os.path.expanduser(p))) for p in configured]
    home = Path.home()
    for extra in ("Desktop", "Documents", "Downloads"):
        candidate = home / extra
        if candidate not in roots:
            roots.append(candidate)
    return [r for r in roots if r.is_dir()]


def find_files(query: str, limit: int = 12, dirs_only: bool = False) -> list[str]:
    """
    Filename search over the usual places, best matches first.

    `dirs_only` narrows results to directories — open_target sets it when the
    request explicitly said "folder"/"directory", so "open the SAT TOP
    folder" isn't drowned out by unrelated PDFs that happen to share the
    same words.
    """
    query = (query or "").strip().lower()
    if not query:
        return []
    # "my cv" / "the changes.pdf file" — drop words that are never part of a
    # filename, so the search terms are what the user actually named.
    stop = {"my", "the", "a", "an", "file", "folder", "please", "open"}
    terms = [t for t in query.replace("_", " ").replace("-", " ").split() if t not in stop]
    if not terms:
        terms = [query]

    exclude = {d.lower() for d in (CONFIG.get_path("index.exclude_dirs", []) or [])}
    # (depth, name length, path): depth FIRST. A project like "Claude
    # skills" clones the same short folder name ("claude-api") once per
    # language subfolder — under pure length-sorting those buried
    # duplicates crowded out the actual Desktop shortcut the user meant.
    # Ranking shallower (closer-to-root) hits first fixes that without
    # loosening what counts as a match at all.
    scored: list[tuple[int, int, str]] = []
    scanned = 0
    deadline = time.monotonic() + SEARCH_TIME_BUDGET_S
    out_of_time = False
    for root in _search_roots():
        if out_of_time:
            break
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in exclude and not d.startswith(".")]
            depth = len(Path(dirpath).relative_to(root).parts)
            # Don't descend forever. Nothing a person asks for by name lives
            # 6 levels inside Desktop; deep trees here are dependency folders
            # (node_modules, .venv, site-packages) that only add scan time.
            if depth >= SEARCH_MAX_DEPTH:
                dirnames[:] = []
            names = dirnames if dirs_only else filenames + dirnames
            for entry in names:
                scanned += 1
                low = entry.lower()
                if all(t in low for t in terms):
                    scored.append((depth, len(entry), str(Path(dirpath) / entry)))
            # A WALL-CLOCK budget, not just an entry cap. Measured on this
            # machine: "open my cv" took 8.96s — the entry cap alone doesn't
            # bound anything when the disk is slow (this one is 99% full),
            # and 9 seconds of silence is exactly what "it's not responding"
            # feels like. Checked per directory, not per file, so the check
            # itself costs nothing.
            if scanned > SEARCH_MAX_ENTRIES or time.monotonic() > deadline:
                out_of_time = True
                break
        if out_of_time:
            break
    scored.sort()
    return [path for _, _, path in scored[:limit]]


def search_files(query: str, root: str | None = None) -> str:
    """Find files/folders by name — GREEN."""
    hits = find_files(query)
    if not hits:
        return f"No files matching {query!r} found."
    return "\n".join(hits)


REGISTRY: dict[str, Any] = {
    "open_target": open_target,
    "open_in": open_in,
    "remember_alias": remember_alias,
    "list_aliases": list_aliases,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
