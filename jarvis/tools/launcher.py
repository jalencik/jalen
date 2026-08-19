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
# 0.6 accepts "igram"->"ayugram" and "vs code"->"Visual Studio Code" while
# still rejecting unrelated words.
FUZZY_MIN = 0.6

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


_app_index_cache: dict[str, str] | None = None
_app_index_built_at = 0.0
APP_INDEX_TTL_S = 300.0


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
    builtin = _APP_ALIASES.get(key)
    if builtin:
        if builtin.startswith(("ms-settings:", "http")) or shutil.which(builtin) or Path(builtin).exists():
            return builtin, key

    if key in index:
        return index[key], key

    # A same-thing-different-name sibling that IS installed (telegram -> ayugram).
    for sibling in _family_candidates(key):
        if sibling in index:
            return index[sibling], sibling
        sibling_builtin = _APP_ALIASES.get(sibling)
        if sibling_builtin and (shutil.which(sibling_builtin) or Path(sibling_builtin).exists()):
            return sibling_builtin, sibling

    # Substring both ways: "igram" is inside "ayugram"; "visual studio code"
    # contains the spoken "vs code" only after fuzzy, but "code" is inside it.
    contains = [n for n in index if key in n or n in key]
    if contains:
        best = min(contains, key=len)  # shortest = least extra junk
        return index[best], best

    close = difflib.get_close_matches(key, list(index), n=1, cutoff=FUZZY_MIN)
    if close:
        return index[close[0]], close[0]
    return None, None


# ------------------------------------------------------------------- open
def _startfile(target: str) -> None:
    """Open with the file's own default program — the thing that was missing."""
    if IS_WINDOWS:
        os.startfile(target)  # noqa: S606
    else:  # pragma: no cover - dev convenience only
        subprocess.Popen(["xdg-open", target])


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

    hits = find_files(raw)
    if not hits:
        return f"I couldn't find an app, file or folder called {raw}."

    # Only ask when the choice is genuinely ambiguous. Asking "which one?"
    # about two files that share a NAME (the same document in Desktop and
    # Downloads) is noise — either satisfies the request. Likewise, naming a
    # file outright ("changes.pdf") is already unambiguous. Ask only when the
    # candidates are actually different things.
    distinct = {Path(h).name.lower() for h in hits}
    asked_for = Path(raw).name.lower()
    exact = [h for h in hits if Path(h).name.lower() == asked_for]

    chosen: str | None = None
    if exact:
        chosen = exact[0]
    elif len(distinct) == 1:
        chosen = hits[0]

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


def find_files(query: str, limit: int = 12) -> list[str]:
    """Filename search over the usual places, best matches first."""
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
    scored: list[tuple[int, str]] = []
    scanned = 0
    for root in _search_roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in exclude and not d.startswith(".")]
            for entry in filenames + dirnames:
                scanned += 1
                if scanned > 60_000:
                    break
                low = entry.lower()
                if all(t in low for t in terms):
                    # Prefer shorter names: "CV.pdf" over "CV_old_draft_v3.pdf".
                    scored.append((len(entry), str(Path(dirpath) / entry)))
            if scanned > 60_000:
                break
    scored.sort()
    return [path for _, path in scored[:limit]]


def search_files(query: str, root: str | None = None) -> str:
    """Find files/folders by name — GREEN."""
    hits = find_files(query)
    if not hits:
        return f"No files matching {query!r} found."
    return "\n".join(hits)


REGISTRY: dict[str, Any] = {
    "open_target": open_target,
    "remember_alias": remember_alias,
    "list_aliases": list_aliases,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
