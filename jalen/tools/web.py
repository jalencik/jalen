"""
Doing things on websites, not just opening them.

"Go to YouTube and search X and play it" is one intent, not three steps the
user should have to narrate. Before this, "open youtube" opened the site and
stopped — everything after that was on you.

Design note, because the obvious approach is worse: this drives sites by URL
wherever a site has a real URL grammar (YouTube search, Google search, a
channel, a direct video), NOT by clicking through the page with UI
automation. Two reasons, both learned the hard way in this project:

  * Chrome exposes almost nothing to UI automation by default — verified
    live, the accessibility tree for a Chrome window is a handful of
    anonymous panes plus the address bar. Clicking "the third search
    result" is not something that can be done reliably.
  * Page layouts change constantly. A URL like
    youtube.com/results?search_query=X has been stable for a decade.

So: URLs where a URL exists, keyboard where it doesn't, and honesty when
neither works — never a click-and-hope that reports success it can't verify.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
from typing import Any

from .system import IS_WINDOWS, _title_pattern

# Sites with a real search grammar. The value is a format string taking the
# url-encoded query. Adding a site here makes "search <site> for X" work.
_SEARCH_URLS: dict[str, str] = {
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "google": "https://www.google.com/search?q={q}",
    "chess": "https://www.chess.com/search?q={q}",
    "github": "https://github.com/search?q={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    "amazon": "https://www.amazon.com/s?k={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "linkedin": "https://www.linkedin.com/search/results/all/?keywords={q}",
    "twitter": "https://x.com/search?q={q}",
    "x": "https://x.com/search?q={q}",
    "spotify": "https://open.spotify.com/search/{q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "gmail": "https://mail.google.com/mail/u/0/#search/{q}",
    "drive": "https://drive.google.com/drive/search?q={q}",
}

_DEFAULT_SITE = "google"


def _launch(url: str) -> None:
    if IS_WINDOWS:
        subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    else:  # pragma: no cover - dev convenience
        subprocess.Popen(["xdg-open", url])


def _browser_showed(expect: str, timeout: float = 12.0) -> str | None:
    """
    The browser's window title once the page has loaded, or None.

    This is what makes the difference between reporting what happened and
    guessing. A page can fail to load, redirect, or land on a consent
    screen; the title tells us which, and it is the only signal available
    given Chrome exposes nothing else to automation.
    """
    try:
        import uiautomation as auto
    except ImportError:
        return None
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            win = auto.WindowControl(searchDepth=1, ClassName="Chrome_WidgetWin_1")
            if win.Exists(0.5, 0.2) and win.Name:
                last = win.Name
                if _title_pattern(expect).search(win.Name):
                    return win.Name
        except Exception:
            pass
        time.sleep(0.4)
    return last


def search_site(site: str, query: str) -> str:
    """
    Search a website and show the results — GREEN.

    "search youtube for lofi", "google the weather in Tashkent".
    """
    site_key = (site or "").strip().lower().replace(".com", "").replace("www.", "")
    query = (query or "").strip()
    if not query:
        return "Search for what?"

    template = _SEARCH_URLS.get(site_key)
    if template is None:
        # Not a site with a known search grammar — say so plainly and do the
        # closest useful thing rather than pretending to search it.
        template = _SEARCH_URLS[_DEFAULT_SITE]
        encoded = urllib.parse.quote_plus(f"{site_key} {query}".strip())
        _launch(template.format(q=encoded))
        title = _browser_showed(query.split()[0] if query.split() else "search")
        return (
            f"I don't know how to search {site_key} directly, so I googled "
            f"\"{site_key} {query}\" instead."
            + (f" {title.rsplit(' - ', 1)[0]} is up." if title else "")
        )

    encoded = urllib.parse.quote_plus(query)
    _launch(template.format(q=encoded))
    title = _browser_showed(query.split()[0] if query.split() else site_key)
    if title:
        return f"Searched {site_key} for {query}. Results are up."
    return f"I opened a {site_key} search for {query}, but the page didn't confirm it loaded."


def play_on_youtube(query: str) -> str:
    """
    Search YouTube and start the first result playing — GREEN.

    "play lofi hip hop", "go to youtube and play Timeless".

    How it plays without clicking: it reads the first video's id out of the
    results HTML and opens /watch?v=<id>, which autoplays. No keystrokes are
    sent to anything, so there is no focus to guess at and nothing to type
    into the wrong box.
    """
    query = (query or "").strip()
    if not query:
        return "Play what?"

    # Resolve the video BEFORE opening anything, and open it directly.
    #
    # This used to open the results page and then send six Tabs and an Enter,
    # hoping focus had landed on the first result. It had not. Where focus
    # actually starts after `start <url>` depends on the browser, the tab,
    # and whether the page has finished loading — very often it is the
    # YouTube search box, so six Tabs walked along the header and Enter
    # re-submitted the search. He reported exactly that: it typed into the
    # search bar and wiped what was there.
    #
    # Nothing about that approach can be made reliable, because it is
    # guessing at a focus state it cannot observe. Reading the video id out
    # of the results page and opening /watch?v=<id> is deterministic: the
    # video opens and autoplays, with no keystrokes sent anywhere.
    video_id, title = _first_youtube_result(query)
    if video_id is None:
        # Falling back to the results page is honest and still useful — but
        # say so, and never claim playback that did not happen.
        encoded = urllib.parse.quote_plus(query)
        _launch(f"https://www.youtube.com/results?search_query={encoded}")
        return (
            f"I couldn't reach YouTube to find a video for {query}, so I've "
            "opened the search results instead. Click the one you want."
        )

    _launch(f"https://www.youtube.com/watch?v={video_id}")
    if title:
        return f"Playing {title} on YouTube."
    return f"Playing the top result for {query} on YouTube."


def _first_youtube_result(query: str) -> tuple[str | None, str | None]:
    """
    The video id and title of the first real result, or (None, None).

    YouTube renders results from a JSON blob embedded in the page, so the
    ids are in the HTML even though the visible list is built by script.
    `videoRenderer` is what marks an actual video — matching bare "videoId"
    instead picks up autoplay hints, shorts shelves and sidebar suggestions,
    which is how you end up playing something unrelated to what he asked
    for.
    """
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.youtube.com/results?search_query={encoded}"
    try:
        from .research import _get

        status, html = _get(url)
        if status != 200 or not html:
            return None, None
    except Exception:
        return None, None

    match = re.search(
        r'"videoRenderer":\s*\{\s*"videoId":\s*"([\w-]{11})"', html
    )
    if match is None:
        # Some responses order the keys differently; accept a videoId that
        # appears within a short distance of a videoRenderer marker.
        window = re.search(r'"videoRenderer".{0,400}?"videoId":"([\w-]{11})"', html, re.S)
        if window is None:
            return None, None
        match = window

    video_id = match.group(1)
    title = None
    after = html[match.end(): match.end() + 2000]
    title_match = re.search(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)+)"', after)
    if title_match:
        try:
            title = json.loads(f'"{title_match.group(1)}"')
        except Exception:
            title = title_match.group(1)
    return video_id, title


# What "play X" actually means, when he did not say where.
#
# HIS COMPLAINT, and it was fair:
#
#     "this bitch has been searching for 'we are the people' when I am
#      actually saying play we are the people man, it means it cannot handle
#      it without that go to youtube jargon"
#
# It routed to open_target - a search of his Desktop and Documents for a FILE
# by that name - which found nothing and said so. Nobody saying "play We Are
# The People" means "look for a file called that", and requiring him to add
# "on YouTube" is making him speak the router's language instead of his own.
#
# LOCAL FIRST, THEN YOUTUBE, because both readings are real: "play my
# interview recording" is a file and "play We Are The People" is not. Trying
# the file search costs nothing when it misses, and a hit is unambiguous.
_MEDIA_FILLER = re.compile(
    r"^(?:me|us|it|that|this|the|a|an|some|my)\s+", re.I
)


def _strip_filler(query: str) -> str:
    """
    "play me the We are the people" -> "We are the people".

    Straight out of the log: open_target was handed "me the We are the
    people" and searched for a file by that literal name. Repeated because
    "me the" is two fillers, and one pass leaves the second.
    """
    text = (query or "").strip()
    for _ in range(3):
        stripped = _MEDIA_FILLER.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    return text or (query or "").strip()


def play_media(query: str) -> str:
    """
    Play something. A local file if there is one, YouTube otherwise - GREEN.

    Never answers "I couldn't find an app, file or folder called ..." for a
    song title, which is the reply that started this.
    """
    wanted = _strip_filler(query)
    if not wanted:
        return "Play what?"

    # Local files, quietly. Only an actual media file counts - matching a
    # PDF called "we are the people" and opening it would be worse than
    # missing.
    try:
        from .launcher import find_files

        hits = [h for h in (find_files(wanted) or [])
                if str(h).lower().endswith(
                    (".mp3", ".mp4", ".m4a", ".wav", ".flac", ".mkv", ".avi",
                     ".mov", ".webm", ".ogg", ".wma", ".aac"))]
        if hits:
            from .launcher import open_target

            return open_target(str(hits[0]))
    except Exception:
        pass       # a failed local search must never block the YouTube path

    return play_on_youtube(wanted)


REGISTRY: dict[str, Any] = {
    "play_media": play_media,
    "search_site": search_site,
    "play_on_youtube": play_on_youtube,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
