"""
The protected-domain list matched a substring of every argument joined together.

"*.paypal.com" became the substring ".paypal.com", and "*.binance.*" became
".binance.". So the bare domain - the one people actually type - was never
protected:

    open_url("https://www.paypal.com")          BLACK
    open_url("https://paypal.com/signin")       GREEN-to-AMBER (no dot before it)
    web_read("https://binance.com")             not refused
    search_site(site="paypal.com")              not refused

And the same substring fired on prose: a Telegram message that merely said
"I paid with click.uz" was refused outright, because the text argument was
in the joined blob.

Found by an adversarial review of the browse_to work. browse_to lets his
signed-in Chrome go anywhere, which makes this list the only hard stop left
between a request and his bank.

THE RULE NOW: parse the HOSTNAME out of anything that is a destination - an
argument named for one (url, site, link...) or a value that is a whole URL -
and compare it with the pattern: the domain itself or any subdomain of it,
globs honoured. Prose is left alone. A web address that cannot be parsed is
refused, not waved through.
"""
from __future__ import annotations

import pytest

from jarvis.config import CONFIG
from jarvis.safety import SafetyEngine, Tier


@pytest.fixture
def engine():
    return SafetyEngine(CONFIG)


@pytest.mark.parametrize("tool,args", [
    ("open_url", {"url": "https://paypal.com/signin"}),
    ("open_url", {"url": "https://www.paypal.com"}),
    ("open_url", {"url": "HTTPS://WWW.PAYPAL.COM./myaccount"}),
    ("open_url", {"url": "https://someone:secret@paypal.com/"}),
    ("open_url", {"url": "https://paypal.com:443/"}),
    ("web_read", {"url": "https://binance.com"}),
    ("web_read", {"url": "https://www.binance.com/en/login"}),
    ("search_site", {"site": "paypal.com", "query": "refund"}),
    ("open_url", {"url": "payme.uz"}),
    ("open_url", {"url": "https://my.click.uz/login"}),
    ("site_permission", {"url": "click.uz"}),
    # A whole-URL value is a destination whatever its argument is called.
    ("remember_alias", {"alias": "money", "target": "https://paypal.com"}),
])
def test_every_spelling_of_a_protected_site_is_refused(engine, tool, args):
    assert engine.classify(tool, args).tier is Tier.BLACK, (tool, args)


@pytest.mark.parametrize("tool,args", [
    # Prose that MENTIONS a protected site is not a visit to it.
    ("send_telegram_message", {"to": "Saved Messages", "text": "I paid with click.uz yesterday"}),
    ("send_telegram_message", {"to": "Saved Messages",
                               "text": "Receipt from www.paypal.com attached, all good"}),
    # A search ABOUT the site goes to Google, not to the site.
    ("open_url", {"url": "https://www.google.com/search?q=is+www.paypal.com+down"}),
    # Near-misses that are different hosts entirely.
    ("open_url", {"url": "https://notpaypal.com"}),
    ("open_url", {"url": "https://clickup.com"}),
    ("web_read", {"url": "https://huggingface.co/datasets"}),
])
def test_things_that_only_mention_a_protected_site_are_not_refused(engine, tool, args):
    assert engine.classify(tool, args).tier is not Tier.BLACK, (tool, args)


def test_an_unparseable_address_is_refused_not_waved_through(engine):
    # urlsplit raises ValueError on an unclosed IPv6 literal.
    assert engine.classify("open_url", {"url": "http://[::1"}).tier is Tier.BLACK


def test_the_browser_can_ask_about_where_a_page_actually_landed(engine):
    """
    The typed URL is not where a redirect ends. browse_to re-checks the
    FINAL address with this, after the page has loaded.
    """
    assert engine.protected_domain("https://www.binance.com/en")
    assert engine.protected_domain("paypal.com")
    assert engine.protected_domain("https://example.com/?next=paypal.com") is None
    assert engine.protected_domain("") is None


def test_password_managers_are_still_refused_by_name(engine):
    """Unchanged: the app list is not part of this fix."""
    assert engine.classify("open_app", {"name": "Bitwarden"}).tier is Tier.BLACK
