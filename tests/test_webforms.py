"""
Filling in a real form, against a real browser.

    "it cannot click fields, ask informatino from mee if needed, work without
     becoming idle... not being able to copy paste files, or pdfs if necessary"

From his own log, the moment this exists to end:

    He:    "Yes, go on filling out my application."
    Jalen: "The EarthPrize site is open, Boss. Go ahead and navigate to the
            application page and click the first field you want filled -
            I'll take it from there one at a time."

That answer was honest at the time: `autofill.py` drives the DESKTOP with
keystrokes, where finding a field is Tab-and-hope, and Tab-and-hope has
already typed into YouTube's search box and wiped it. Refusing was correct.

Inside Jalen's own Playwright browser it is not a guess. `input[type=password]`
IS the password box, the way `<a>` is a link. So the invariant that mattered -
**Jalen never chooses which field a secret is typed into** - is not being
relaxed; it is being satisfied by a mechanism that can actually satisfy it.

Everything below runs against a real Chrome and a fixture shaped like the
awkward parts of a real form: four different ways of labelling a field, an
"Email" next to a "Confirm email", validation that only appears after
submitting, and a sign-up variant with two password boxes.
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

import pytest

from jarvis.tools import webagent as wa
from jarvis.tools import webforms as wf

playwright = pytest.importorskip("playwright.sync_api")

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# SERVED OVER HTTP, not opened as file://.
#
# Not fussiness: a file:// page has no HOST, and the whole password path is
# built on host approval - "only on a host the vault has an explicit approval
# for, matched exactly". Testing that against a URL with no host would prove
# nothing about the check that matters.
_SERVER = {"port": None}


@pytest.fixture(scope="module", autouse=True)
def http_server():
    import functools
    import http.server
    import socketserver

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(FIXTURE_DIR))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    _SERVER["port"] = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True,
                              name="fixture-http")
    thread.start()
    yield
    httpd.shutdown()


def _url(**params) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    base = f"http://127.0.0.1:{_SERVER['port']}/fake_form.html"
    return base + (f"?{query}" if query else "")


@pytest.fixture()
def form():
    """
    The real session, on the real browser thread, pointed at the fixture.

    Not a mock. Every bug this module could have - a selector that matches
    nothing, a label that is not where you expect, a fill() that silently
    does nothing on a contenteditable - only exists against a real DOM.
    """
    session = wa._Session.get()

    def open_at(url):
        session.do(lambda page: page.goto(url, wait_until="domcontentloaded"))

    try:
        open_at(_url())
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"browser not launchable: {exc}")
    yield open_at
    session.stop()


# ---------------------------------------------------------------------------
# READING THE FORM — what makes "ask him for what's missing" possible
# ---------------------------------------------------------------------------
def test_it_finds_fields_labelled_four_different_ways(form):
    """
    Real forms label fields inconsistently. A scanner that only handles
    <label for> works on demos and nothing else.
    """
    out = wf.inspect_form()
    assert "Full name" in out            # <label for>
    assert "Country of residence" in out  # aria-label
    assert "Your project title" in out    # placeholder only
    assert "referee_email" in out         # name attribute, nothing else


def test_it_says_which_required_fields_are_still_empty(form):
    """
    This is the whole point. Without it the only honest thing Jalen can say
    is "click a field and I'll type into it", which is what it used to say.
    """
    out = wf.inspect_form()
    assert "Still needed:" in out
    assert "Full name" in out.split("Still needed:")[1]


def test_it_marks_the_password_field_as_off_limits(form):
    out = wf.inspect_form()
    assert "only fill this from your vault" in out


# ---------------------------------------------------------------------------
# FILLING IT
# ---------------------------------------------------------------------------
def test_it_fills_the_field_he_named(form):
    assert "Filled" in wf.fill_form_field("Full name", "Jaloliddin Musaev")
    assert "Jaloliddin Musaev" in wf.inspect_form() or "[x] Full name" in wf.inspect_form()


def test_email_does_not_land_in_confirm_email(form):
    """
    "Email" is a substring of "Confirm email". A matcher that takes the first
    hit puts the address in the wrong box, and the form fails validation
    later for a reason nobody can see.
    """
    wf.fill_form_field("Email", "me@example.com")
    wf.fill_form_field("Confirm email", "me@example.com")
    values = wa._Session.get().do(lambda page: (
        page.locator("#email").input_value(),
        page.locator("#email2").input_value(),
    ))
    assert values == ("me@example.com", "me@example.com")


def test_an_unknown_field_lists_what_is_actually_there(form):
    out = wf.fill_form_field("national insurance number", "x")
    assert "no field called" in out
    assert "Full name" in out, "it should say what it CAN see"


def test_it_refuses_to_type_into_a_password_box(form):
    """
    A model that can write into a password box by name is one
    prompt-injection away from being asked to.
    """
    out = wf.fill_form_field("Password", "hunter2")
    assert "only fill those from your vault" in out
    typed = wa._Session.get().do(lambda page: page.locator("#pw").input_value())
    assert typed == "", "it typed into the password box anyway"


def test_it_can_choose_from_a_dropdown(form):
    assert "Filled" in wf.fill_form_field("Category", "Climate")
    chosen = wa._Session.get().do(
        lambda page: page.locator("#cat").input_value())
    assert chosen == "Climate"


# ---------------------------------------------------------------------------
# FILES
# ---------------------------------------------------------------------------
def test_it_attaches_a_real_file(form, tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 pretend")
    assert "Attached cv.pdf" in wf.upload_to_form(str(cv))
    name = wa._Session.get().do(lambda page: page.evaluate(
        "() => document.getElementById('cv').files[0].name"))
    assert name == "cv.pdf"


def test_a_missing_file_is_reported_not_uploaded(form):
    assert "no file at" in wf.upload_to_form(r"C:\nope\missing.pdf")


def test_no_upload_box_is_said_plainly(form):
    form(_url(nofile=1))
    assert "no file upload on this page" in wf.upload_to_form(__file__)


# ---------------------------------------------------------------------------
# SUBMITTING — and believing the page, not the click
# ---------------------------------------------------------------------------
def test_submitting_reports_the_validation_it_gets_back(form):
    """
    The fixture validates only AFTER submit, like a server does. A submit
    that reports success while the page shows "the two emails do not match"
    is the failure this catches.
    """
    wf.fill_form_field("Full name", "Jaloliddin")
    wf.fill_form_field("Email", "me@example.com")
    wf.fill_form_field("Confirm email", "different@example.com")
    out = wf.submit_form()
    assert "pushed back" in out
    assert "do not match" in out


def test_a_clean_submit_is_reported_as_moving_on(form):
    wf.fill_form_field("Full name", "Jaloliddin")
    wf.fill_form_field("Email", "me@example.com")
    wf.fill_form_field("Confirm email", "me@example.com")
    wf.fill_form_field("Country of residence", "Uzbekistan")
    out = wf.submit_form()
    assert "pushed back" not in out


def test_errors_can_be_read_without_submitting_again(form):
    # Full name is filled because it is `required`: the browser's OWN
    # validation runs before the form's, and an empty required field stops
    # the page ever reaching its own checks. That is real behaviour, not a
    # fixture quirk - and it is why submit_form reports whatever it finds
    # rather than assuming which layer complained.
    wf.fill_form_field("Full name", "Jaloliddin")
    wf.fill_form_field("Email", "a@b.com")
    wf.fill_form_field("Confirm email", "c@d.com")
    wf.submit_form()
    assert "do not match" in wf.form_errors()


# ---------------------------------------------------------------------------
# THE SECRET PATH — every one of these is a refusal
# ---------------------------------------------------------------------------
def test_a_password_needs_an_explicit_approval_for_that_exact_host(form, tmp_path, monkeypatch):
    from jarvis.tools import vault

    monkeypatch.setattr(vault, "APPROVALS_PATH", tmp_path / "approvals.json")
    out = wf.fill_login_field()
    assert "don't have your approval" in out
    typed = wa._Session.get().do(lambda page: page.locator("#pw").input_value())
    assert typed == ""


def test_a_signup_form_with_two_password_boxes_is_refused(form, tmp_path, monkeypatch):
    """
    Two password boxes means "password" and "confirm password", and guessing
    which is which is exactly the guess this module refuses to make.
    """
    from jarvis.tools import vault

    monkeypatch.setattr(vault, "APPROVALS_PATH", tmp_path / "approvals.json")
    form(_url(passwords=2))
    vault.remember_site_decision(
        wa._Session.get().do(lambda page: page.url), "always")
    monkeypatch.setattr(vault, "get_secret", lambda name: "hunter2")
    out = wf.fill_login_field()
    assert "won't guess which is which" in out
    for box in ("#pw", "#pw2"):
        assert wa._Session.get().do(
            lambda page, b=box: page.locator(b).input_value()) == ""


def test_the_password_is_never_returned_to_the_caller():
    """
    A tool result reaches the model, the transcript window and the audit log.
    A password must never take that path — which is why fill_login_field
    calls vault.get_secret (internal, deliberately NOT a registered tool)
    and returns a sentence rather than a value.
    """
    import inspect

    from jarvis import tools

    assert "get_secret" not in tools.REGISTRY
    source = inspect.getsource(wf.fill_login_field)
    assert "del secret" in source
    assert "return secret" not in source
    assert 'locator(\'input[type="password"]\')' in source, (
        "it is no longer addressing the password box by type - it is guessing"
    )


def test_a_locked_vault_says_so_rather_than_failing_oddly(form, tmp_path, monkeypatch):
    from jarvis.tools import vault

    monkeypatch.setattr(vault, "APPROVALS_PATH", tmp_path / "approvals.json")
    vault.remember_site_decision(
        wa._Session.get().do(lambda page: page.url), "always")

    def locked(name):
        raise vault.VaultLocked("locked")

    monkeypatch.setattr(vault, "get_secret", locked)
    assert "vault is locked" in wf.fill_login_field().lower()


# ---------------------------------------------------------------------------
# Threading, because that is what broke delegation
# ---------------------------------------------------------------------------
def test_the_form_tools_work_from_another_thread(form):
    """
    Same failure mode that crashed web_delegate: the brain runs every tool on
    whichever pool thread is free. These go through the same session, so they
    inherit the fix — and this is the test that proves they inherited it.
    """
    out: "queue.Queue[tuple]" = queue.Queue()

    def wrap():
        try:
            out.put(("ok", wf.inspect_form()))
        except Exception as exc:  # noqa: BLE001
            out.put(("err", f"{type(exc).__name__}: {exc}"))

    thread = threading.Thread(target=wrap, name="form-caller")
    thread.start()
    thread.join(timeout=120)
    status, value = out.get(timeout=5)
    assert status == "ok", f"form tools die on another thread: {value}"
    assert "Full name" in value
