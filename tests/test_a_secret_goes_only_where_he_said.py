"""
A secret goes only where HE said, and only to the site it belongs to.

Two claims, both reproduced against the code as it was before this file:

1. A ONE-TIME APPROVAL WAS NOT HIS ANSWER. fill_credential(approved_once=True)
   typed the secret into whatever page had focus. Nothing checked that he had
   been asked, that the question named the site, or that the page was still
   that site when the keys went out - the brain could simply set the flag,
   and the AMBER announcement ("fill credential. Say stop...") never named a
   site either. Worse, with Notepad or Telegram in front, the address bar
   that got "checked" belonged to a Chrome window BEHIND it, so the password
   was typed into the chat box.

2. A SECRET WAS NOT BOUND TO ITS SITE. Permission is per DOMAIN, so once any
   site was "always", every stored secret could be typed there: a lookalike
   he had once approved got his Google password, from fill_credential and
   from fill_login_field(site="accounts.google.com") alike. A one-time code
   read for Google went into whatever page was open, and "openai.com"
   anywhere in a From header - the display name included - made a mail
   OpenAI's.

Nothing here touches a real vault, a real browser, or his screen: the vault
lives in tmp_path, the page is a fake, and so is the question.
"""
from __future__ import annotations

import pytest

from jalen.tools import autofill, interaction, otp, vault
from jalen.tools import webagent as wa
from jalen.tools import webforms as wf
from jalen.tools.system import IS_WINDOWS

GOOGLE = "accounts.google.com"
LOOKALIKE = "accounts-google.evil.example"


# ---------------------------------------------------------------------------
# FAKES
# ---------------------------------------------------------------------------
class _Said:
    """What app.ConfirmAnswer looks like from the tool layer."""

    def __init__(self, outcome: str, words: str = "") -> None:
        self.outcome = outcome
        self.words = words

    def __bool__(self) -> bool:
        return self.outcome == "yes"


YES, NO = _Said("yes", "yes"), _Said("no", "no")


@pytest.fixture(autouse=True)
def vault_state(tmp_path, monkeypatch):
    """A vault, its approvals and its site bindings, all in tmp_path."""
    monkeypatch.setattr(vault, "VAULT_PATH", tmp_path / "vault.json")
    monkeypatch.setattr(vault, "APPROVALS_PATH", tmp_path / "approvals.json")
    monkeypatch.setattr(vault, "SECRET_SITES_PATH", tmp_path / "secret_sites.json",
                        raising=False)
    vault.lock_vault()
    interaction.uninstall()
    yield tmp_path
    interaction.uninstall()
    vault.lock_vault()


def _unlocked(secrets: dict) -> None:
    vault._save_blob(vault._seal(secrets, "pass"))
    vault.unlock_vault("pass")


@pytest.fixture()
def screen(monkeypatch):
    """
    The window in front of him and what gets typed into it. `typed` records
    WHERE each keystroke burst landed, because which site got it is the
    whole question.
    """
    state = {"window": 1, "url": "", "typed": []}

    def focused():
        return (state["window"], state["url"]) if state["url"] else (0, "")

    monkeypatch.setattr(autofill, "_focused_page", focused, raising=False)
    monkeypatch.setattr(autofill, "_read_url", lambda: state["url"])
    monkeypatch.setattr(autofill, "_type", lambda text: state["typed"].append(
        (state["url"], text)) or True)
    return state


def him(*answers, then=None) -> list[str]:
    """
    Install HIS side of a spoken yes/no. Records every question he was asked;
    `then` runs while he is being asked (the page moving under him).
    """
    asked: list[str] = []
    queue = list(answers)

    def confirm(question):
        asked.append(question)
        if then is not None:
            then()
        return queue.pop(0) if queue else _Said("timeout")

    interaction.install_confirm(confirm)
    return asked


class _Page:
    """Just enough of a Playwright page for fill_login_field."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.passwords: list[tuple[str, str]] = []

    def locator(self, selector: str):
        page = self

        class _Boxes:
            def count(self) -> int:
                return 1 if selector == 'input[type="password"]' else 0

            @property
            def first(self):
                return self

            def fill(self, value: str) -> None:
                page.passwords.append((page.url, value))

        return _Boxes()


@pytest.fixture()
def jalens_chrome(monkeypatch):
    """fill_login_field's browser job, run inline on one fake page."""
    page = _Page("about:blank")
    monkeypatch.setattr(wf, "_do", lambda job, *, tab, timeout=120.0: job(page))
    return page


# ===========================================================================
# CLAIM 1 - a secret goes into a site he has not approved for good ONLY after
# his own yes to a question that named that site, and only if the page is
# still that site when the keys go out.
# ===========================================================================
class TestAOneTimeApprovalIsHisAnswer:

    def test_approved_once_set_by_the_brain_types_nothing(self, screen):
        """THE CLAIM. The model sets a flag; nobody asked him anything."""
        screen["url"] = "https://newsite.example/apply"
        _unlocked({"sign_in_code": "SECRET"})

        said = autofill.fill_credential("sign_in_code", approved_once=True)

        assert screen["typed"] == [], "typed on the model's say-so alone"
        assert "SECRET" not in said

    def test_his_yes_to_a_question_naming_the_site_types_it_once(self, screen):
        screen["url"] = "https://newsite.example/apply"
        _unlocked({"sign_in_code": "SECRET"})
        vault.tie_secret("sign_in_code", ["newsite.example"])
        asked = him(YES)

        said = autofill.fill_credential("sign_in_code")

        assert len(asked) == 1 and "newsite.example" in asked[0], (
            "he was not asked, or the question did not name the site")
        assert "sign_in_code" in asked[0], "the question did not say WHAT goes in"
        assert screen["typed"] == [("https://newsite.example/apply", "SECRET")]
        assert "just this once" in said
        # Nothing written down - that is what makes it once.
        assert vault.site_permission("newsite.example") == "ask"

    @pytest.mark.parametrize("answer", [
        _Said("no", "no"),
        _Said("timeout"),
        _Said("correction", "use my other code instead"),
    ], ids=["no", "silence", "correction"])
    def test_anything_but_a_yes_types_nothing(self, screen, answer):
        screen["url"] = "https://newsite.example/apply"
        _unlocked({"sign_in_code": "SECRET"})
        vault.tie_secret("sign_in_code", ["newsite.example"])
        him(answer)

        said = autofill.fill_credential("sign_in_code")

        assert screen["typed"] == []
        assert "SECRET" not in said
        if answer.outcome == "correction":
            assert "use my other code instead" in said, (
                "his correction was dropped instead of handed back")

    def test_with_nobody_to_ask_nothing_is_typed(self, screen):
        """Text mode, a test, a Telegram turn: no voice to ask through."""
        screen["url"] = "https://newsite.example/apply"
        _unlocked({"sign_in_code": "SECRET"})
        vault.tie_secret("sign_in_code", ["newsite.example"])

        said = autofill.fill_credential("sign_in_code", approved_once=True)

        assert screen["typed"] == []
        assert "newsite.example" in said

    def test_a_page_that_moved_while_he_was_asked_gets_nothing(self, screen):
        """He said yes to newsite.example. The keys must not go to evil."""
        screen["url"] = "https://newsite.example/apply"
        _unlocked({"sign_in_code": "SECRET"})
        vault.tie_secret("sign_in_code", ["*"])
        him(YES, then=lambda: screen.update(url="https://evil.example/login"))

        said = autofill.fill_credential("sign_in_code")

        assert screen["typed"] == [], "typed into a page he never approved"
        assert "SECRET" not in said

    def test_another_window_after_the_question_gets_nothing(self, screen):
        """Same site, different window: not the field he clicked."""
        screen["url"] = "https://newsite.example/apply"
        _unlocked({"sign_in_code": "SECRET"})
        vault.tie_secret("sign_in_code", ["*"])
        him(YES, then=lambda: screen.update(window=2))

        autofill.fill_credential("sign_in_code")

        assert screen["typed"] == []

    @pytest.mark.skipif(not IS_WINDOWS, reason="reads Win32 windows")
    def test_a_window_that_is_not_the_browser_gets_nothing(self, monkeypatch):
        """
        _read_url fell back to the FIRST browser window when the one in front
        was not a browser. Notepad in front, Chrome behind it on an approved
        site: the approval was Chrome's and the password went to Notepad.
        """
        import ctypes
        import sys
        import types

        from jalen.tools import browsertabs

        chrome, notepad = 111, 222
        monkeypatch.setattr(browsertabs, "_windows",
                            lambda: [(chrome, "Common App", "Chrome")])
        monkeypatch.setattr(ctypes.windll.user32, "GetForegroundWindow",
                            lambda: notepad)

        class _Edit:
            def Exists(self, *_a):
                return True

            def GetValuePattern(self):
                return types.SimpleNamespace(Value="https://commonapp.org/apply")

        class _Control:
            def EditControl(self, searchDepth=10):
                return _Edit()

        fake = types.ModuleType("uiautomation")
        fake.ControlFromHandle = lambda hwnd: _Control()
        monkeypatch.setitem(sys.modules, "uiautomation", fake)
        typed: list[str] = []
        monkeypatch.setattr(autofill, "_type", lambda text: typed.append(text) or True)
        vault.remember_site_decision("commonapp.org", "always")
        _unlocked({"sign_in_code": "SECRET"})

        said = autofill.fill_credential("sign_in_code")

        assert typed == [], "the password went into a window that is not a browser"
        assert "can't read which page" in said

    def test_the_bridge_is_his_confirm_and_only_a_real_yes_counts(self):
        """
        The tool layer reuses app.confirm() - the same spoken yes a RED
        action gets, with its echo defence and its correction handling - and
        a truthy thing that is not a yes (a string, a correction) is a no.
        """
        from jalen.app import ConfirmAnswer

        for given, yes in ((ConfirmAnswer("yes", "yes"), True),
                           (True, True),
                           (ConfirmAnswer("correction", "the other one"), False),
                           (ConfirmAnswer("timeout"), False),
                           ("He said: no", False),
                           (None, False)):
            interaction.install_confirm(lambda q, g=given: g)
            assert interaction.confirm("Type it into x.example?").yes is yes, given
        interaction.uninstall()
        assert interaction.confirm("anything?").outcome == "unavailable"

    def test_jalen_puts_his_own_confirm_behind_the_bridge(self):
        import inspect

        from jalen.app import Jalen

        source = inspect.getsource(Jalen.__init__)
        assert "interaction.install_confirm(" in source
        assert "self.confirm(question)" in source

    def test_asking_is_not_something_the_model_can_do_or_skip(self):
        """
        confirm() is code asking HIM; as a tool, the model could ask itself.
        And the spec must stop offering the flag that used to stand in for
        his answer.
        """
        from jalen import tools
        from jalen.brain.tools import TOOL_SPECS

        for name in ("confirm", "install_confirm", "tie_secret",
                     "secret_binding", "has_secret"):
            assert name not in tools.REGISTRY
            assert name not in TOOL_SPECS
        assert "approved_once" not in TOOL_SPECS["fill_credential"][1]


# ===========================================================================
# CLAIM 2 - a secret is typed only on the site it belongs to, whatever else
# he has approved.
# ===========================================================================
class TestASecretBelongsToItsSite:

    def test_a_secret_named_for_a_site_is_not_typed_on_a_lookalike(self, screen):
        """THE CLAIM, through fill_credential. He approved the lookalike once."""
        vault.remember_site_decision(LOOKALIKE, "always")
        screen["url"] = f"https://{LOOKALIKE}/signin"
        _unlocked({GOOGLE: "HIS-GOOGLE-PASSWORD"})

        said = autofill.fill_credential(GOOGLE)

        assert screen["typed"] == [], "his Google password went to a lookalike"
        assert "HIS-GOOGLE-PASSWORD" not in said

    def test_a_secret_tied_to_a_site_is_not_typed_on_another(self, screen):
        vault.remember_site_decision(LOOKALIKE, "always")
        screen["url"] = f"https://{LOOKALIKE}/signin"
        _unlocked({"gmail": "HIS-GOOGLE-PASSWORD"})
        vault.tie_secret("gmail", [GOOGLE])
        asked = him(YES)

        said = autofill.fill_credential("gmail")

        assert screen["typed"] == []
        assert asked == [], "a yes by voice must not move a secret to another site"
        assert GOOGLE in said, "he is not told where it belongs"

    def test_the_login_field_will_not_type_another_sites_password(
            self, jalens_chrome):
        """THE CLAIM, through fill_login_field(site=...)."""
        jalens_chrome.url = "https://evil.example/login"
        vault.remember_site_decision("evil.example", "always")
        _unlocked({GOOGLE: "HIS-GOOGLE-PASSWORD"})

        said = wf.fill_login_field(site=GOOGLE)

        assert jalens_chrome.passwords == [], "Google's password on evil.example"
        assert "HIS-GOOGLE-PASSWORD" not in said

    def test_the_login_field_default_needs_no_new_question(self, jalens_chrome):
        """A login saved under the page's own host is that host's. No ceremony."""
        jalens_chrome.url = f"https://{GOOGLE}/signin"
        vault.remember_site_decision(GOOGLE, "always")
        _unlocked({GOOGLE: "HIS-GOOGLE-PASSWORD"})
        asked = him()

        said = wf.fill_login_field()

        assert jalens_chrome.passwords == [(f"https://{GOOGLE}/signin",
                                            "HIS-GOOGLE-PASSWORD")]
        assert asked == []
        assert "HIS-GOOGLE-PASSWORD" not in said

    def test_an_untied_login_is_tied_on_first_use_after_his_yes(
            self, jalens_chrome):
        jalens_chrome.url = f"https://{GOOGLE}/signin"
        vault.remember_site_decision(GOOGLE, "always")
        vault.remember_site_decision("evil.example", "always")
        _unlocked({"gmail": "HIS-GOOGLE-PASSWORD"})
        asked = him(YES)

        wf.fill_login_field(site="gmail")

        assert len(asked) == 1 and GOOGLE in asked[0] and "gmail" in asked[0]
        assert jalens_chrome.passwords == [(f"https://{GOOGLE}/signin",
                                            "HIS-GOOGLE-PASSWORD")]
        assert vault.secret_binding("gmail", f"https://{GOOGLE}/x") == "here"

        # ...and from then on it is Google's, whatever else he approved.
        jalens_chrome.url = "https://evil.example/login"
        jalens_chrome.passwords.clear()
        wf.fill_login_field(site="gmail")
        assert jalens_chrome.passwords == []
        assert len(asked) == 1, "asked again instead of refusing"

    def test_the_login_page_moving_while_he_is_asked_gets_nothing(
            self, jalens_chrome):
        jalens_chrome.url = f"https://{GOOGLE}/signin"
        vault.remember_site_decision(GOOGLE, "always")
        vault.remember_site_decision("evil.example", "always")
        _unlocked({"gmail": "HIS-GOOGLE-PASSWORD", "evil.example": "OTHER"})
        him(YES, then=lambda: setattr(jalens_chrome, "url",
                                      "https://evil.example/login"))

        said = wf.fill_login_field(site="gmail")

        assert jalens_chrome.passwords == []
        assert "HIS-GOOGLE-PASSWORD" not in said and "OTHER" not in said

    def test_saying_no_to_tying_types_and_ties_nothing(self, jalens_chrome):
        jalens_chrome.url = f"https://{GOOGLE}/signin"
        vault.remember_site_decision(GOOGLE, "always")
        _unlocked({"gmail": "HIS-GOOGLE-PASSWORD"})
        him(NO)

        wf.fill_login_field(site="gmail")

        assert jalens_chrome.passwords == []
        assert vault.secret_binding("gmail", f"https://{GOOGLE}/x") == "unbound"

    def test_an_untied_secret_is_tied_on_first_use_only_after_his_yes(
            self, screen):
        """
        Secrets stored before binding existed. The first time one is about to
        be typed, he is asked a question naming it AND the site; his yes ties
        it there for good, and no other approved site can have it after.
        """
        vault.remember_site_decision("commonapp.org", "always")
        vault.remember_site_decision(LOOKALIKE, "always")
        screen["url"] = "https://commonapp.org/apply"
        _unlocked({"sign_in_code": "SECRET"})
        asked = him(YES)

        autofill.fill_credential("sign_in_code")

        assert len(asked) == 1
        assert "sign_in_code" in asked[0] and "commonapp.org" in asked[0]
        assert screen["typed"] == [("https://commonapp.org/apply", "SECRET")]

        screen["url"] = f"https://{LOOKALIKE}/apply"
        autofill.fill_credential("sign_in_code")
        assert screen["typed"] == [("https://commonapp.org/apply", "SECRET")]
        assert len(asked) == 1, "asked again instead of refusing"

    def test_a_no_to_tying_an_untied_secret_types_nothing(self, screen):
        vault.remember_site_decision("commonapp.org", "always")
        screen["url"] = "https://commonapp.org/apply"
        _unlocked({"sign_in_code": "SECRET"})
        him(NO)

        autofill.fill_credential("sign_in_code")

        assert screen["typed"] == []
        assert vault.secret_binding("sign_in_code",
                                    "https://commonapp.org/") == "unbound"

    def test_a_secret_he_marked_for_any_site_needs_no_tying(self, screen):
        """A phone number in the vault is typed into every application form."""
        vault.remember_site_decision("commonapp.org", "always")
        screen["url"] = "https://commonapp.org/apply"
        _unlocked({"phone": "+998 90 000 0000"})
        vault.tie_secret("phone", ["*"])
        asked = him()

        autofill.fill_credential("phone")

        assert screen["typed"] == [("https://commonapp.org/apply", "+998 90 000 0000")]
        assert asked == []

    @pytest.mark.parametrize("page, tied_to, where", [
        ("https://accounts.google.com/signin", "accounts.google.com", "here"),
        ("https://x.accounts.google.com/", "accounts.google.com", "here"),
        ("https://apply.commonapp.org/x", "commonapp.org", "here"),
        ("accounts.google.com/signin", "accounts.google.com", "here"),
        ("https://accounts.google.com.evil.tld/", "accounts.google.com", "elsewhere"),
        ("https://evilaccounts.google.com/", "accounts.google.com", "elsewhere"),
        ("https://google.com/", "accounts.google.com", "elsewhere"),
        ("https://accounts.google.com@evil.tld/", "accounts.google.com", "elsewhere"),
        ("https://evil.tld/?next=accounts.google.com", "accounts.google.com", "elsewhere"),
        ("about:blank", "accounts.google.com", "elsewhere"),
        ("", "accounts.google.com", "elsewhere"),
    ])
    def test_the_binding_is_the_host_never_a_substring(self, page, tied_to, where):
        vault.tie_secret("gmail", [tied_to])
        assert vault.secret_binding("gmail", page) == where

    def test_a_secret_nobody_tied_is_unbound_not_anywhere(self):
        assert vault.secret_binding("sign_in_code", "https://commonapp.org/") == "unbound"
        # A NAME that is a host is that host's, and nobody else's.
        assert vault.secret_binding(GOOGLE, f"https://{GOOGLE}/") == "here"
        assert vault.secret_binding(GOOGLE, f"https://{LOOKALIKE}/") == "elsewhere"

    def test_he_can_see_where_each_secret_goes(self):
        values = {"gmail": "VALUE-ONE", "phone": "VALUE-TWO",
                  "sign_in_code": "VALUE-THREE"}
        _unlocked(values)
        vault.tie_secret("gmail", [GOOGLE])
        vault.tie_secret("phone", ["*"])

        listed = vault.list_secrets()

        assert f"gmail (only on {GOOGLE})" in listed
        assert "phone (any site)" in listed
        assert "sign_in_code (not tied to a site yet)" in listed
        for value in values.values():
            assert value not in listed

    def test_the_binding_file_is_out_of_reach_of_every_file_tool(self):
        """
        A binding the brain could rewrite with edit_file would be no binding.
        The file's NAME is what keeps it out of reach: it matches the
        never-touch pattern *secret*, so every file tool is refused on it.
        """
        from jalen.config import CONFIG
        from jalen.safety import SafetyEngine, Tier

        engine = SafetyEngine(CONFIG)
        assert vault.SECRET_SITES_PATH.name == "secret_sites.json", (
            "renamed - check it still matches a never_touch pattern")
        real = str(vault.ROOT / "data" / "secret_sites.json")
        for tool, args in (("read_file", {"path": real}),
                           ("edit_file", {"path": real, "content": "{}"}),
                           ("create_file", {"path": real, "content": "{}"}),
                           ("move_file", {"source": real, "destination": "C:/x.json"}),
                           ("delete_file", {"path": real})):
            assert engine.classify(tool, args).tier is Tier.BLACK, tool


# ===========================================================================
# ONE-TIME CODES - read from his mail for ONE service, typed only into that
# service's own sign-in page.
# ===========================================================================
class TestALoginCodeGoesToItsOwnSignIn:

    @pytest.fixture()
    def code_page(self, monkeypatch):
        page = _Page("about:blank")
        typed: list[tuple[str, str]] = []

        class _Box:
            def click(self):
                pass

            def fill(self, value):
                typed.append((page.url, value))

        class _Session:
            def do(self, job, timeout=None, tab="adopt"):
                return job(page)

        monkeypatch.setattr(otp, "_recent_code", lambda key, minutes: ("482913", ""))
        monkeypatch.setattr(wa._Session, "get", lambda: _Session())
        monkeypatch.setattr(wa, "_first_visible",
                            lambda pg, selectors, timeout=3.0: _Box())
        page.typed = typed
        return page

    @pytest.mark.parametrize("service, url", [
        ("google", f"https://{LOOKALIKE}/challenge"),
        ("gemini", "https://evil.example/2fa"),
        ("chatgpt", "https://auth.openai.com.evil.example/mfa"),
        ("chatgpt", "https://evil.example/?return=auth.openai.com"),
    ])
    def test_a_code_is_not_typed_into_another_sites_page(self, code_page,
                                                         service, url):
        """THE CLAIM for codes: a relay phishing page asking for his code."""
        code_page.url = url

        said = otp.fill_login_code(service)

        assert code_page.typed == [], f"his {service} code went to {url}"
        assert "482913" not in said

    @pytest.mark.parametrize("service, url", [
        ("google", "https://accounts.google.com/v3/signin/challenge/totp"),
        ("chatgpt", "https://auth.openai.com/email-verification"),
    ])
    def test_a_code_still_goes_into_the_real_sign_in(self, code_page, service, url):
        code_page.url = url

        otp.fill_login_code(service)

        assert code_page.typed == [(url, "482913")]

    @pytest.mark.parametrize("sender, ok", [
        ("OpenAI <noreply@tm.openai.com>", True),
        ("openai.com <attacker@evil.example>", False),
        ("Support <help@openai.com.evil.example>", False),
        ("x@notopenai.com", False),
        ('"noreply@openai.com" <attacker@evil.example>', False),
    ])
    def test_a_mail_is_the_services_only_if_its_address_is(self, sender, ok):
        assert otp._from_trusted_sender(sender, "chatgpt") is ok


# ===========================================================================
# THE INJECTION GUARD - nothing that types a secret runs for a turn that has
# read untrusted text, whatever the posture.
# ===========================================================================
@pytest.mark.parametrize("posture", ["irreversible_only", "autonomous"])
@pytest.mark.parametrize("tool, args", [
    ("fill_credential", {"secret": "gmail"}),
    ("fill_login_field", {"site": GOOGLE}),
    ("fill_login_code", {"service": "google"}),
    ("remember_site_decision", {"url": LOOKALIKE, "decision": "always"}),
    ("unlock_vault_prompt", {}),
])
def test_no_path_that_types_a_secret_runs_for_content(tool, args, posture):
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    engine.posture = posture
    engine.paranoid = False
    assert engine.classify(tool, args, origin="content").tier is Tier.BLACK
