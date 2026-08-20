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

    How it plays without clicking: YouTube's own keyboard shortcuts. After
    the results page loads, Tab moves focus into the results list and Enter
    opens the focused video, which autoplays. This is the documented
    keyboard path through YouTube's own UI, and it survives layout changes
    that would break any coordinate- or element-based click.
    """
    query = (query or "").strip()
    if not query:
        return "Play what?"

    encoded = urllib.parse.quote_plus(query)
    _launch(f"https://www.youtube.com/results?search_query={encoded}")

    title = _browser_showed(query.split()[0] if query.split() else "youtube", timeout=15.0)
    if title is None:
        return f"I searched YouTube for {query} but couldn't confirm the page loaded."

    try:
        import uiautomation as auto

        win = auto.WindowControl(searchDepth=1, ClassName="Chrome_WidgetWin_1")
        if not win.Exists(2, 0.3):
            return f"Searched YouTube for {query}, but I lost track of the browser window."
        win.SetActive()
        time.sleep(0.8)
        # Into the page body, then onto the first result, then open it.
        for _ in range(6):
            win.SendKeys("{Tab}", waitTime=0.12)
        win.SendKeys("{Enter}", waitTime=0.2)
    except Exception as exc:
        return (
            f"Searched YouTube for {query} — the results are up, but I couldn't "
            f"start playback ({type(exc).__name__}). Press Enter on the first video."
        )

    time.sleep(3.0)
    now = _browser_showed(query.split()[0] if query.split() else "youtube", timeout=6.0)
    # A video page title loses the "N results" prefix a search page carries.
    if now and "- YouTube" in now and "results" not in now.lower():
        name = now.rsplit(" - YouTube", 1)[0].strip()
        # YouTube prefixes the tab title with an unread-notification count,
        # "(394) lofi hip hop" — spoken aloud that is noise, and it made the
        # reply sound broken.
        name = re.sub(r"^\(\d+\)\s*", "", name)
        return f"Playing {name} on YouTube."
    return (
        f"Searched YouTube for {query} and the results are up. I couldn't "
        "confirm a video started — press Enter on the first one if it didn't."
    )


REGISTRY: dict[str, Any] = {
    "search_site": search_site,
    "play_on_youtube": play_on_youtube,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
