"""
Signing in to ChatGPT through HIS Google account, driven against real Chrome.

    "it is still not be able to sign in you know man, could you please make
     him so that he is goign to sign in he has everything but he still could
     nto ... when signing into chatgpt I do not want it to go through ghost
     mode, rather it should go thorugh my own google,
     jaloliddin2009applicant@gmail.com account and hand that task off from
     there."

WHAT THE LOG ACTUALLY SHOWED
----------------------------
data/audit.jsonl, session 6be4a562c392:

    06:33:40  "could you please sign me into ChatGPT?"
    06:33:42  tool: web_sign_in_state          <- reports, then asks
    06:34:07  "Do you already have an account, or ...?"
    06:34:27  "you can sign into it using my wallet"
    06:37:36  tool: click_element              <- DESKTOP automation
    06:37:55  "I can't find a Continue with Google button"
    06:40:19  "you'll need to tell me what's on screen"

Two defects, and the second is the interesting one:

  1. NO TOOL SIGNED IN. `web_sign_in_state` reports state and asks a
     question; the router sent the imperative "sign me in" to it, so an
     instruction got answered with a question, twice, in a loop.

  2. THE FALLBACK WAS BLIND. `click_element` and `read_screen` drive Windows
     UI Automation, which addresses a Chrome *window*. Chrome does not
     publish page DOM through UIA, so the button was genuinely not findable
     that way - while a Playwright session with full DOM access sat unused.

So these tests assert against the DOM path, with a real browser, and they
assert the STOP as hard as they assert the progress: the password box is
where Jalen's authority ends.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from jalen.config import CONFIG
from jalen.brain.router import IntentRouter
from jalen.tools import webagent as wa

playwright = pytest.importorskip("playwright.sync_api")

FIXTURE = Path(__file__).parent / "fixtures" / "fake_google_login.html"
ACCOUNT = "jaloliddin2009applicant@gmail.com"


def _url(**params) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return FIXTURE.resolve().as_uri() + (f"?{query}" if query else "")


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as pw:
        try:
            found = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chrome not launchable: {exc}")
        yield found
        found.close()


@pytest.fixture()
def page(browser):
    """
    A page where chatgpt.com and accounts.google.com are the FIXTURE.

    Served at their real hostnames on purpose. The code under test refuses
    to read a Google form until the browser is genuinely on Google's host -
    a guard that exists because chatgpt.com's own login screen carries an
    email box right beside the Continue with Google button. A fixture on
    file:// would skip straight past that guard and prove nothing about it.

    Nothing leaves the machine: every request to both hosts is fulfilled
    from the local file.
    """
    page = browser.new_page()
    body = FIXTURE.read_text(encoding="utf-8")

    def serve(route):
        route.fulfill(status=200, content_type="text/html; charset=utf-8",
                      body=body)

    page.route("https://chatgpt.com/**", serve)
    page.route("https://accounts.google.com/**", serve)
    yield page
    page.close()


def _site(**params) -> str:
    """The fixture, as chatgpt.com."""
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return "https://chatgpt.com/" + (f"?{query}" if query else "")


def _brief() -> dict:
    """
    A brief that PASSES the gate, so these tests exercise what they claim.

    Written out rather than trimmed because the gate in TaskSpec.problems()
    is real and correct: it rejected the thin version this file first used,
    and a test that routes around a working guard is testing the guard's
    absence. This is the delegation from his log, filled in properly.
    """
    return {
        "objective": "Rank the ten most powerful people in the world in 2026",
        "context": "A general knowledge request from O'ktam, who wants a "
                   "current list rather than one recalled from training data.",
        "task": "Research and rank them, one to ten.",
        "required_output": "A numbered list of ten names with a justification.",
        "success_criteria": [
            "exactly ten named individuals, ranked one to ten",
            "each entry gives a role and a one sentence justification",
            "the ranking methodology is stated explicitly",
        ],
        "constraints": ["real, verifiable positions of power only"],
        "negative_requirements": ["no fictional characters"],
    }


@pytest.fixture()
def adapter():
    """
    The REAL ChatGPT adapter, unmodified.

    Not even the URL is swapped any more: the `page` fixture serves the
    local file AT chatgpt.com, so the adapter under test is byte for byte
    the one that ships, host guard and all.
    """
    return wa.CHATGPT


# ---------------------------------------------------------------------------
# THE ROUTING BUG: an imperative is not a question
# ---------------------------------------------------------------------------
class TestSignInIsAnAction:
    """His exact words must reach a tool that DOES something."""

    @pytest.fixture(scope="class")
    def router(self):
        return IntentRouter(CONFIG)

    @pytest.mark.parametrize("said", [
        "sign me into ChatGPT",
        "sign me in to chatgpt",
        "please sign me in to ChatGPT",
        "log me into chatgpt",
        "sign me in to chatgpt with my google account",
        # Verbatim from data/audit.jsonl, 2026-08-24T06:33:40.
        "Hey Jalen, could you please sign me into ChatGPT?",
    ])
    def test_imperative_reaches_the_tool_that_signs_in(self, router, said):
        intent = router.route(said)
        assert intent is not None, f"{said!r} routed nowhere"
        assert intent.tool == "web_sign_in", (
            f"{said!r} is an instruction and reached {intent.tool!r}. "
            f"That is the loop from his log: he told it to sign him in and "
            f"got a question back."
        )

    @pytest.mark.parametrize("asked", [
        "am i signed in to chatgpt?",
        "are you signed in to gemini?",
    ])
    def test_question_still_reaches_the_reporter(self, router, asked):
        intent = router.route(asked)
        assert intent is not None
        assert intent.tool == "web_sign_in_state"

    def test_the_tool_is_actually_dispatchable(self):
        """A registry entry is the difference between a tool and a plan."""
        from jalen.tools import REGISTRY
        assert "web_sign_in" in REGISTRY
        assert "web_sign_in" in wa.REGISTRY


class TestTheAccountIsHis:
    """Not a chooser with nobody in it, and not the first tile going."""

    def test_configured_account_is_used(self):
        assert wa._google_account() == ACCOUNT

    def test_no_account_configured_asks_rather_than_guesses(self, monkeypatch):
        monkeypatch.setattr(wa, "_google_account", lambda: "")
        answer = wa.web_sign_in("chatgpt")
        assert "config" in answer.lower()
        assert "google_account" in answer

    def test_tile_selectors_target_the_address(self):
        tiles = wa._account_tile(ACCOUNT)
        assert tiles, "no way to pick him off the chooser"
        assert any(ACCOUNT in selector for selector in tiles)

    def test_unknown_agent_is_named_not_guessed(self):
        assert "ChatGPT or Gemini" in wa.web_sign_in("copilot")


# ---------------------------------------------------------------------------
# THE FLOW, AGAINST A REAL BROWSER
# ---------------------------------------------------------------------------
class TestGoogleStageDetection:
    """Reading the page correctly is the whole job; everything else follows."""

    @pytest.mark.parametrize("stage,expected", [
        ("chooser", "chooser"),
        ("email", "email"),
        ("password", "password"),
    ])
    def test_stage_is_recognised(self, page, stage, expected):
        page.goto(_site(stage=stage, account=ACCOUNT))
        assert wa.google_stage(page, ACCOUNT) == expected

    def test_signed_in_page_is_not_a_google_stage(self, page, adapter):
        page.goto(_site(stage="ready", account=ACCOUNT))
        assert wa.google_stage(page, ACCOUNT) == "none"
        assert wa.page_state(page, adapter) == "ready"

    def test_the_password_box_outranks_the_account_chip(self, page):
        """
        THE REAL BUG, measured on accounts.google.com/v3/signin/challenge/pwd:
        the password field is count=1 visible=True, and yet the page also
        shows his account as a clickable chip at the top - so _account_tile
        matched it and the stage came back "chooser" over a ready password
        box. A password field is unambiguous; the chooser screen has none.
        This builds that exact page - password box AND the account chip - and
        holds it to reading "password".
        """
        page.set_content(
            f'<div data-identifier="{ACCOUNT}" role="link">{ACCOUNT}</div>'
            f'<input type="password" name="Passwd" autofocus>'
        )
        assert wa.google_stage(page, ACCOUNT) == "password", (
            "the account chip on the password page fooled it back into "
            "'chooser' - the bug that made the spoken line go vague"
        )


class TestTheWholeFlow:

    def test_signs_in_when_the_session_is_live(self, page, adapter):
        """
        The steady state after the one-time password: no wall at all.

        This is what he gets for months once he has typed it once, and it is
        the outcome the fix exists to make ordinary.
        """
        page.goto(_site(account=ACCOUNT, nopw=1))
        result = wa._drive_google_sign_in(page, adapter, ACCOUNT, wait_s=10)
        assert result.startswith("READY|"), result
        assert page.evaluate("window.__picked") == ACCOUNT, (
            "picked the wrong tile - that is ghost mode with extra steps"
        )

    def test_picks_his_account_not_the_first_one(self, page, adapter):
        """The chooser lists a decoy first. Order must not decide this."""
        page.goto(_site(account=ACCOUNT))
        wa._drive_google_sign_in(page, adapter, ACCOUNT, wait_s=2)
        assert page.evaluate("window.__picked") == ACCOUNT

    def test_types_his_address_when_the_chooser_lacks_him(self, page, adapter):
        page.goto(_site(account=ACCOUNT, known=0))
        wa._drive_google_sign_in(page, adapter, ACCOUNT, wait_s=2)
        assert page.evaluate("window.__typed") == ACCOUNT

    def test_stops_at_the_password_box(self, page, adapter):
        """
        The boundary. It reports WAITING and says whose job it is.

        If this test ever fails because something got cleverer, that is a
        regression regardless of what it enabled.
        """
        page.goto(_site(account=ACCOUNT))
        result = wa._drive_google_sign_in(page, adapter, ACCOUNT, wait_s=2)
        assert result.startswith("WAITING|"), result
        assert "password" in result.lower()
        assert wa.google_stage(page, ACCOUNT) == "password"

    def test_never_types_into_the_password_box(self, page, adapter):
        page.goto(_site(account=ACCOUNT))
        wa._drive_google_sign_in(page, adapter, ACCOUNT, wait_s=2)
        typed = page.eval_on_selector("input[type=password]", "el => el.value")
        assert typed == "", "Jalen put something in the password box"

    def test_carries_on_by_itself_once_the_human_is_through(self, page, adapter):
        """
        "hand that task off from there."

        The human finishes; nobody says "carry on"; the code notices. Driven
        from a timer inside the page so the completion is genuinely
        asynchronous, not something the test hands it.
        """
        page.goto(_site(account=ACCOUNT))
        wa._drive_google_sign_in(page, adapter, ACCOUNT, wait_s=2)
        assert wa.google_stage(page, ACCOUNT) == "password"

        page.evaluate("setTimeout(window.finish, 400)")
        outcome = wa._await_state(page, adapter, ACCOUNT,
                                  time.monotonic() + 15)
        assert outcome == "ready", "it did not notice the sign-in completing"

    def test_challenge_is_handed_over_never_solved(self, page, adapter):
        page.goto(_site(stage="password", account=ACCOUNT))
        page.evaluate(
            "document.querySelector('#password h1').textContent = "
            "'2-Step Verification'"
        )
        assert wa.google_stage(page, ACCOUNT) == "challenge"


class TestGoogleRefusesAutomatedBrowsers:
    """
    The real reason he could never sign in, and the honest way round it.

    Measured on this machine, driving the shipping code against the real
    accounts.google.com:

        navigator.webdriver : True
        after pressing Next : accounts.google.com/v3/signin/rejected
        Google says         : "Couldn't sign you in. This browser or app may
                               not be secure. Try using a different browser."

    Playwright sets navigator.webdriver and Google declines OAuth for any
    browser carrying it. So the answer is NOT to hide the flag - that is
    defeating a security control - but to satisfy it: open a real Chrome on
    the same profile, let him sign in himself, and reattach afterwards.
    """

    def test_rejection_url_is_recognised(self, page):
        page.route(
            "https://accounts.google.com/v3/signin/rejected**",
            lambda route: route.fulfill(
                status=200, content_type="text/html",
                body="<h1>Couldn&rsquo;t sign you in</h1>"
                     "<p>This browser or app may not be secure.</p>"),
        )
        page.goto("https://accounts.google.com/v3/signin/rejected?x=1")
        assert wa.automation_rejected(page)

    def test_rejection_text_is_recognised_without_the_url(self, page):
        page.route("https://accounts.google.com/anything**",
                   lambda route: route.fulfill(
                       status=200, content_type="text/html",
                       body="<p>This browser or app may not be secure.</p>"))
        page.goto("https://accounts.google.com/anything")
        assert wa.automation_rejected(page)

    def test_an_ordinary_google_page_is_not_a_rejection(self, page):
        page.goto(_site(stage="email", account=ACCOUNT))
        assert not wa.automation_rejected(page)

    def test_chrome_is_findable_for_the_handover(self):
        """Without a real chrome.exe the honest route does not exist."""
        assert wa._chrome_exe(), "no installed Chrome found to hand over to"

    def test_rejection_triggers_the_real_chrome_handover(self, monkeypatch):
        """Refusal must lead somewhere, not just be reported."""
        monkeypatch.setattr(
            wa._Session, "do",
            lambda self, job, timeout=0: "REJECTED|")
        handed: list = []

        def _handover(adapter, wait_s):
            handed.append(adapter.key)
            return "READY|Signed in to ChatGPT."

        monkeypatch.setattr(wa, "hand_to_real_chrome", _handover)
        answer = wa.web_sign_in("chatgpt")
        assert handed == ["chatgpt"], "Google refused and nothing happened"
        assert "Signed in" in answer

    def test_the_handover_uses_the_same_profile(self):
        """
        The whole point: cookies from the real window must be the ones
        Playwright later reads. A different directory would sign him in
        somewhere Jalen never looks.
        """
        import inspect
        source = inspect.getsource(wa.hand_to_real_chrome)
        assert "PROFILE_DIR" in source
        assert "user-data-dir" in source

    def test_the_handover_never_hides_the_automation_flag(self):
        """
        A guard against the tempting wrong fix. Masking navigator.webdriver,
        or launching with the automation switches stripped to look human, is
        defeating a security control rather than satisfying one - and it is
        exactly what a future performance pass might "tidy" this into.
        """
        import ast
        import inspect

        # The MECHANISMS, not the words - the module documents the finding in
        # prose, and prose is how the next person learns why this rule
        # exists. Only executable code is searched.
        tree = ast.parse(inspect.getsource(wa))
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                node.value = ast.Constant(value="")     # drop docstrings
        code = ast.unparse(tree)

        for forbidden in ("add_init_script",
                          "Object.defineProperty",
                          "disable-blink-features",
                          "AutomationControlled",
                          "excludeSwitches",
                          "stealth"):
            assert forbidden not in code, (
                f"{forbidden!r} is executed in webagent.py - that is "
                f"bypassing Google's browser check, not satisfying it"
            )


# ---------------------------------------------------------------------------
# THE TASK THAT WAS WAITING
# ---------------------------------------------------------------------------
class TestPendingTaskResumes:
    """
    A sign-in is never the thing he asked for. It is the thing in the way.
    """

    def setup_method(self):
        wa._PENDING.clear()

    def teardown_method(self):
        wa._PENDING.clear()

    def test_blocked_delegation_is_remembered(self, monkeypatch):
        monkeypatch.setattr(wa, "_exchange",
                            lambda *a, **k: {"state": "signed-out"})
        spec = _brief()
        answer = wa.web_delegate("chatgpt", spec)
        assert "not signed in" in answer.lower()
        assert wa.pending_delegation("chatgpt").startswith("Rank the ten")

    def test_sign_in_resends_it(self, monkeypatch):
        monkeypatch.setattr(wa, "_exchange",
                            lambda *a, **k: {"state": "signed-out"})
        spec = _brief()
        wa.web_delegate("chatgpt", spec)

        sent: list = []

        def _delivered(adapter, message, criteria):
            sent.append(message)
            return {"answer": "1. ...", "finished": True, "why": "",
                    "url": "https://chatgpt.com/c/1"}

        monkeypatch.setattr(wa, "_exchange", _delivered)
        monkeypatch.setattr(
            wa._Session, "do",
            lambda self, job, timeout=0: "READY|Signed in to ChatGPT.")

        answer = wa.web_sign_in("chatgpt")
        assert sent, "signed in and then forgot what he had asked for"
        assert "powerful" in sent[0]
        assert "Signed in" in answer

    def test_nothing_pending_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(
            wa._Session, "do",
            lambda self, job, timeout=0: "READY|You're already signed in.")
        answer = wa.web_sign_in("chatgpt")
        assert "signed in" in answer.lower()

    def test_a_wall_does_not_consume_the_pending_task(self, monkeypatch):
        """Waiting at the password box must not lose the brief."""
        monkeypatch.setattr(wa, "_exchange",
                            lambda *a, **k: {"state": "signed-out"})
        wa.web_delegate("chatgpt", _brief())
        monkeypatch.setattr(
            wa._Session, "do",
            lambda self, job, timeout=0: "WAITING|Password box is yours.")
        wa.web_sign_in("chatgpt")
        assert wa.pending_delegation("chatgpt"), (
            "the brief was dropped while waiting for the password"
        )


def test_this_file_never_writes_his_real_web_chats_or_profile():
    """
    test_sign_in_resends_it completes a FAKE delegation, and web_delegate
    saves every completed one to CHATS_PATH - which was his real
    data/web_chats.json. Measured 2026-09-30: all 68 records in that file
    were this fixture ("Rank the ten most powerful people", url
    chatgpt.com/c/1), so list_web_chats and read_web_result("") reported a
    test to him as his latest delegation. And the real-Chrome tests ran on
    his signed-in data/browser_profile. conftest.py now points both at the
    session's scratch directory.
    """
    real = (wa.ROOT / "data").resolve()
    for path in (wa.CHATS_PATH, wa.PROFILE_DIR):
        assert real not in Path(path).resolve().parents, path
