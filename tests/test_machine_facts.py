"""
Jalen could not answer five plain questions about the machine he runs on.

Found by driving the real thing in typed mode on 2026-10-01 (55 requests):

    "what time is it in New York"      "That's your local time here, Boss, not
                                        New York's - I don't have a timezone
                                        lookup tool."
    "what's my local time zone"        "I don't have a tool that reports your
                                        timezone."
    "how long has my laptop been on"   "I don't have a tool that reports uptime"
    "which app is in front right now"  a hedged guess; get_window_list had no
                                        foreground flag
    "what programs are running"        a list of WINDOWS, and the Claude
                                        desktop app called "Chrome", because
                                        get_window_list reported only the
                                        window class (Chrome_WidgetWin_1)

None of these needs a network or an account. The machine knows its own zone,
its boot time, which window is in front and what is running.

ONE FACT THAT SHAPES THE TIME TOOL. Python's zoneinfo needs a time zone
database, and Windows has none of its own: the `tzdata` package supplies it.
It is NOT installed in this project's venv (zoneinfo.available_timezones() is
empty there; ZoneInfo("America/New_York") raises) and is not in
requirements.txt. So "what time is it in New York" cannot be answered from
zoneinfo on this machine as it stands. time_in therefore carries a small
built-in table of major cities with their daylight saving rules, uses
zoneinfo when the database exists, and says plainly what it does not know.
"""
from __future__ import annotations

import datetime as dt
from datetime import timedelta, timezone

import pytest

from jarvis.tools import desktop, system


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _utc(y, mo, d, h=0, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def _tashkent(utc_now):
    """This machine's own zone: UTC+5, Windows calls it West Asia Standard Time."""
    return utc_now.astimezone(timezone(timedelta(hours=5), "West Asia Standard Time"))


@pytest.fixture
def clock(monkeypatch):
    """Pin 'now' (a UTC instant) and the machine's own zone (Tashkent, UTC+5),
    and pretend there is NO time zone database, as in the real venv."""
    state = {"now": _utc(2026, 10, 1, 12, 0)}
    monkeypatch.setattr(system, "_utc_now", lambda: state["now"])
    monkeypatch.setattr(system, "_local_now", lambda: _tashkent(state["now"]))
    monkeypatch.setattr(system, "_local_at", lambda ts: _tashkent(dt.datetime.fromtimestamp(ts, timezone.utc)))
    monkeypatch.setattr(system, "_zoneinfo_for", lambda key: None)
    monkeypatch.setattr(system, "_local_dst_in_effect", lambda: False)
    return state


# ---------------------------------------------------------------------------
# time in another city
# ---------------------------------------------------------------------------
def test_time_in_new_york_without_a_time_zone_database(clock):
    """12:00 UTC on 1 Oct 2026: New York is on daylight time, UTC-4."""
    reply = system.time_in("New York")
    assert "8:00 AM" in reply
    assert "New York" in reply
    assert "9 hours behind you" in reply        # Tashkent is UTC+5


@pytest.mark.parametrize("spoken", ["new york", "New York City", "nyc", "NEW YORK.",
                                    "the time in new york", "New York time"])
def test_the_place_is_matched_the_way_he_would_say_it(clock, spoken):
    assert "8:00 AM" in system.time_in(spoken)


def test_a_day_ahead_is_said_so(clock):
    clock["now"] = _utc(2026, 10, 1, 20, 0)      # 1:00 AM in Tashkent on the 2nd
    reply = system.time_in("Tokyo")              # UTC+9: 5:00 AM on the 2nd
    assert "5:00 AM" in reply and "4 hours ahead of you" in reply
    assert "tomorrow" not in reply               # same calendar day as Tashkent's

    clock["now"] = _utc(2026, 10, 1, 17, 0)      # 10:00 PM in Tashkent
    assert "tomorrow" in system.time_in("Tokyo")  # 2:00 AM on the 2nd
    clock["now"] = _utc(2026, 10, 1, 2, 0)       # 7:00 AM in Tashkent
    assert "yesterday" in system.time_in("Los Angeles")  # 7:00 PM on Sept 30


def test_half_hour_and_same_time_phrases(clock):
    assert "half an hour ahead of you" in system.time_in("Delhi")   # UTC+5:30 vs Tashkent UTC+5
    assert "the same time as you" in system.time_in("Samarkand")
    assert "an hour behind you" in system.time_in("Dubai")          # UTC+4
    assert "2 hours behind you" in system.time_in("Moscow")         # UTC+3
    assert "45 minutes ahead of you" in system.time_in("Kathmandu")  # UTC+5:45
    assert "2 hours ahead of you" in system.time_in("Bangkok")       # UTC+7


def test_us_time_zone_names_work_only_with_the_word_time(clock):
    assert "8:00 AM" in system.time_in("Eastern time")
    assert "5:00 AM" in system.time_in("pacific time")
    assert "7:00 AM" in system.time_in("central time")


@pytest.mark.parametrize("vague", ["central", "pacific", "mountain", "Georgia"])
def test_a_name_that_could_mean_two_places_is_asked_about_not_guessed(clock, vague):
    reply = system.time_in(vague)
    assert "don't know" in reply and "AM" not in reply and "PM" not in reply


def test_every_place_in_the_table_resolves_and_is_spoken(clock):
    for city in system._CITIES:
        reply = system.time_in(city)
        assert " AM" in reply or " PM" in reply, city


def test_an_unknown_place_says_what_this_machine_can_and_cannot_do(clock):
    reply = system.time_in("Atlantis")
    assert "Atlantis" in reply
    assert "major cities" in reply            # no database here: say so
    assert "AM" not in reply and "PM" not in reply


def test_with_a_database_any_zone_it_knows_is_answered(clock, monkeypatch):
    """When tzdata IS installed, an IANA name works even if it is not in the table."""
    from zoneinfo import ZoneInfo  # noqa: F401 - only to prove the import path

    fake = timezone(timedelta(hours=3), "Fake")

    def _zone(key):
        return fake if key == "Africa/Nairobi" else None

    monkeypatch.setattr(system, "_zoneinfo_for", _zone)
    reply = system.time_in("Africa/Nairobi")
    assert "3:00 PM" in reply
    assert "2 hours behind you" in reply


def test_with_a_database_the_database_wins_over_the_table(clock, monkeypatch):
    seen = []

    def _zone(key):
        seen.append(key)
        return timezone(timedelta(hours=-4), "EDT")

    monkeypatch.setattr(system, "_zoneinfo_for", _zone)
    assert "8:00 AM" in system.time_in("New York")
    assert seen and seen[0] == "America/New_York"


def test_an_empty_place_asks_for_one(clock):
    assert "which" in system.time_in("").lower()


# ---------------------------------------------------------------------------
# the built-in daylight saving rules: transition instants in 2026
# (US 8 Mar / 1 Nov, EU 29 Mar / 25 Oct, Sydney 5 Apr / 4 Oct, Auckland 5 Apr / 27 Sep)
# ---------------------------------------------------------------------------
def _offset_hours(city, when):
    return system._offset_minutes(system._CITIES[city], when) / 60


@pytest.mark.parametrize("city, when, expected", [
    # United States: 2nd Sunday of March 02:00 local -> 07:00 UTC in New York
    ("new york", _utc(2026, 3, 8, 6, 59), -5),
    ("new york", _utc(2026, 3, 8, 7, 0), -4),
    # ...and the 1st Sunday of November 02:00 local daylight -> 06:00 UTC
    ("new york", _utc(2026, 11, 1, 5, 59), -4),
    ("new york", _utc(2026, 11, 1, 6, 0), -5),
    ("los angeles", _utc(2026, 3, 8, 9, 59), -8),
    ("los angeles", _utc(2026, 3, 8, 10, 0), -7),
    ("chicago", _utc(2026, 7, 1), -5),
    ("denver", _utc(2026, 1, 15), -7),
    # Europe: last Sunday of March and October, both at 01:00 UTC
    ("london", _utc(2026, 3, 29, 0, 59), 0),
    ("london", _utc(2026, 3, 29, 1, 0), 1),
    ("london", _utc(2026, 10, 25, 0, 59), 1),
    ("london", _utc(2026, 10, 25, 1, 0), 0),
    ("paris", _utc(2026, 7, 1), 2),
    ("paris", _utc(2026, 12, 1), 1),
    ("kyiv", _utc(2026, 7, 1), 3),
    # Australia (south): DST from the 1st Sunday of October to the 1st Sunday of April
    ("sydney", _utc(2026, 4, 4, 15, 59), 11),
    ("sydney", _utc(2026, 4, 4, 16, 0), 10),
    ("sydney", _utc(2026, 10, 3, 15, 59), 10),
    ("sydney", _utc(2026, 10, 3, 16, 0), 11),
    ("sydney", _utc(2026, 1, 15), 11),
    ("sydney", _utc(2026, 7, 15), 10),
    # New Zealand: last Sunday of September to the 1st Sunday of April
    ("auckland", _utc(2026, 9, 26, 13, 59), 12),
    ("auckland", _utc(2026, 9, 26, 14, 0), 13),
    ("auckland", _utc(2026, 4, 4, 13, 59), 13),
    ("auckland", _utc(2026, 4, 4, 14, 0), 12),
    # places that do not change
    ("tashkent", _utc(2026, 7, 1), 5),
    ("tashkent", _utc(2026, 1, 1), 5),
    ("tokyo", _utc(2026, 7, 1), 9),
    ("dubai", _utc(2026, 1, 1), 4),
    ("moscow", _utc(2026, 7, 1), 3),
    ("istanbul", _utc(2026, 7, 1), 3),
    ("phoenix", _utc(2026, 7, 1), -7),
    ("honolulu", _utc(2026, 7, 1), -10),
    ("delhi", _utc(2026, 7, 1), 5.5),
    ("kathmandu", _utc(2026, 7, 1), 5.75),
    ("sao paulo", _utc(2026, 7, 1), -3),
])
def test_the_built_in_daylight_saving_rules(city, when, expected):
    assert _offset_hours(city, when) == expected


# ---------------------------------------------------------------------------
# his own time zone
# ---------------------------------------------------------------------------
def test_get_timezone_names_the_zone_and_the_offset(clock):
    reply = system.get_timezone()
    assert "West Asia Standard Time" in reply
    assert "UTC plus 5" in reply
    assert "5:00 PM" in reply


def test_get_timezone_says_so_when_daylight_saving_is_on(clock, monkeypatch):
    monkeypatch.setattr(system, "_local_dst_in_effect", lambda: True)
    assert "daylight saving" in system.get_timezone().lower()
    monkeypatch.setattr(system, "_local_dst_in_effect", lambda: False)
    assert "daylight" not in system.get_timezone().lower()


@pytest.mark.parametrize("hours, minutes, expected", [
    (5, 0, "UTC plus 5"), (-4, 0, "UTC minus 4"), (0, 0, "UTC"),
    (5, 30, "UTC plus 5:30"), (-3, -30, "UTC minus 3:30"),
])
def test_offsets_are_spoken_the_way_a_person_says_them(hours, minutes, expected):
    assert system._spoken_offset(hours * 60 + minutes) == expected


# ---------------------------------------------------------------------------
# uptime
# ---------------------------------------------------------------------------
def test_uptime_in_hours_and_minutes(clock, monkeypatch):
    boot = clock["now"] - timedelta(hours=3, minutes=17)
    monkeypatch.setattr(system, "_boot_time", lambda: boot.timestamp())
    monkeypatch.setattr(system, "_fast_startup_on", lambda: False)
    reply = system.get_uptime()
    assert "3 hours" in reply and "17 minutes" in reply
    assert "since 1:43 PM today" in reply         # 08:43 UTC = 13:43 Tashkent


def test_uptime_in_days_drops_the_minutes(clock, monkeypatch):
    boot = clock["now"] - timedelta(days=2, hours=4, minutes=30)
    monkeypatch.setattr(system, "_boot_time", lambda: boot.timestamp())
    monkeypatch.setattr(system, "_fast_startup_on", lambda: False)
    reply = system.get_uptime()
    assert "2 days" in reply and "4 hours" in reply
    assert "minutes" not in reply


def test_uptime_of_a_few_minutes(clock, monkeypatch):
    monkeypatch.setattr(system, "_boot_time", lambda: (clock["now"] - timedelta(minutes=1)).timestamp())
    monkeypatch.setattr(system, "_fast_startup_on", lambda: False)
    assert "1 minute" in system.get_uptime()


def test_fast_startup_is_mentioned_only_when_it_could_mislead(clock, monkeypatch):
    """
    With Fast Startup on, a shutdown does not reset Windows' clock - only a
    restart does - so "on for 5 days" can be true on a laptop he shut down
    last night. Said once the figure is a day or more, not for a fresh boot.
    """
    monkeypatch.setattr(system, "_fast_startup_on", lambda: True)
    monkeypatch.setattr(system, "_boot_time", lambda: (clock["now"] - timedelta(days=5)).timestamp())
    assert "Fast Startup" in system.get_uptime()
    monkeypatch.setattr(system, "_boot_time", lambda: (clock["now"] - timedelta(hours=3)).timestamp())
    assert "Fast Startup" not in system.get_uptime()
    monkeypatch.setattr(system, "_fast_startup_on", lambda: False)
    monkeypatch.setattr(system, "_boot_time", lambda: (clock["now"] - timedelta(days=5)).timestamp())
    assert "Fast Startup" not in system.get_uptime()


def test_uptime_that_cannot_be_read_is_a_sentence_not_a_traceback(clock, monkeypatch):
    def _boom():
        raise OSError("no")

    monkeypatch.setattr(system, "_boot_time", _boom)
    reply = system.get_uptime()
    assert isinstance(reply, str) and "uptime" in reply.lower() and "Traceback" not in reply


def test_the_real_machine_answers_all_three_facts():
    """No faking at all: these run against the machine the suite runs on."""
    assert "UTC" in system.get_timezone()
    assert "on for" in system.get_uptime()
    assert " AM" in system.time_in("Tokyo") or " PM" in system.time_in("Tokyo")


# ---------------------------------------------------------------------------
# which app is in front, and what is running
# ---------------------------------------------------------------------------
class _Rect:
    def __init__(self, w=800, h=600):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


class _Win:
    def __init__(self, title, cls, pid, hwnd, size=(800, 600)):
        self.Name = title
        self.ClassName = cls
        self.ProcessId = pid
        self.NativeWindowHandle = hwnd
        self.BoundingRectangle = _Rect(*size)


class _FakeAuto:
    def __init__(self, windows):
        self._windows = windows

    def GetRootControl(self):
        outer = self

        class _Root:
            def GetChildren(self):
                return outer._windows

        return _Root()


class _FakeProc:
    def __init__(self, pid, name, exe="", rss=0):
        self.pid, self._name, self._exe = pid, name, exe
        self.info = {"pid": pid, "name": name, "exe": exe,
                     "memory_info": type("M", (), {"rss": rss})()}

    def name(self):
        return self._name

    def exe(self):
        return self._exe


class _FakePsutil:
    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    def __init__(self, procs):
        self._procs = {p.pid: p for p in procs}

    def Process(self, pid):
        if pid not in self._procs:
            raise self.NoSuchProcess(pid)
        return self._procs[pid]

    def process_iter(self, attrs=None):
        return iter(list(self._procs.values()))


CLAUDE_APP = r"C:\Program Files\WindowsApps\Claude_2.16120.0.0_x64__pzs8sxrjxfjjc\app\claude.exe"
CLAUDE_CODE = r"C:\Users\u\AppData\Local\Packages\Claude_x\LocalCache\Roaming\Claude\claude-code\2.1.284\claude.exe"
GB = 1_000_000_000
MB = 1_000_000


@pytest.fixture
def desk(monkeypatch):
    """A desktop: Claude's app in front, two Chrome windows, Calculator (a
    Store app, so its window belongs to ApplicationFrameHost), the taskbar."""
    procs = [
        _FakeProc(10, "claude.exe", CLAUDE_APP, 600 * MB),
        _FakeProc(11, "claude.exe", CLAUDE_APP, 150 * MB),          # a helper, no window
        _FakeProc(12, "claude.exe", CLAUDE_CODE, 300 * MB),         # the CLI Jalen spawns
        _FakeProc(20, "chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe", 1 * GB),
        _FakeProc(21, "chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe", 900 * MB),
        _FakeProc(30, "ApplicationFrameHost.exe", r"C:\Windows\System32\ApplicationFrameHost.exe", 40 * MB),
        _FakeProc(40, "svchost.exe", r"C:\Windows\System32\svchost.exe", 700 * MB),
        _FakeProc(41, "svchost.exe", r"C:\Windows\System32\svchost.exe", 400 * MB),
        _FakeProc(50, "python.exe", r"C:\proj\.venv\Scripts\python.exe", 350 * MB),
        _FakeProc(60, "FooBarApp.exe", r"C:\Apps\FooBarApp.exe", 80 * MB),
        _FakeProc(99, "explorer.exe", r"C:\Windows\explorer.exe", 120 * MB),
    ]
    windows = [
        _Win("Claude", "Chrome_WidgetWin_1", 10, 1001),
        _Win("Inbox - Gmail - Google Chrome", "Chrome_WidgetWin_1", 20, 1002),
        _Win("Research notes - Google Chrome", "Chrome_WidgetWin_1", 21, 1003),
        _Win("Calculator", "ApplicationFrameWindow", 30, 1004),
        _Win("", "Shell_TrayWnd", 99, 1005),
        _Win("Program Manager", "Progman", 99, 1006),
        _Win("Jalen orb", "TkTopLevel", 777, 1007),
        _Win("Hidden zero size", "Whatever", 60, 1008, size=(0, 0)),
    ]
    monkeypatch.setattr(desktop, "auto", _FakeAuto(windows))
    monkeypatch.setattr(desktop, "psutil", _FakePsutil(procs))
    monkeypatch.setattr(desktop.os, "getpid", lambda: 777)
    monkeypatch.setattr(desktop, "_foreground_hwnd", lambda: 1001)
    monkeypatch.setattr(desktop, "_foreground_window", lambda: {
        "hwnd": 1001, "title": "Claude", "pid": 10, "cls": "Chrome_WidgetWin_1"})
    monkeypatch.setattr(desktop, "_visible_windows", lambda: [
        (10, "Claude"), (20, "Inbox - Gmail - Google Chrome"),
        (21, "Research notes - Google Chrome"), (30, "Calculator")])
    return windows


# --- friendly names ----------------------------------------------------------
@pytest.mark.parametrize("process, exe, title, expected", [
    ("chrome.exe", "", "", "Google Chrome"),
    ("Code.exe", "", "", "VS Code"),
    ("WindowsTerminal.exe", "", "", "Windows Terminal"),
    ("explorer.exe", "", "", "File Explorer"),
    ("svchost.exe", "", "", "Windows services"),
    ("claude.exe", CLAUDE_APP, "", "Claude"),
    ("claude.exe", CLAUDE_CODE, "", "Claude Code"),
    ("ApplicationFrameHost.exe", "", "Calculator", "Calculator"),
    ("FooBarApp.exe", "", "", "Foo Bar App"),
    ("some_tool.exe", "", "", "Some tool"),
    ("", "", "", "an unknown program"),
])
def test_friendly_names(process, exe, title, expected):
    assert desktop.friendly_program_name(process, exe, title) == expected


# --- get_window_list ---------------------------------------------------------
def test_the_claude_app_is_not_called_chrome(desk):
    """THE BUG: same window class as Chrome, so Claude was reported as Chrome."""
    reply = desktop.get_window_list()
    assert "Chrome_WidgetWin" not in reply
    assert "Claude" in reply
    assert "claude.exe" not in reply.lower()       # no file names read aloud


def test_the_window_list_says_which_one_is_in_front(desk):
    reply = desktop.get_window_list()
    assert reply.startswith("In front: Claude.")
    assert "Also open:" in reply
    assert "Inbox - Gmail in Google Chrome" in reply
    assert "Research notes in Google Chrome" in reply
    assert "Calculator" in reply


def test_the_taskbar_the_desktop_and_jalens_own_windows_are_not_listed(desk):
    reply = desktop.get_window_list()
    for noise in ("Program Manager", "Shell_TrayWnd", "Jalen orb", "Hidden zero size", "taskbar"):
        assert noise not in reply


def test_a_window_list_with_nothing_in_front_makes_no_claim(desk, monkeypatch):
    monkeypatch.setattr(desktop, "_foreground_hwnd", lambda: 424242)
    reply = desktop.get_window_list()
    assert "In front" not in reply
    assert reply.startswith("Open windows:")


def test_only_the_shell_open_is_said_plainly(desk, monkeypatch):
    monkeypatch.setattr(desktop, "auto", _FakeAuto([_Win("", "Shell_TrayWnd", 99, 1005),
                                                    _Win("Program Manager", "Progman", 99, 1006)]))
    reply = desktop.get_window_list()
    assert reply != "No visible windows found."
    assert "desktop" in reply.lower()


def test_a_window_title_is_capped(desk, monkeypatch):
    long_title = "A" * 500
    monkeypatch.setattr(desktop, "auto", _FakeAuto([_Win(long_title, "X", 60, 1001)]))
    assert long_title not in desktop.get_window_list()


# --- foreground_app ----------------------------------------------------------
def test_foreground_app_names_the_program_not_the_window_class(desk):
    reply = desktop.foreground_app()
    assert reply == "Claude is in front."


def test_foreground_app_adds_the_title_when_it_says_more(desk, monkeypatch):
    monkeypatch.setattr(desktop, "_foreground_window", lambda: {
        "hwnd": 1002, "title": "Inbox - Gmail - Google Chrome", "pid": 20, "cls": "Chrome_WidgetWin_1"})
    reply = desktop.foreground_app()
    assert reply.startswith("Google Chrome is in front.")
    assert "Inbox - Gmail" in reply
    assert "Chrome_WidgetWin" not in reply


def test_foreground_app_on_a_store_app_uses_the_window_title(desk, monkeypatch):
    monkeypatch.setattr(desktop, "_foreground_window", lambda: {
        "hwnd": 1004, "title": "Calculator", "pid": 30, "cls": "ApplicationFrameWindow"})
    reply = desktop.foreground_app()
    assert reply == "Calculator is in front."
    assert "ApplicationFrameHost" not in reply


def test_foreground_app_on_the_desktop(desk, monkeypatch):
    monkeypatch.setattr(desktop, "_foreground_window", lambda: {
        "hwnd": 1006, "title": "Program Manager", "pid": 99, "cls": "Progman"})
    assert "desktop" in desktop.foreground_app().lower()


def test_foreground_app_when_it_cannot_tell(desk, monkeypatch):
    monkeypatch.setattr(desktop, "_foreground_window", lambda: None)
    reply = desktop.foreground_app()
    assert "can't tell" in reply and "Traceback" not in reply


def test_foreground_app_in_jalens_own_window(desk, monkeypatch):
    monkeypatch.setattr(desktop, "_foreground_window", lambda: {
        "hwnd": 1007, "title": "Jalen orb", "pid": 777, "cls": "TkTopLevel"})
    assert "Jalen" in desktop.foreground_app()


# --- running_programs --------------------------------------------------------
def test_running_programs_groups_processes_into_programs(desk):
    reply = desktop.running_programs()
    assert reply.count("Google Chrome") == 1               # two processes, one program
    assert "1.9 gigabytes" in reply                         # 1.0 + 0.9 GB
    assert "Chrome_WidgetWin" not in reply and "chrome.exe" not in reply.lower()


def test_running_programs_puts_the_ones_with_a_window_first(desk):
    reply = desktop.running_programs()
    windowed, _, background = reply.partition("In the background")
    for name in ("Google Chrome", "Claude", "Calculator"):
        assert name in windowed
    assert "Windows services" in background and "Windows services" not in windowed
    assert "Foo Bar App" not in windowed          # no window of its own (the zero-size one is hidden)


def test_the_two_claudes_are_two_programs(desk):
    """The desktop app and the Claude Code CLI Jalen spawns share a file name."""
    reply = desktop.running_programs()
    assert "Claude Code" in reply and "Claude at" in reply


def test_running_programs_is_biggest_first_and_respects_the_limit(desk):
    reply = desktop.running_programs(top_n=1)
    windowed = reply.partition("In the background")[0]
    assert "Google Chrome" in windowed
    assert "Claude at" not in windowed


def test_a_store_app_is_named_without_borrowing_the_hosts_memory(desk):
    """Calculator's window belongs to ApplicationFrameHost, which hosts every Store
    app: its 40 MB is not Calculator's, so no size is claimed."""
    reply = desktop.running_programs()
    assert "Calculator" in reply
    assert "Calculator at" not in reply


def test_running_programs_says_how_many_in_all(desk):
    assert "programs" in desktop.running_programs()


def test_running_programs_without_psutil_is_a_sentence(monkeypatch):
    monkeypatch.setattr(desktop, "psutil", None)
    assert "can't" in desktop.running_programs().lower()


def test_the_real_machine_answers_running_programs_and_foreground():
    assert desktop.running_programs()
    assert desktop.foreground_app()


# ---------------------------------------------------------------------------
# the three edits every new tool needs, and that none of them acts
# ---------------------------------------------------------------------------
NEW_TOOLS = ("get_timezone", "time_in", "get_uptime", "foreground_app", "running_programs")


def test_the_new_tools_are_registered_specced_and_green():
    import yaml

    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS

    y = yaml.safe_load(open("config/safety.yaml", encoding="utf-8"))
    green = set((y.get("green") or {}).get("tools") or [])
    refused_from_content = set((y.get("injection_guard") or {}).get("refuse_from_content") or [])
    for name in NEW_TOOLS:
        assert name in tools.REGISTRY, f"{name}: not in REGISTRY"
        assert name in TOOL_SPECS, f"{name}: no TOOL_SPECS entry"
        assert name in green, f"{name}: not GREEN in config/safety.yaml"
        assert name not in refused_from_content, (
            f"{name} only reads; it must not be refused after a read")


def test_time_in_is_the_only_one_that_takes_an_argument():
    from jarvis.brain.tools import TOOL_SPECS

    assert set(TOOL_SPECS["time_in"][1]) == {"place"}
    for name in ("get_timezone", "get_uptime", "foreground_app"):
        assert TOOL_SPECS[name][1] == {}


def test_the_new_tools_classify_green_even_after_a_read():
    """Reading what a stranger wrote must not take away 'what time is it in Tokyo'."""
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    for name in NEW_TOOLS:
        args = {"place": "Tokyo"} if name == "time_in" else {}
        for origin in ("user", "content"):
            verdict = engine.classify(name, args, origin=origin)
            assert verdict.tier is Tier.GREEN, (name, origin, verdict.tier)
            assert not verdict.detail.get("unclassified"), name
