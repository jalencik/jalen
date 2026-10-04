"""
Local system actions — the things the router calls without ever touching an LLM.

Windows-only by design (pywin32 + ctypes). Everything degrades to a readable
error message rather than a traceback, because these get spoken out loud.

Media control uses Windows media keys rather than the Spotify Web API: as of
February 2026 Spotify requires a Premium subscription for developer access, and
media keys work with Spotify Free, YouTube, AIMP, VLC and anything else that
registers for them. Play/pause/skip/volume only — which is exactly what the
spec asked for.
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

IS_WINDOWS = platform.system() == "Windows"


class DidNotWork(str):
    """
    A spoken sentence that says the action did NOT happen.

    It is a plain str in every way - tools return spoken sentences, errors are
    return values, and this is still one - with a flag the caller can read.
    The router path logs a tool as "executed" whenever the call returned
    without raising, so a close_app that answered "I can't find a window
    called the calculator" was filed as an executed close. app.handle_local
    reads `.failed` and files it as failed instead.
    """

    failed = True

# Virtual key codes
VK = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "vol_up": 0xAF,
    "vol_down": 0xAE,
    "vol_mute": 0xAD,
}
KEYEVENTF_KEYUP = 0x0002


def _tap(vk: int) -> None:
    if not IS_WINDOWS:
        raise RuntimeError("Windows only")
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


# ----------------------------------------------------------------------- media
def media_play_pause() -> str:
    _tap(VK["play_pause"])
    return "Done."


def media_next() -> str:
    _tap(VK["next"])
    return "Skipped."


def media_previous() -> str:
    _tap(VK["previous"])
    return "Back one."


def volume_step(direction: str = "up", steps: int = 4) -> str:
    key = VK["vol_up"] if direction == "up" else VK["vol_down"]
    for _ in range(steps):
        _tap(key)
    return f"Volume {direction}."


def volume_mute_toggle(mute: bool = True) -> str:
    _tap(VK["vol_mute"])
    return "Muted." if mute else "Unmuted."


def volume_set(level: int) -> str:
    """Absolute volume needs the audio endpoint API; pycaw is the light way."""
    level = max(0, min(100, int(level)))
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"Volume {level}."
    except Exception:
        # Fall back to stepping — imprecise but always works
        for _ in range(50):
            _tap(VK["vol_down"])
        for _ in range(round(level / 2)):
            _tap(VK["vol_up"])
        return f"Roughly {level}."


# ------------------------------------------------------------------------ apps
_APP_ALIASES = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "browser": "chrome.exe",
    "telegram": "Telegram.exe",
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "code": "code",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "spotify": "spotify.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "settings": "ms-settings:",
    "task manager": "taskmgr.exe",
    "aimp": "AIMP.exe",
    "zoom": "Zoom.exe",
    "photoshop": "photoshop.exe",
}


def _title_pattern(name: str) -> "re.Pattern":
    """
    Case-insensitive window-title matcher.

    This mattered more than it looks. The router lowercases every utterance
    (_normalise does .lower()), so "close Chrome" reaches these tools as
    "chrome" — and a plain f".*{name}.*" is CASE-SENSITIVE, so it never
    matched the real window titled "… - Google Chrome". Verified live:
    RegexName=".*notepad.*" -> not found, ".*Notepad.*" -> found. Every
    close/focus/window command was silently failing this way, which is a
    large part of "basic commands don't work". uiautomation accepts a
    compiled pattern, so re.I fixes it; re.escape stops an app name with
    regex characters from being interpreted as a pattern.
    """
    return re.compile(f".*{re.escape(name)}.*", re.I)


def _resolve_executable(name: str) -> str | None:
    """
    Turn a spoken app name into something Windows can actually launch,
    deterministically — never by asking an LLM to guess an .exe name.

    Order: explicit aliases -> PATH -> the App Paths registry (how the Run
    dialog resolves names) -> Start Menu shortcuts. Returns None if nothing
    matched, so the caller can say so honestly instead of firing a
    subprocess at a name that doesn't exist and reporting success anyway.
    """
    key = (name or "").strip().lower()
    if not key:
        return None

    alias = _APP_ALIASES.get(key)
    if alias:
        if alias.startswith("ms-settings:") or alias.startswith("http"):
            return alias
        if shutil.which(alias):
            return alias

    for candidate in (key, f"{key}.exe", key.replace(" ", "")):
        found = shutil.which(candidate)
        if found:
            return found

    if IS_WINDOWS:
        try:
            import winreg

            exe = key if key.endswith(".exe") else f"{key}.exe"
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
                    with winreg.OpenKey(root, path) as hkey:
                        value, _ = winreg.QueryValueEx(hkey, "")
                        if value and Path(value).exists():
                            return value
                except OSError:
                    continue
        except ImportError:
            pass

        for base in (
            os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        ):
            root = Path(base)
            if not root.is_dir():
                continue
            try:
                for lnk in root.rglob("*.lnk"):
                    if lnk.stem.lower() == key or key in lnk.stem.lower():
                        return str(lnk)
            except OSError:
                continue
    return alias if alias else None


def open_app(name: str) -> str:
    """
    Launch an app and CONFIRM it launched.

    The old version called Popen on the raw spoken name and unconditionally
    replied "Opening X" as long as Popen itself didn't raise — which it
    doesn't for a nonexistent program, since `cmd /c start` succeeds and the
    failure surfaces in a window the user never sees. So Jalen cheerfully
    claimed success while nothing opened. Now: resolve deterministically,
    then verify a process or window actually appeared before saying so.
    """
    target = _resolve_executable(name)
    if target is None:
        return f"I couldn't find an app called {name} on this machine."

    try:
        if target.startswith("ms-settings:") or target.startswith("http") or target.endswith(".lnk"):
            os.startfile(target)  # noqa: S606
        else:
            subprocess.Popen([target], shell=False, close_fds=True)
    except Exception as exc:
        return f"Couldn't open {name}: {exc}"

    if _wait_for_app(name, target):
        return f"Opening {name}."
    return f"I tried to open {name} but nothing came up."


def _wait_for_app(spoken: str, target: str, timeout: float = 6.0) -> bool:
    """True once a matching process or visible window shows up."""
    import time as _time

    stem = Path(target).stem.lower()
    spoken_low = (spoken or "").strip().lower()
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        try:
            import psutil

            for proc in psutil.process_iter(["name"]):
                pname = (proc.info.get("name") or "").lower()
                if pname.startswith(stem) or (spoken_low and spoken_low in pname):
                    return True
        except Exception:
            pass
        try:
            import uiautomation as auto

            if auto.WindowControl(searchDepth=1, RegexName=_title_pattern(spoken)).Exists(0.4, 0.2):
                return True
        except Exception:
            pass
        _time.sleep(0.25)
    return False


_LEADING_ARTICLE = re.compile(r"^(?:the|my|an?)\s+(?=\S)", re.I)


def _wait_until_gone(hwnd: int, timeout_s: float = 2.5) -> bool:
    """
    Is this window gone? True once it is destroyed OR no longer visible - an
    app that closes to the tray (Telegram, Spotify) keeps its window alive
    and hidden, and to him that window is closed.

    The handle is the window's own, not a fresh title search: with six Chrome
    windows open, closing one leaves five that still match "chrome".

    2.5 seconds: NOT MEASURED. A window with nothing to save answers Alt+F4
    within a frame or two; the budget is for a slow app tearing down, and it
    ends the wait early the moment the window is gone.
    """
    if not IS_WINDOWS or not hwnd:
        return False
    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout_s
    while True:
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def close_app(name: str) -> str:
    """
    Close a running app's window by (partial) title — AMBER; closing can
    lose unsaved work (a real save-prompt can appear and just sit there),
    which is exactly why this isn't GREEN. Referenced by the router since
    Phase 0/1 but never actually implemented until now — a real gap, found
    while building Phase C's tool inventory.

    Says "Closed" only once the window is gone. Alt+F4 is a request: a window
    with unsaved work answers it with a save prompt and stays. A result that
    means "this did not happen" is a DidNotWork, so the router path files it
    as failed instead of executed.
    """
    # "close the calculator" reaches here as "the calculator"; the title is
    # "Calculator". Dropped unless nothing would be left ("close the").
    target = _LEADING_ARTICLE.sub("", (name or "").strip())
    target = target or (name or "").strip()
    try:
        import uiautomation as auto

        window = auto.WindowControl(searchDepth=1, RegexName=_title_pattern(target))
        if not window.Exists(2, 0.3):
            return DidNotWork(f"I can't find a window called {target}.")
        # The handle is read BEFORE the keys are sent: a closed window has
        # none to give. It is also what makes the check below mean THIS
        # window - a second Notepad with the same title is not this one.
        handle = int(getattr(window, "NativeWindowHandle", 0) or 0)
        window.SendKeys("{Alt}{F4}")
        # "Closed calculator." used to be said the instant the keys were sent
        # (live QA, 2026-10-01), and the audit log recorded it as done. A
        # window that asks "save changes?" is still there. Look, and say so.
        # No handle, nothing to look at: say what was done and no more.
        if handle and _window_still_open(handle):
            return DidNotWork(
                f"I asked {target} to close, but its window is still open. "
                "It may be waiting for you to save something."
            )
        return f"Closed {target}."
    except ImportError:
        return "Window control isn't installed yet."
    except Exception as exc:
        return DidNotWork(f"Couldn't close {target}: {exc}")


def _window_still_open(handle: int, wait_s: float = 1.5) -> bool:
    """
    True if this window is still on screen after up to `wait_s` seconds.

    The inverse of _wait_until_gone, with the shorter budget close_app uses:
    polls every 100 ms and returns the moment the window is gone, so an
    ordinary close costs one poll, not the whole wait. Hidden counts as gone:
    Telegram and Spotify "close" to the tray, the window still exists, and
    nobody means that by "still open". 1.5 s is NOT MEASURED against a slow
    app; it is longer than a normal window takes to go and shorter than a
    pause he would notice.
    """
    return not _wait_until_gone(handle, timeout_s=wait_s)


def open_folder(path: str) -> str:
    try:
        os.startfile(os.path.expandvars(os.path.expanduser(path)))  # noqa: S606
        return "Opened."
    except Exception as exc:
        return f"Couldn't open that folder: {exc}"


def open_url(url: str) -> str:
    """
    Open a URL in the default browser — AMBER (already tiered in
    config/safety.yaml). The router referenced this tool name for "search
    the web for X" / "google X" with no implementation behind it at all —
    this is that missing half, not a new capability being invented.
    """
    raw = (url or "").strip()
    if not raw:
        return "Open what URL?"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", raw):
        raw = "https://" + raw
    try:
        os.startfile(raw)  # noqa: S606
        return f"Opening {_spoken_site(raw)}."
    except Exception as exc:
        return f"Couldn't open that URL: {exc}"


def _spoken_site(address: str) -> str:
    """
    Where an address goes, in a form worth saying aloud: the site, never the
    address. This reply is read out in voice mode, and "Opening https://www.
    google.com/search?q=state+space+models+versus+transformers+for+long+
    sequences." was read out in full (live QA, 2026-10-01). A query string
    can also carry words he would rather not have said to the room, and a
    link can carry a login (user:password@host), so only the host survives.
    """
    from urllib.parse import urlparse

    try:
        host = (urlparse(address).hostname or "").lower()
    except ValueError:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host or "that page"


def lock_workstation() -> str:
    ctypes.windll.user32.LockWorkStation()
    return "Locked."


def sign_out() -> str:
    """
    Log the current Windows user out — RED. This closes every running app;
    Windows still prompts individual apps that have unsaved changes before
    the session actually ends, but that prompt is easy to miss, which is
    exactly why this tier stops and asks first rather than skipping straight
    to execution just because the router matched the phrase quickly.
    """
    if not IS_WINDOWS:
        return "Signing out is Windows-only."
    EWX_LOGOFF = 0x00000000
    try:
        ok = ctypes.windll.user32.ExitWindowsEx(EWX_LOGOFF, 0)
    except Exception as exc:
        return f"Couldn't sign out: {exc}"
    if not ok:
        return f"Couldn't sign out (error {ctypes.GetLastError()})."
    return "Signing out."


def empty_recycle_bin() -> str:
    """
    Permanently empty the Recycle Bin — RED (already tiered in
    config/safety.yaml). Unlike delete_file, there's no second undo after
    this — it's the step that makes a deleted file actually gone.
    SHEmptyRecycleBinW is the documented Windows shell API for it, called
    directly via ctypes; no extra dependency needed.
    """
    if not IS_WINDOWS:
        return "Emptying the Recycle Bin is Windows-only."
    SHERB_NOCONFIRMATION = 0x00000001
    SHERB_NOPROGRESSUI = 0x00000002
    SHERB_NOSOUND = 0x00000004
    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    try:
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    except Exception as exc:
        return f"Couldn't empty the Recycle Bin: {exc}"
    # S_OK (0) on success. The documented quirk: a Recycle Bin that's
    # already empty returns a non-zero HRESULT (E_UNEXPECTED, 0x8000FFFF —
    # -2147418113 as the signed value ctypes hands back) instead of S_OK.
    # That's not a real failure, so it's reported as success too.
    if result in (0, -2147418113):
        return "Recycle Bin emptied."
    return f"Couldn't empty the Recycle Bin (error {result})."


def focus_window(name: str) -> str:
    try:
        import uiautomation as auto

        window = auto.WindowControl(searchDepth=1, RegexName=_title_pattern(name))
        if not window.Exists(2, 0.3):
            return f"I can't find a window called {name}."
        window.SetActive()
        window.SetTopmost(False)
        return f"Switched to {name}."
    except ImportError:
        return "Window control isn't installed yet."
    except Exception as exc:
        return f"Couldn't switch: {exc}"


def window_state(state: str) -> str:
    try:
        import uiautomation as auto

        window = auto.GetForegroundControl().GetTopLevelControl()
        if state.startswith("min"):
            window.Minimize()
            return "Minimised."
        window.Maximize()
        return "Maximised."
    except Exception as exc:
        return f"Couldn't do that: {exc}"


# ----------------------------------------------------------------------- facts
def get_time() -> str:
    return datetime.now().strftime("It's %-I:%M %p." if not IS_WINDOWS else "It's %#I:%M %p.")


def get_date() -> str:
    fmt = "%A, %B %#d." if IS_WINDOWS else "%A, %B %-d."
    return datetime.now().strftime("It's " + fmt)


# ---------------------------------------------------- time zones and the clock
# Three seams (_utc_now, _local_now, _local_at) so the tests pin the clock and
# the machine's own zone without touching the real ones.
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local_now() -> datetime:
    """Now, in the zone this machine is set to (aware; the OS knows its own
    daylight saving, so this is right without any database)."""
    return _utc_now().astimezone()


def _local_at(timestamp: float) -> datetime:
    """A past instant in this machine's zone AS IT WAS THEN (the C library
    applies the daylight saving of that date, which a fixed offset would not)."""
    return datetime.fromtimestamp(timestamp).astimezone()


def _local_dst_in_effect() -> bool:
    try:
        return time.localtime().tm_isdst > 0
    except (OSError, ValueError):
        return False


def _clock(moment: datetime) -> str:
    """'3:05 PM'. Built by hand: strftime's no-leading-zero flag is %#I on
    Windows and %-I everywhere else."""
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def _spoken_offset(minutes: int) -> str:
    """'UTC plus 5', 'UTC minus 4', 'UTC plus 5:30', 'UTC'."""
    if not minutes:
        return "UTC"
    hours, rest = divmod(abs(int(minutes)), 60)
    amount = f"{hours}:{rest:02d}" if rest else str(hours)
    return f"UTC {'plus' if minutes > 0 else 'minus'} {amount}"


class _Place(NamedTuple):
    label: str        # how it is said aloud
    iana: str         # the database name, used when a time zone database exists
    std_minutes: int  # offset from UTC in winter (or all year, for no daylight saving)
    rule: str         # "" (never changes), "US", "EU", "AU" or "NZ"


# WHY A TABLE. Windows ships no time zone database; Python's zoneinfo reads
# the `tzdata` package instead, and tzdata is not installed in this project's
# venv (zoneinfo.available_timezones() is empty, ZoneInfo("America/New_York")
# raises ZoneInfoNotFoundError) and not in requirements.txt. Without a table,
# "what time is it in New York" cannot be answered here at all. With
# `pip install tzdata` the table is still used for the NAME lookup but the
# database decides the offset (see time_in).
#
# ONLY ZONES WHOSE RULE IS KNOWN FOR CERTAIN. Left out on purpose, because
# their rules have changed or are set year by year: Cairo (daylight saving
# returned in 2023), Casablanca (suspended every Ramadan), Santiago, Tehran,
# Beirut, Amman, Asuncion, Adelaide. Asking for one of them gets an honest
# "I don't know", never a guess. Mexico (no daylight saving since 2022) and
# Brazil (none since 2019) are listed as fixed.
#
# MEASURED 2026-10-01: every zone in the table (77 database zones) was compared
# with Windows' own zone database through .NET's TimeZoneInfo, once an hour from
# 2025-01-01 to 2027-12-31 UTC: 2,023,560 instants, 0 disagreements. The first
# run disagreed on Almaty for all 26,280 hours, because the Windows id first
# tried for it still carried Kazakhstan's old UTC+6; Almaty has been UTC+5 since
# 2024-03-01, which the table has and the corrected id ("Qyzylorda Standard
# Time") confirms. The rules cover dates from 2007 (US), 1996 (EU), 2008 (AU)
# and 2007 (NZ); an earlier date is not answered correctly, and this tool only
# ever asks about now. NOT MEASURED: any year after 2027 - a government can move
# a rule, which is why the table is for the places named, not a database.
_CITIES: dict[str, _Place] = {}


def _add_places(rule: str, std_hours: float, iana: str, *cities: str) -> None:
    """One database zone, several city names that share it. Each name is
    its own label, so "what time is it in Samarkand" answers "in Samarkand"."""
    for city in cities:
        _CITIES[city] = _Place(city.upper() if iana == "UTC" else city.title(),
                               iana, int(round(std_hours * 60)), rule)


def _table() -> None:
    # (rule, winter offset, database zone, cities)
    us = "US"
    _add_places(us, -5, "America/New_York", "new york", "washington", "boston", "miami",
                "atlanta", "toronto", "detroit", "philadelphia")
    _add_places(us, -6, "America/Chicago", "chicago", "dallas", "houston", "austin", "minneapolis")
    _add_places(us, -7, "America/Denver", "denver", "salt lake city")
    _add_places(us, -8, "America/Los_Angeles", "los angeles", "san francisco", "seattle",
                "san diego", "las vegas", "vancouver", "portland")
    _add_places(us, -9, "America/Anchorage", "anchorage")
    _add_places("", -7, "America/Phoenix", "phoenix")
    _add_places("", -10, "Pacific/Honolulu", "honolulu")
    _add_places("", -6, "America/Mexico_City", "mexico city")
    _add_places("", -5, "America/Bogota", "bogota", "lima")
    _add_places("", -4, "America/Caracas", "caracas")
    _add_places("", -3, "America/Sao_Paulo", "sao paulo", "rio de janeiro", "rio")
    _add_places("", -3, "America/Argentina/Buenos_Aires", "buenos aires")
    eu = "EU"
    _add_places(eu, 0, "Europe/London", "london", "edinburgh", "manchester")
    _add_places(eu, 0, "Europe/Dublin", "dublin")
    _add_places(eu, 0, "Europe/Lisbon", "lisbon")
    _add_places(eu, 1, "Europe/Paris", "paris", "marseille")
    _add_places(eu, 1, "Europe/Berlin", "berlin", "munich", "frankfurt", "hamburg")
    _add_places(eu, 1, "Europe/Madrid", "madrid", "barcelona")
    _add_places(eu, 1, "Europe/Rome", "rome", "milan")
    for city, zone in (("amsterdam", "Europe/Amsterdam"), ("brussels", "Europe/Brussels"),
                       ("vienna", "Europe/Vienna"), ("zurich", "Europe/Zurich"),
                       ("stockholm", "Europe/Stockholm"), ("oslo", "Europe/Oslo"),
                       ("copenhagen", "Europe/Copenhagen"), ("warsaw", "Europe/Warsaw"),
                       ("prague", "Europe/Prague"), ("budapest", "Europe/Budapest")):
        _add_places(eu, 1, zone, city)
    for city, zone in (("athens", "Europe/Athens"), ("helsinki", "Europe/Helsinki"),
                       ("kyiv", "Europe/Kyiv"), ("bucharest", "Europe/Bucharest"),
                       ("sofia", "Europe/Sofia"), ("riga", "Europe/Riga"),
                       ("vilnius", "Europe/Vilnius"), ("tallinn", "Europe/Tallinn")):
        _add_places(eu, 2, zone, city)
    _add_places("", 2, "Africa/Johannesburg", "johannesburg", "cape town")
    _add_places("", 1, "Africa/Lagos", "lagos")
    _add_places("", 0, "Africa/Accra", "accra")
    _add_places("", 3, "Europe/Moscow", "moscow", "saint petersburg", "st petersburg")
    _add_places("", 3, "Europe/Istanbul", "istanbul", "ankara")
    _add_places("", 3, "Europe/Minsk", "minsk")
    _add_places("", 3, "Asia/Riyadh", "riyadh", "jeddah")
    _add_places("", 3, "Asia/Baghdad", "baghdad")
    _add_places("", 3, "Asia/Qatar", "doha")
    _add_places("", 3, "Africa/Nairobi", "nairobi")
    _add_places("", 3, "Africa/Addis_Ababa", "addis ababa")
    _add_places("", 4, "Asia/Dubai", "dubai", "abu dhabi")
    _add_places("", 4, "Asia/Baku", "baku")
    _add_places("", 4, "Asia/Tbilisi", "tbilisi")
    _add_places("", 4, "Asia/Yerevan", "yerevan")
    _add_places("", 4.5, "Asia/Kabul", "kabul")
    _add_places("", 5, "Asia/Tashkent", "tashkent", "samarkand", "bukhara", "namangan", "andijan")
    _add_places("", 5, "Asia/Almaty", "almaty", "astana")
    _add_places("", 5, "Asia/Karachi", "karachi", "lahore", "islamabad")
    _add_places("", 5, "Asia/Yekaterinburg", "yekaterinburg")
    _add_places("", 5.5, "Asia/Kolkata", "delhi", "new delhi", "mumbai", "kolkata",
                "bangalore", "chennai", "hyderabad")
    _add_places("", 5.75, "Asia/Kathmandu", "kathmandu")
    _add_places("", 6, "Asia/Dhaka", "dhaka")
    _add_places("", 6, "Asia/Bishkek", "bishkek")
    _add_places("", 7, "Asia/Bangkok", "bangkok")
    _add_places("", 7, "Asia/Jakarta", "jakarta")
    _add_places("", 7, "Asia/Ho_Chi_Minh", "ho chi minh city", "saigon", "hanoi")
    _add_places("", 8, "Asia/Singapore", "singapore")
    _add_places("", 8, "Asia/Kuala_Lumpur", "kuala lumpur")
    _add_places("", 8, "Asia/Hong_Kong", "hong kong")
    _add_places("", 8, "Asia/Shanghai", "beijing", "shanghai", "shenzhen")
    _add_places("", 8, "Asia/Taipei", "taipei")
    _add_places("", 8, "Asia/Manila", "manila")
    _add_places("", 8, "Australia/Perth", "perth")
    _add_places("", 9, "Asia/Seoul", "seoul")
    _add_places("", 9, "Asia/Tokyo", "tokyo", "osaka", "kyoto")
    _add_places("", 10, "Australia/Brisbane", "brisbane")
    _add_places("AU", 10, "Australia/Sydney", "sydney", "melbourne", "canberra")
    _add_places("NZ", 12, "Pacific/Auckland", "auckland", "wellington")
    _add_places("", 0, "UTC", "utc", "gmt")


_table()

# What he might say instead of the city's own name.
_ALIASES = {
    "nyc": "new york", "new york city": "new york", "ny": "new york", "la": "los angeles",
    "sf": "san francisco", "bay area": "san francisco", "dc": "washington",
    "washington dc": "washington", "d c": "washington", "uk": "london", "england": "london",
    "britain": "london", "great britain": "london", "united kingdom": "london",
    # Only the "... time" forms: a bare "central" or "pacific" is as likely to
    # be a region as a US time zone, and a wrong guess is worse than a question.
    "eastern time": "new york", "pacific time": "los angeles",
    "central time": "chicago", "mountain time": "denver",
    "kiev": "kyiv", "ukraine": "kyiv", "uzbekistan": "tashkent", "russia": "moscow",
    "turkey": "istanbul", "japan": "tokyo", "south korea": "seoul", "korea": "seoul",
    "china": "beijing", "india": "delhi", "pakistan": "karachi", "kazakhstan": "almaty",
    "france": "paris", "germany": "berlin", "italy": "rome", "spain": "madrid",
    "poland": "warsaw", "netherlands": "amsterdam", "holland": "amsterdam",
    "saudi arabia": "riyadh", "uae": "dubai", "united arab emirates": "dubai",
    "singapore time": "singapore", "thailand": "bangkok", "vietnam": "hanoi",
    "ireland": "dublin", "portugal": "lisbon", "greece": "athens", "finland": "helsinki",
    "sweden": "stockholm", "norway": "oslo", "denmark": "copenhagen", "switzerland": "zurich",
    "austria": "vienna", "belgium": "brussels", "czech republic": "prague", "czechia": "prague",
    "hungary": "budapest", "romania": "bucharest", "bulgaria": "sofia",
    # No "georgia": the country and the US state are an easy mix-up here.
    "new zealand": "auckland", "kenya": "nairobi", "nigeria": "lagos", "ghana": "accra",
    "armenia": "yerevan", "azerbaijan": "baku", "qatar": "doha",
    "iraq": "baghdad", "afghanistan": "kabul", "nepal": "kathmandu", "bangladesh": "dhaka",
    "kyrgyzstan": "bishkek", "indonesia": "jakarta", "philippines": "manila",
    "malaysia": "kuala lumpur", "taiwan": "taipei", "hawaii": "honolulu", "alaska": "anchorage",
    "zulu": "utc", "universal time": "utc", "greenwich": "london",
}


def _clean_place(place: str) -> str:
    text = re.sub(r"[^\w\s/'\-]", " ", (place or "").lower())
    text = " ".join(text.split())
    for prefix in ("what time is it in ", "current time in ", "the local time in ",
                   "the time in ", "time in ", "in ", "the "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    for suffix in (" local time", " time", " city"):
        if text.endswith(suffix) and text not in _CITIES and text not in _ALIASES:
            text = text[: -len(suffix)]
    return text.strip()


def _zoneinfo_for(key: str):
    """The database's zone for a name like 'America/New_York', or None when
    there is no database (this machine's venv) or no such zone."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(key)
    except Exception:  # ZoneInfoNotFoundError, ValueError, OSError, ImportError
        return None


def _nth_sunday(year: int, month: int, n: int) -> int:
    first = datetime(year, month, 1).weekday()          # Monday is 0, Sunday is 6
    return 1 + (6 - first) % 7 + 7 * (n - 1)


def _last_sunday(year: int, month: int) -> int:
    last = datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return (last - timedelta(days=(last.weekday() + 1) % 7)).day


def _daylight_saving_now(place: _Place, utc: datetime) -> bool:
    """Is daylight saving in force at this UTC instant? Every rule is worked out
    in UTC, so there is no ambiguous local hour to get wrong."""
    year = utc.year
    std = timedelta(minutes=place.std_minutes)
    hour = timedelta(hours=1)

    def at(month: int, day: int, hh: int) -> datetime:
        return datetime(year, month, day, hh, tzinfo=timezone.utc)

    if place.rule == "US":      # 2nd Sunday of March 02:00 -> 1st Sunday of November 02:00, local
        return at(3, _nth_sunday(year, 3, 2), 2) - std <= utc < at(11, _nth_sunday(year, 11, 1), 2) - std - hour
    if place.rule == "EU":      # last Sunday of March -> last Sunday of October, both 01:00 UTC
        return at(3, _last_sunday(year, 3), 1) <= utc < at(10, _last_sunday(year, 10), 1)
    if place.rule == "AU":      # 1st Sunday of October 02:00 -> 1st Sunday of April 03:00 (south: wraps the year)
        return utc < at(4, _nth_sunday(year, 4, 1), 3) - std - hour or utc >= at(10, _nth_sunday(year, 10, 1), 2) - std
    if place.rule == "NZ":      # last Sunday of September 02:00 -> 1st Sunday of April 03:00
        return utc < at(4, _nth_sunday(year, 4, 1), 3) - std - hour or utc >= at(9, _last_sunday(year, 9), 2) - std
    return False


def _offset_minutes(place: _Place, utc: datetime) -> int:
    return place.std_minutes + (60 if _daylight_saving_now(place, utc) else 0)


def _hours_phrase(minutes: int) -> str:
    hours, rest = divmod(abs(int(minutes)), 60)
    if not hours:
        return "half an hour" if rest == 30 else f"{rest} minutes"
    unit = "an hour" if hours == 1 and not rest else f"{hours} hours"
    if hours == 1 and rest == 30:
        return "an hour and a half"
    if not rest:
        return unit
    return f"{hours} and a half hours" if rest == 30 else f"{hours} hours {rest} minutes"


def get_timezone() -> str:
    """This machine's time zone and what time it says there - GREEN, read-only."""
    here = _local_now()
    name = here.tzname() or "an unnamed zone"
    offset = int((here.utcoffset() or timedelta(0)).total_seconds() // 60)
    saving = " Daylight saving time is in effect." if _local_dst_in_effect() else ""
    return f"Your time zone is {name}, {_spoken_offset(offset)}, and it's {_clock(here)}.{saving}"


def time_in(place: str) -> str:
    """
    The time in another city, and how far from here - GREEN, read-only.

    Uses the time zone database when this machine has one (Python's `tzdata`
    package; Windows has none of its own) and otherwise the built-in table of
    major cities above. Asked for a place it does not know, it says so and says
    which of the two situations it is in.
    """
    key = _clean_place(place)
    if not key:
        return "Which place? Name a city, like Tokyo or New York."
    now = _utc_now()
    entry = _CITIES.get(_ALIASES.get(key, key))
    zone = _zoneinfo_for(entry.iana) if entry else None
    label = entry.label if entry else ""
    if entry is None and "/" in key:
        # A database name passed straight through ("Africa/Nairobi").
        iana = "/".join("_".join(w.capitalize() for w in part.split("_")) for part in key.split("/"))
        zone = _zoneinfo_for(iana)
        label = iana.rsplit("/", 1)[-1].replace("_", " ")
    if zone is not None:
        offset = int(now.astimezone(zone).utcoffset().total_seconds() // 60)
    elif entry is not None:
        offset = _offset_minutes(entry, now)
    else:
        has_database = _zoneinfo_for("Europe/London") is not None
        why = "" if has_database else " I only know major cities on this machine."
        return f"I don't know the time zone for {place.strip()}. Try a big city in the same country.{why}"

    there = now.astimezone(timezone(timedelta(minutes=offset)))
    here = _local_now()
    here_offset = int((here.utcoffset() or timedelta(0)).total_seconds() // 60)
    day = " tomorrow" if there.date() > here.date() else " yesterday" if there.date() < here.date() else ""
    gap = offset - here_offset
    if gap == 0:
        relation = "the same time as you"
    else:
        relation = f"{_hours_phrase(gap)} {'ahead of' if gap > 0 else 'behind'} you"
    return f"It's {_clock(there)}{day} in {label}, {relation}."


# ------------------------------------------------------------------- uptime
def _boot_time() -> float:
    import psutil

    return psutil.boot_time()


def _fast_startup_on() -> bool:
    """Windows Fast Startup (hybrid shutdown): HiberbootEnabled in the Power key."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager\Power") as key:
            return bool(winreg.QueryValueEx(key, "HiberbootEnabled")[0])
    except OSError:
        return False


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def get_uptime() -> str:
    """How long this computer has been on, and since when - GREEN, read-only."""
    try:
        boot = _boot_time()
        seconds = max(0, int(_utc_now().timestamp() - boot))
        started = _local_at(boot)
        today = _local_now().date()
    except Exception:  # noqa: BLE001 - spoken, so never a traceback
        return "I couldn't read the uptime on this machine."
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        length = _plural(days, "day") + (f" {_plural(hours, 'hour')}" if hours else "")
    elif hours:
        length = _plural(hours, "hour") + (f" {_plural(minutes, 'minute')}" if minutes else "")
    elif minutes:
        length = _plural(minutes, "minute")
    else:
        length = "less than a minute"
    gap = (today - started.date()).days
    if gap == 0:
        when = f"{_clock(started)} today"
    elif gap == 1:
        when = f"{_clock(started)} yesterday"
    elif gap < 7:
        when = f"{_clock(started)} on {started:%A}"
    else:
        when = f"{_clock(started)} on {started:%B} {started.day}"
    note = ""
    if days and _fast_startup_on():
        note = " Windows has Fast Startup on, so shutting down does not reset that - only a restart does."
    return f"This computer has been on for {length}, since {when}.{note}"


def get_battery() -> str:
    try:
        import psutil

        battery = psutil.sensors_battery()
        if battery is None:
            return "I can't read the battery on this machine."
        plugged = " and charging" if battery.power_plugged else ""
        return f"Battery is at {int(battery.percent)} percent{plugged}."
    except Exception as exc:
        return f"Couldn't read the battery: {exc}"


def get_system_status() -> str:
    try:
        import psutil

        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.4)
        disk = psutil.disk_usage("C:/" if IS_WINDOWS else "/")
        return (
            f"CPU {cpu:.0f} percent, memory {mem.percent:.0f} percent used with "
            f"{mem.available / 1e9:.1f} gigabytes free, disk {disk.percent:.0f} percent full."
        )
    except Exception as exc:
        return f"Couldn't read system status: {exc}"


def screenshot(path: str | None = None) -> str:
    try:
        from PIL import ImageGrab

        target = path or os.path.join(
            os.path.expanduser("~"), "Pictures", f"jalen-{datetime.now():%Y%m%d-%H%M%S}.png"
        )
        os.makedirs(os.path.dirname(target), exist_ok=True)
        ImageGrab.grab(all_screens=True).save(target)
        return f"Saved to {os.path.basename(target)}."
    except Exception as exc:
        return f"Screenshot failed: {exc}"


# --------------------------------------------------------------------- dispatch
REGISTRY = {
    "media_play_pause": media_play_pause,
    "media_next": media_next,
    "media_previous": media_previous,
    "volume_step": volume_step,
    "volume_set": volume_set,
    "volume_mute_toggle": volume_mute_toggle,
    "open_app": open_app,
    "close_app": close_app,
    "open_folder": open_folder,
    "open_url": open_url,
    "focus_window": focus_window,
    "window_state": window_state,
    "lock_workstation": lock_workstation,
    "sign_out": sign_out,
    "empty_recycle_bin": empty_recycle_bin,
    "get_time": get_time,
    "get_date": get_date,
    "get_timezone": get_timezone,
    "time_in": time_in,
    "get_uptime": get_uptime,
    "get_battery": get_battery,
    "get_system_status": get_system_status,
    "screenshot": screenshot,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
