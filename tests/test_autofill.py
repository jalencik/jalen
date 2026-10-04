"""
Typing his details into a form, including the ones from the vault.

The piece Phase 4 was missing: the vault stored secrets and the approval
system remembered which sites he trusted, and nothing could actually TYPE
anything. All gate, no action.

Two properties carry the safety here, and both are about what Jalen does
NOT do:

  It never chooses the field. He clicks the box; Jalen types into it. A
  "find the password box" implementation on Chrome is really Tab-and-hope,
  and Tab-and-hope already typed into YouTube's search box and wiped it.

  It never trusts the page about which page it is. The domain comes from
  Chrome's own address bar, which a phishing page cannot forge.
"""
from __future__ import annotations

import pytest

from jalen.tools import autofill, interaction, vault


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "VAULT_PATH", tmp_path / "vault.json")
    monkeypatch.setattr(vault, "APPROVALS_PATH", tmp_path / "approvals.json")
    monkeypatch.setattr(vault, "SECRET_SITES_PATH", tmp_path / "secret_sites.json")
    vault.lock_vault()
    interaction.uninstall()
    typed: list[str] = []
    monkeypatch.setattr(autofill, "_type", lambda text: typed.append(text) or True)
    # Never his real screen: an unpatched test would read whichever window
    # is in front of him right now.
    _looking_at(monkeypatch, "")
    yield typed
    interaction.uninstall()
    vault.lock_vault()


def _looking_at(monkeypatch, url: str) -> None:
    """The browser window in front of him is showing `url`."""
    monkeypatch.setattr(autofill, "_focused_page",
                        lambda: (1, url) if url else (0, ""))
    monkeypatch.setattr(autofill, "_read_url", lambda: url)


class _Said:
    def __init__(self, outcome: str) -> None:
        self.outcome, self.words = outcome, outcome

    def __bool__(self) -> bool:
        return self.outcome == "yes"


def _he_answers(outcome: str) -> list[str]:
    """He answers the tool's own question. Returns what he was asked."""
    asked: list[str] = []
    interaction.install_confirm(lambda q: asked.append(q) or _Said(outcome))
    return asked


def _unlocked(secrets):
    vault._save_blob(vault._seal(secrets, "pass"))
    vault.unlock_vault("pass")


# ------------------------------------------------------------ the URL gate
def test_nothing_is_typed_when_the_page_is_unknown(monkeypatch, isolated):
    """
    An unreadable address bar means an unknown domain, and an unknown domain
    is not a trusted one. Typing anyway would be filling a password into a
    page nobody identified.
    """
    _looking_at(monkeypatch, "")
    _unlocked({"sign_in_code": "SECRET"})
    reply = autofill.fill_credential("sign_in_code")
    assert "can't read which page" in reply
    assert isolated == [], "it typed into an unidentified page"


def test_a_blocked_site_is_refused(monkeypatch, isolated):
    _looking_at(monkeypatch, "https://sketchy.example/login")
    vault.remember_site_decision("sketchy.example", "never")
    _unlocked({"sign_in_code": "SECRET"})
    asked = _he_answers("yes")
    reply = autofill.fill_credential("sign_in_code")
    assert "never" in reply and "sketchy.example" in reply
    assert isolated == []
    assert asked == [], "a site he blocked is not a question"


def test_an_unapproved_site_asks_rather_than_typing(monkeypatch, isolated):
    """
    His three-way choice, enforced in the tool rather than left to the
    model's judgement - and now ASKED by the tool too, naming the site it
    read, because a flag the model set after asking in its own words was
    the whole approval (tests/test_a_secret_goes_only_where_he_said.py).
    """
    _looking_at(monkeypatch, "https://newsite.example/apply")
    _unlocked({"sign_in_code": "SECRET"})
    vault.tie_secret("sign_in_code", ["*"])
    asked = _he_answers("no")
    reply = autofill.fill_credential("sign_in_code")
    assert len(asked) == 1
    assert "newsite.example" in asked[0] and "just this once" in asked[0]
    assert "nothing typed" in reply.lower()
    assert isolated == []


def test_just_this_once_types_but_records_nothing(monkeypatch, isolated):
    _looking_at(monkeypatch, "https://newsite.example/apply")
    _unlocked({"sign_in_code": "SECRET"})
    vault.tie_secret("sign_in_code", ["*"])
    _he_answers("yes")

    reply = autofill.fill_credential("sign_in_code")
    assert isolated == ["SECRET"]
    assert "just this once" in reply
    # Nothing written down — that is what makes it once.
    assert vault.site_permission("newsite.example") == "ask"


def test_an_always_approved_site_types_without_asking(monkeypatch, isolated):
    _looking_at(monkeypatch, "https://commonapp.org/apply")
    vault.remember_site_decision("commonapp.org", "always")
    _unlocked({"sign_in_code": "SECRET"})
    vault.tie_secret("sign_in_code", ["commonapp.org"])
    asked = _he_answers("no")

    reply = autofill.fill_credential("sign_in_code")
    assert isolated == ["SECRET"]
    assert "as agreed" in reply
    assert asked == []


def test_a_lookalike_domain_does_not_inherit_approval(monkeypatch, isolated):
    """
    THE PHISHING CASE, end to end. Approving google.com must not let a
    password be typed into "login.google.com.evil.tld".
    """
    vault.remember_site_decision("google.com", "always")
    _looking_at(monkeypatch, "https://login.google.com.evil.tld/signin")
    _unlocked({"sign_in_code": "SECRET"})

    reply = autofill.fill_credential("sign_in_code")
    assert "login.google.com.evil.tld" in reply
    assert isolated == [], "a password was typed into a lookalike domain"


# -------------------------------------------------------------- the secret
def test_the_secret_never_appears_in_the_reply(monkeypatch, isolated):
    """
    The reply reaches the model, the transcript window, the audit log and
    possibly the speakers. The value must not be in it.
    """
    _looking_at(monkeypatch, "https://commonapp.org/apply")
    vault.remember_site_decision("commonapp.org", "always")
    _unlocked({"sign_in_code": "Jalol2009applicant***"})
    vault.tie_secret("sign_in_code", ["commonapp.org"])

    reply = autofill.fill_credential("sign_in_code")
    assert isolated == ["Jalol2009applicant***"]
    assert "Jalol2009applicant" not in reply
    assert "sign_in_code" in reply, "he isn't told WHICH secret went in"


def test_a_locked_vault_says_so(monkeypatch, isolated):
    _looking_at(monkeypatch, "https://commonapp.org/apply")
    vault.remember_site_decision("commonapp.org", "always")
    vault._save_blob(vault._seal({"sign_in_code": "SECRET"}, "pass"))

    reply = autofill.fill_credential("sign_in_code")
    assert "locked" in reply
    assert isolated == []


def test_an_unknown_secret_lists_what_there_is(monkeypatch, isolated):
    _looking_at(monkeypatch, "https://commonapp.org/apply")
    vault.remember_site_decision("commonapp.org", "always")
    _unlocked({"phone": "12345"})

    reply = autofill.fill_credential("passport")
    assert "nothing stored called" in reply
    assert "phone" in reply
    assert "12345" not in reply, "the VALUE leaked while listing names"


# ------------------------------------------------------------------ typing
@pytest.mark.parametrize(
    "raw",
    ["Jalol2009applicant***", "a+b^c%d~e", "{braces}", "(parens)", "[brackets]"],
)
def test_syntax_characters_are_escaped(raw):
    """
    SendKeys treats {}()+^%~ as syntax. His sign-in code ends in three
    asterisks and could contain any of these; unescaped they become modifier
    keys and the password silently arrives WRONG — which looks exactly like
    a wrong password.
    """
    escaped = autofill._escape(raw)
    for char in "{}()+^%~[]":
        if char in raw:
            assert "{" + char + "}" in escaped, f"{char!r} was not escaped"


def test_plain_text_is_not_mangled():
    assert autofill._escape("Jaloliddin Musaev") == "Jaloliddin Musaev"


def test_empty_text_is_refused(isolated):
    assert "Nothing to type" in autofill.fill_field("   ")
    assert isolated == []


def test_ordinary_text_goes_through(isolated):
    reply = autofill.fill_field("Jaloliddin Musaev")
    assert isolated == ["Jaloliddin Musaev"]
    assert "Jaloliddin Musaev" in reply


def test_tab_is_its_own_tool_not_a_side_effect():
    """
    A Tab that fires when he did not expect it is how text lands in the
    wrong box — the exact failure play_on_youtube used to produce.
    """
    import inspect

    assert "Tab" not in inspect.getsource(autofill.fill_field)
    assert "Tab" in inspect.getsource(autofill.next_field)


# ------------------------------------------------------------ reachability
def test_tiers():
    """
    fill_credential is AMBER, not RED: the real gate is the per-domain check
    inside it, and RED would ask out loud on every field even for a site he
    approved forever — the friction the allowlist exists to remove.
    """
    from jalen import tools
    from jalen.brain.tools import TOOL_SPECS
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    expected = {
        "current_page_url": Tier.GREEN,
        "fill_field": Tier.GREEN,
        "next_field": Tier.GREEN,
        "fill_credential": Tier.AMBER,
    }
    for name, tier in expected.items():
        assert name in tools.REGISTRY and name in TOOL_SPECS
        assert engine.classify(name, {}).tier is tier


def test_jalen_never_picks_the_field():
    """
    The contract. If this module ever gains a "find the password box"
    routine, it becomes Tab-and-hope and can type into the wrong one.
    """
    import inspect

    source = inspect.getsource(autofill)
    for forbidden in ("find_element", "search_field", "locate_input"):
        assert forbidden not in source
