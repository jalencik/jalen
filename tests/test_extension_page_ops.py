"""
The extension's page-side logic, run against a REAL DOM.

pageOp is the function the service worker injects into a tab to read fields
and act on them. It runs in the page, so it is testable in the page: this
lifts the exact function out of service_worker.js, injects it into a real
Chrome via Playwright, and drives it against a real form. What it CANNOT test
is the native-messaging round trip through Chrome, which needs the user to
load the unpacked extension - so the field logic, which is where the bugs
live, is proven here, and the transport is proven in test_bridge.py.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

WORKER = Path(__file__).resolve().parent.parent / "browser_extension" / "service_worker.js"


def _extract_page_op() -> str:
    """The pageOp function source, verbatim, from the shipping worker file."""
    text = WORKER.read_text(encoding="utf-8")
    start = text.index("function pageOp(")
    return text[start:]      # pageOp is the last thing in the file


PAGE_OP = _extract_page_op()

FORM = """
  <h1>Application</h1>
  <form>
    <label>First name <input autocomplete="given-name" name="fname" required></label>
    <label>Email <input type="email" name="email"></label>
    <label>Country
      <select name="country"><option>Uzbekistan</option><option>Other</option></select>
    </label>
    <label><input type="checkbox" name="agree"> I agree</label>
    <label>Password <input type="password" name="pw"></label>
  </form>
  <button type="button" id="go">Continue</button>
"""


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chrome not launchable: {exc}")
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    page = browser.new_page()
    page.set_content(FORM)
    # Install pageOp once, then call it by name for each op.
    page.add_script_tag(content=PAGE_OP)
    yield page
    page.close()


def op(page, command, payload=None):
    return page.evaluate(
        "([c, p]) => pageOp(c, p)", [command, payload or {}])


class TestReading:

    def test_get_page_state(self, page):
        state = op(page, "get_page_state")
        assert state["title"] is not None
        assert "Application" in state["headings"]
        assert state["hasPasswordField"] is True
        assert state["fieldCount"] >= 4

    def test_get_form_fields_reports_semantics(self, page):
        fields = op(page, "get_form_fields")["fields"]
        by_name = {f["name"]: f for f in fields}
        assert by_name["fname"]["autocomplete"] == "given-name"
        assert by_name["fname"]["required"] is True
        assert by_name["email"]["type"] == "email"
        assert by_name["country"]["options"] == ["Uzbekistan", "Other"]

    def test_get_visible_text(self, page):
        assert "Application" in op(page, "get_visible_text")["text"]


class TestActing:

    def _index_of(self, page, name):
        fields = op(page, "get_form_fields")["fields"]
        return next(f["index"] for f in fields if f["name"] == name)

    def test_fill_by_index(self, page):
        i = self._index_of(page, "fname")
        assert op(page, "fill", {"index": i, "value": "Jaloliddin"})["filled"]
        assert page.eval_on_selector("input[name=fname]", "e => e.value") == "Jaloliddin"

    def test_fill_refuses_a_password_without_permission(self, page):
        i = self._index_of(page, "pw")
        result = op(page, "fill", {"index": i, "value": "hunter2"})
        assert result.get("__code") == "SECRET_FIELD"
        assert page.eval_on_selector("input[name=pw]", "e => e.value") == "", \
            "the password field was written to anyway"

    def test_select_an_option(self, page):
        i = self._index_of(page, "country")
        op(page, "fill", {"index": i, "value": "Other"})
        assert page.eval_on_selector("select[name=country]", "e => e.value") == "Other"

    def test_check_a_box(self, page):
        i = self._index_of(page, "agree")
        op(page, "check", {"index": i, "checked": True})
        assert page.eval_on_selector("input[name=agree]", "e => e.checked") is True

    def test_click_by_text(self, page):
        page.evaluate("document.getElementById('go').addEventListener("
                      "'click', () => window.__clicked = true)")
        op(page, "click", {"text": "Continue"})
        assert page.evaluate("window.__clicked") is True

    def test_a_missing_field_is_an_error_not_a_crash(self, page):
        result = op(page, "fill", {"selector": "#nope", "value": "x"})
        assert result.get("__code") == "NOT_FOUND"


class TestContentEditable:
    """
    ChatGPT and Gemini type into contenteditable divs, not <textarea>.

    Measured against his real signed-in ChatGPT: scanning only
    input/textarea/select found two file inputs and MISSED the message box
    entirely - so the agent could read the page and never send a prompt.
    And assigning .value to a contenteditable silently does nothing, which
    is how a "sent" prompt arrives empty.
    """

    CHAT = """
      <div id="prompt" contenteditable="true"
           data-placeholder="Message ChatGPT"></div>
      <div id="rt" role="textbox" contenteditable="true"
           aria-label="Ask Gemini"></div>
      <button id="send">Send</button>
    """

    @pytest.fixture()
    def chat(self, browser):
        page = browser.new_page()
        page.set_content(self.CHAT)
        page.add_script_tag(content=PAGE_OP)
        yield page
        page.close()

    def test_the_message_box_is_found(self, chat):
        labels = [f["label"] for f in op(chat, "get_form_fields")["fields"]]
        assert any("Message ChatGPT" in l for l in labels), labels

    def test_a_role_textbox_is_found(self, chat):
        labels = [f["label"] for f in op(chat, "get_form_fields")["fields"]]
        assert any("Ask Gemini" in l for l in labels), labels

    def test_typing_into_it_actually_lands(self, chat):
        fields = op(chat, "get_form_fields")["fields"]
        i = next(f["index"] for f in fields if "Message ChatGPT" in f["label"])
        result = op(chat, "fill", {"index": i, "value": "research PM2.5"})
        assert result.get("filled") is True
        assert chat.eval_on_selector("#prompt", "e => e.textContent") \
            == "research PM2.5", "the prompt never reached the box"

    def test_the_index_points_at_the_right_element(self, chat):
        """nth() must scan the same set fields() did, or values land wrong."""
        fields = op(chat, "get_form_fields")["fields"]
        i = next(f["index"] for f in fields if "Ask Gemini" in f["label"])
        op(chat, "fill", {"index": i, "value": "hello gemini"})
        assert chat.eval_on_selector("#rt", "e => e.textContent") == "hello gemini"
        assert chat.eval_on_selector("#prompt", "e => e.textContent") == "", \
            "it typed into the wrong box"
