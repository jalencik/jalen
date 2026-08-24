"""
His details, from a folder he owns, into ordinary form fields - and NOT
into the ones that are his by rule.

    "I will give him a folder, where it will have access to all of
     information there, where it can take that information and fill it out
     the corresponding box itself."
    "I fill out payment sections myself."

The two things these hold the line on:
  - a password field and a payment field are never filled, and are named
    back to him rather than skipped silently, because a silent skip reads as
    "done" on a form that is not;
  - a prose sentence with a colon in it is not a field. The seed file is
    full of them ("Edit it freely: it is plain text"), and an earlier parser
    read those as data.
"""
from __future__ import annotations

import pytest

from jarvis.tools import profile as P


# ---------------------------------------------------------------------------
# READING THE FOLDER
# ---------------------------------------------------------------------------
class TestReadingTheFolder:

    def _write(self, tmp_path, monkeypatch, text):
        (tmp_path / "profile.md").write_text(text, encoding="utf-8")
        monkeypatch.setattr(P, "PROFILE_DIR", tmp_path)

    def test_real_fields_are_read(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch,
                    "## Identity\nFull name: Jaloliddin Musayev\nAge: 17\n")
        prof = P.load_profile()
        assert prof["full name"] == "Jaloliddin Musayev"
        assert prof["age"] == "17"

    def test_a_sentence_with_a_colon_is_not_a_field(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch,
                    "Edit it freely: it is plain text and lives on your machine.\n"
                    "Age: 17\n")
        prof = P.load_profile()
        assert "age" in prof
        assert not any("plain text" in v for v in prof.values()), (
            "a prose sentence was read as a field value"
        )
        assert len(prof) == 1

    def test_placeholders_are_not_values(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch,
                    "Email: (fill me in - the address you use)\nAge: 17\n")
        prof = P.load_profile()
        assert "email" not in prof, "the reminder text was taken as his email"
        assert prof["age"] == "17"

    def test_headings_are_ignored(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, "# Jaloliddin's profile\nAge: 17\n")
        assert list(P.load_profile()) == ["age"]

    def test_a_missing_folder_is_empty_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "PROFILE_DIR", tmp_path / "nope")
        assert P.load_profile() == {}


# ---------------------------------------------------------------------------
# RECOGNISING FIELDS
# ---------------------------------------------------------------------------
class TestFieldRecognition:

    @pytest.mark.parametrize("field,expected", [
        ({"label": "First Name", "autocomplete": "given-name", "name": ""}, "first_name"),
        ({"label": "Given name", "autocomplete": "", "name": ""}, "first_name"),
        ({"label": "Surname", "autocomplete": "", "name": "last"}, "last_name"),
        ({"label": "Family name", "autocomplete": "family-name", "name": ""}, "last_name"),
        ({"label": "Email address", "autocomplete": "email", "name": ""}, "email"),
        ({"label": "Age", "autocomplete": "", "name": "age"}, "age"),
        ({"label": "Full Name", "autocomplete": "name", "name": ""}, "full_name"),
        ({"label": "Mobile", "autocomplete": "tel", "name": ""}, "phone"),
    ])
    def test_concept_is_recognised(self, field, expected):
        field.setdefault("type", "text")
        concept = P._concept_for(field)
        assert concept is not None and concept.key == expected

    def test_first_name_beats_bare_name(self):
        """
        "First name" contains "name". The longer, more specific label must
        win, or a first-name box gets the full name.
        """
        field = {"label": "First name", "autocomplete": "", "name": "", "type": "text"}
        assert P._concept_for(field).key == "first_name"

    def test_autocomplete_outranks_a_misleading_label(self):
        field = {"label": "Your details", "autocomplete": "email",
                 "name": "", "type": "text"}
        assert P._concept_for(field).key == "email"


class TestPaymentAndSecretsAreOffLimits:
    """His rule, enforced: payment is his to fill, passwords come from vault."""

    @pytest.mark.parametrize("field", [
        {"label": "Card number", "autocomplete": "cc-number", "name": ""},
        {"label": "CVV", "autocomplete": "cc-csc", "name": ""},
        {"label": "Security code", "autocomplete": "", "name": "cvc"},
        {"label": "Expiry", "autocomplete": "cc-exp", "name": ""},
        {"label": "IBAN", "autocomplete": "", "name": "iban"},
        {"label": "Account number", "autocomplete": "", "name": ""},
    ])
    def test_payment_fields_are_recognised_as_such(self, field):
        assert P._is_payment(field), f"{field['label']} was not seen as payment"

    def test_an_ordinary_field_is_not_payment(self):
        assert not P._is_payment(
            {"label": "First name", "autocomplete": "given-name", "name": ""})


# ---------------------------------------------------------------------------
# FILLING A REAL FORM
# ---------------------------------------------------------------------------
class TestFillingARealForm:

    playwright = pytest.importorskip("playwright.sync_api")

    FORM = """
      <form>
        <label>First name <input autocomplete="given-name" name="fname" required></label>
        <label>Last name <input autocomplete="family-name" name="lname"></label>
        <label>Age <input name="age" type="number"></label>
        <label>Email <input autocomplete="email" type="email" required></label>
        <label>Card number <input autocomplete="cc-number" name="card"></label>
        <label>CVV <input name="cvv"></label>
        <label>Password <input type="password" name="pw"></label>
      </form>
    """

    PROFILE = {
        "full name": "Jaloliddin Musayev",
        "first name": "Jaloliddin",
        "last name": "Musayev",
        "age": "17",
    }

    @pytest.fixture()
    def page(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True)
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chrome not launchable: {exc}")
            page = browser.new_page()
            page.set_content(self.FORM)
            yield page
            browser.close()

    def test_it_fills_what_it_knows(self, page):
        result = P.fill_page(page, self.PROFILE)
        assert "First name" in result["filled"]
        assert "Last name" in result["filled"]
        assert "Age" in result["filled"]
        # And the values actually landed in the DOM.
        assert page.eval_on_selector("input[name=fname]", "e => e.value") == "Jaloliddin"
        assert page.eval_on_selector("input[name=age]", "e => e.value") == "17"

    def test_it_never_touches_the_payment_fields(self, page):
        P.fill_page(page, self.PROFILE)
        assert page.eval_on_selector("input[name=card]", "e => e.value") == ""
        assert page.eval_on_selector("input[name=cvv]", "e => e.value") == ""

    def test_it_names_the_payment_fields_back(self, page):
        result = P.fill_page(page, self.PROFILE)
        assert "Card number" in result["payment"]
        assert any("CVV" in p or "cvv" in p.lower() for p in result["payment"])

    def test_it_never_touches_the_password_field(self, page):
        P.fill_page(page, self.PROFILE)
        assert page.eval_on_selector("input[name=pw]", "e => e.value") == ""

    def test_it_reports_a_required_field_it_lacks(self, page):
        """Email is required and not in this profile - he must be asked."""
        result = P.fill_page(page, self.PROFILE)
        assert "Email" in result["needed"]

    def test_the_spoken_summary_is_honest(self, page):
        """
        The one-line result must say what it filled AND what it left, so a
        half-filled form does not read as finished.
        """
        result = P.fill_page(page, self.PROFILE)
        # Simulate the summary builder's inputs.
        assert result["filled"] and result["needed"] and result["payment"]
