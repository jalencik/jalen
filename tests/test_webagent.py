"""
The browser delegation, driven against a real browser.

    "it should be able to chat, with chatgpt like a real human, according to
     the outcome, asking my expectations and what I want, and based on the
     outcome... it should compare it against my expectations, and give it
     another prompt that will fix it"

WHAT IS REAL HERE AND WHAT IS NOT
---------------------------------
Real: Chrome, Playwright, a contenteditable box, a streaming answer, a stop
button that appears and disappears, and the actual selector lists the ChatGPT
and Gemini adapters use.

Not real: the site. Testing against chatgpt.com would need his account, would
rate-limit, and would put test prompts into his real chat history.

That trade is worth naming, because the thing most likely to break is NOT the
site — it is completion detection, and completion detection is hard for
exactly one reason: the answer arrives a word at a time. A page that answered
instantly would let a broken detector pass. tests/fixtures/fake_chat.html
streams, shows a stop button while it does, and uses the same hooks the real
adapters look for.

What this cannot prove is that chatgpt.com still uses those hooks tomorrow.
Nothing offline can. That is why every selector is an ordered candidate list
and every failure says "I couldn't find the message box" rather than raising.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from jarvis.tools import webagent as wa

playwright = pytest.importorskip("playwright.sync_api")

FIXTURE = Path(__file__).parent / "fixtures" / "fake_chat.html"


def _url(**params) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return FIXTURE.resolve().as_uri() + (f"?{query}" if query else "")


@pytest.fixture(scope="module")
def browser():
    """One headless Chrome for the whole module. Headless here only."""
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chrome not launchable: {exc}")
        yield browser
        browser.close()


@pytest.fixture()
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


# The real adapter, so the real selectors are what gets exercised.
ADAPTER = wa.CHATGPT


# ---------------------------------------------------------------------------
# Page state
# ---------------------------------------------------------------------------
def test_a_signed_in_page_is_ready(page):
    page.goto(_url())
    assert wa.page_state(page, ADAPTER) == "ready"


def test_a_signed_out_page_is_detected(page):
    page.goto(_url(signedout=1))
    assert wa.page_state(page, ADAPTER) == "signed-out"


def test_a_human_check_is_detected(page):
    page.goto(_url(challenge=1))
    assert wa.page_state(page, ADAPTER) == "challenge"


def test_a_challenge_outranks_being_signed_out(page):
    """
    A CAPTCHA on a login page shows the login controls too. Reporting that as
    merely "signed-out" would send Jalen off to type a password into a box
    that is not going to accept it.
    """
    page.goto(_url(challenge=1, signedout=1))
    assert wa.page_state(page, ADAPTER) == "challenge"


# ---------------------------------------------------------------------------
# Submitting
# ---------------------------------------------------------------------------
def test_the_prompt_actually_arrives_intact(page):
    """
    The fake page echoes the prompt back, so a send that silently truncated
    would be visible. It would otherwise look identical to one that worked —
    and a truncated brief produces an answer that fails its criteria for a
    reason nobody could diagnose.
    """
    page.goto(_url(delay=300))
    brief = "Line one of the brief\nLine two with detail"
    assert wa.submit_prompt(page, ADAPTER, brief) == ""
    ok, why = wa.wait_for_completion(page, ADAPTER, timeout=20, stable_for=0.6)
    assert ok, why
    answer = wa.read_response(page, ADAPTER)
    assert "Line one of the brief" in answer
    assert "Line two with detail" in answer


def test_a_missing_message_box_is_reported_not_raised(page):
    """
    The site redesigns and the selector stops matching. That has to become a
    sentence he can act on, not a stack trace in a log he will never read.
    """
    page.goto(_url(signedout=1))
    problem = wa.submit_prompt(page, ADAPTER, "anything")
    assert "couldn't find the message box" in problem


# ---------------------------------------------------------------------------
# COMPLETION DETECTION — the part that actually matters
# ---------------------------------------------------------------------------
def test_it_waits_for_the_whole_answer_not_the_first_words(page):
    """
    THE CENTRAL TEST.

    The answer streams a word at a time over ~1.5s. A detector that reads as
    soon as text appears gets "ANSWERING: te" and reports success. The full
    answer ends with a specific line, so a partial read cannot pass.
    """
    page.goto(_url(delay=1500))
    wa.submit_prompt(page, ADAPTER, "test the streaming")
    ok, why = wa.wait_for_completion(page, ADAPTER, timeout=30, stable_for=0.8)
    assert ok, why
    answer = wa.read_response(page, ADAPTER)
    assert answer.rstrip().endswith("indented line three"), (
        f"read the answer before it finished streaming: {answer[-60:]!r}"
    )


def test_it_does_not_report_completion_while_the_stop_button_is_up(page):
    """
    The stop button exists only while generating. Seeing it means the answer
    is still arriving, whatever the text looks like.
    """
    page.goto(_url(delay=3000))
    wa.submit_prompt(page, ADAPTER, "slow one")
    time.sleep(0.8)
    assert wa._present(page, ADAPTER.stop_button), "fixture is not streaming"
    ok, _why = wa.wait_for_completion(page, ADAPTER, timeout=2.0, stable_for=0.5)
    assert not ok, "reported finished while it was still generating"


def test_an_unfinished_answer_is_reported_honestly(page):
    """
    "If completion cannot be determined reliably: report 'External AI
    completion could not be verified.' Do NOT pretend success."
    """
    page.goto(_url(delay=6000))
    wa.submit_prompt(page, ADAPTER, "very slow")
    ok, why = wa.wait_for_completion(page, ADAPTER, timeout=1.5, stable_for=0.5)
    assert not ok
    assert "can't confirm it finished" in why
    assert "won't claim it did" in why


def test_formatting_survives_extraction(page):
    """
    inner_text, not text_content. Code blocks and lists flattened into one
    paragraph are worse than useless — they are wrong in a way that looks
    fine.
    """
    page.goto(_url(delay=300))
    wa.submit_prompt(page, ADAPTER, "formatting")
    wa.wait_for_completion(page, ADAPTER, timeout=20, stable_for=0.6)
    answer = wa.read_response(page, ADAPTER)
    assert "\n" in answer, "line breaks were flattened"
    assert "  indented line three" in answer, "indentation was lost"


def test_the_gemini_adapter_uses_the_same_machinery(page):
    """
    Both adapters share every code path; only the selector lists differ. This
    proves the contenteditable fallback works with Gemini's list too, which
    has no #prompt-textarea at all.
    """
    page.goto(_url(delay=300))
    assert wa.submit_prompt(page, wa.GEMINI, "via gemini selectors") == ""
    # Gemini's response selectors don't match this fixture; ChatGPT's do.
    # What is being proved here is the SEND path, not the read path.
    ok, _ = wa.wait_for_completion(page, ADAPTER, timeout=20, stable_for=0.6)
    assert ok
    assert "via gemini selectors" in wa.read_response(page, ADAPTER)
