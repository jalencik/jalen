"""
Phase 4: the credentials vault and per-site permission.

He chose this shape from a poll — vault, allowlist, audit — and added the
part that makes it usable: Jalen asks whether an approval is for THIS TIME
or FOREVER, remembers the answer, and acts on it.

The threat model is worth stating, because it is not him. A web page can lie
about being a login form, and Jalen cannot tell a real Google sign-in from a
convincing clone. Every test below defends that boundary: an unfamiliar
domain gets nothing, a suffix is not a match, and a secret never leaves the
process as a tool result.
"""
from __future__ import annotations

import json

import pytest

from jarvis.tools import vault


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch a real vault or a real approvals file."""
    monkeypatch.setattr(vault, "VAULT_PATH", tmp_path / "vault.json")
    monkeypatch.setattr(vault, "APPROVALS_PATH", tmp_path / "approvals.json")
    vault.lock_vault()
    yield
    vault.lock_vault()


# ------------------------------------------------------------- encryption
def test_a_sealed_vault_round_trips():
    secrets = {"sign_in_code": "Jalol2009applicant***", "phone": "+998 90 000 0000"}
    blob = vault._seal(secrets, "correct horse battery")
    assert vault._open(blob, "correct horse battery") == secrets


def test_the_wrong_passphrase_returns_nothing_rather_than_garbage():
    blob = vault._seal({"a": "b"}, "right")
    assert vault._open(blob, "wrong") is None


def test_the_secret_is_not_in_the_file_in_plain_text(tmp_path):
    """
    The whole reason this is not .env. A vault whose secret is greppable is
    a filename, not encryption.
    """
    vault._save_blob(vault._seal({"sign_in_code": "SuperSecret123"}, "pass"))
    raw = vault.VAULT_PATH.read_text(encoding="utf-8")
    assert "SuperSecret123" not in raw
    assert "pass" not in raw, "the passphrase itself leaked into the file"


def test_tampering_is_detected_not_decrypted():
    """
    Without the HMAC, a flipped byte decrypts to garbage that gets typed
    into a login form. With it, the vault refuses to open.
    """
    blob = vault._seal({"a": "b"}, "pass")
    blob["data"] = blob["data"][:-4] + "AAAA"
    assert vault._open(blob, "pass") is None


def test_two_vaults_with_the_same_passphrase_differ():
    """A fresh salt each time, so identical contents are not identical files."""
    a = vault._seal({"x": "y"}, "same")
    b = vault._seal({"x": "y"}, "same")
    assert a["salt"] != b["salt"]
    assert a["data"] != b["data"]


# ---------------------------------------------------------------- session
def test_secrets_are_unavailable_until_unlocked():
    vault._save_blob(vault._seal({"sign_in_code": "abc"}, "pass"))
    with pytest.raises(vault.VaultLocked):
        vault.get_secret("sign_in_code")


def test_unlocking_makes_them_available_and_locking_removes_them():
    vault._save_blob(vault._seal({"sign_in_code": "abc"}, "pass"))
    assert "unlocked" in vault.unlock_vault("pass")
    assert vault.get_secret("sign_in_code") == "abc"

    vault.lock_vault()
    with pytest.raises(vault.VaultLocked):
        vault.get_secret("sign_in_code")


def test_an_expired_unlock_stops_working(monkeypatch):
    """
    A laptop left open must not stay unlocked all day. This is the only
    protection against someone who walks up to an unattended machine.
    """
    vault._save_blob(vault._seal({"a": "b"}, "pass"))
    vault.unlock_vault("pass")
    assert vault.get_secret("a") == "b"

    monkeypatch.setattr(
        vault.time, "monotonic", lambda: vault._SESSION.until + 1.0
    )
    with pytest.raises(vault.VaultLocked):
        vault.get_secret("a")


def test_a_failed_unlock_does_not_say_which_part_was_wrong():
    """
    "Wrong passphrase" and "corrupt file" are different, and telling them
    apart out loud helps somebody guessing.
    """
    vault._save_blob(vault._seal({"a": "b"}, "pass"))
    assert vault.unlock_vault("nope") == "That didn't unlock it."


def test_listing_shows_names_never_values():
    """
    A tool result reaches the model, the transcript window, the audit log
    and possibly the speakers. Names are useful there; values are not.
    """
    vault._save_blob(vault._seal({"sign_in_code": "TOPSECRET", "phone": "12345"}, "pass"))
    vault.unlock_vault("pass")
    listed = vault.list_secrets()
    assert "sign_in_code" in listed and "phone" in listed
    assert "TOPSECRET" not in listed and "12345" not in listed


def test_get_secret_is_not_exposed_as_a_tool():
    """
    THE LOAD-BEARING ONE. If get_secret were registered, the model could
    call it and the password would land in the transcript. Secrets go
    straight to the code that types them and nowhere else.
    """
    assert "get_secret" not in vault.REGISTRY
    from jarvis import tools

    assert "get_secret" not in tools.REGISTRY


# ------------------------------------------------------------- approvals
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://accounts.google.com/signin", "accounts.google.com"),
        ("HTTPS://Accounts.Google.COM:443/x?y=1", "accounts.google.com"),
        ("accounts.google.com", "accounts.google.com"),
        ("http://user:pw@commonapp.org/apply", "commonapp.org"),
        ("", ""),
    ],
)
def test_domains_normalise(raw, expected):
    assert vault._normalise_domain(raw) == expected


def test_an_unknown_site_defaults_to_asking():
    """Not a failure — the design. An unfamiliar page gets nothing."""
    assert vault.site_permission("https://something-new.example") == "ask"


def test_a_lookalike_domain_does_not_inherit_approval():
    """
    THE PHISHING TEST. Approving google.com must not approve
    "login.google.com.evil.tld", which is exactly the trick a phishing
    domain uses. Suffix matching here would hand over the password.
    """
    vault.remember_site_decision("google.com", "always")
    assert vault.site_permission("google.com") == "always"
    for lookalike in (
        "login.google.com.evil.tld",
        "google.com.attacker.io",
        "googIe.com",
        "not-google.com",
    ):
        assert vault.site_permission(lookalike) == "ask", (
            f"{lookalike} inherited approval from google.com"
        )


def test_always_and_never_are_both_remembered():
    vault.remember_site_decision("commonapp.org", "always")
    vault.remember_site_decision("sketchy.example", "never")
    assert vault.site_permission("https://commonapp.org/apply") == "always"
    assert vault.site_permission("https://sketchy.example/login") == "never"


def test_once_is_deliberately_not_storable():
    """
    His three-way choice: once, always, never. "Once" is not written down —
    that is what makes it once, and a tool that accepted it would quietly
    turn a single approval into a standing one.
    """
    reply = vault.remember_site_decision("example.com", "once")
    assert "isn't remembered" in reply
    assert vault.site_permission("example.com") == "ask"


def test_a_decision_can_be_taken_back():
    vault.remember_site_decision("example.com", "always")
    assert "Forgotten" in vault.forget_site_decision("example.com")
    assert vault.site_permission("example.com") == "ask"


def test_decisions_are_auditable():
    """He should be able to ask what he has agreed to and get a real answer."""
    vault.remember_site_decision("commonapp.org", "always")
    vault.remember_site_decision("sketchy.example", "never")
    listed = vault.list_site_decisions()
    assert "commonapp.org" in listed
    assert "sketchy.example" in listed
    assert "Always fill" in listed and "Never touch" in listed


def test_decisions_survive_a_restart():
    vault.remember_site_decision("commonapp.org", "always")
    stored = json.loads(vault.APPROVALS_PATH.read_text(encoding="utf-8"))
    assert stored["commonapp.org"]["decision"] == "always"
    assert "decided_at" in stored["commonapp.org"], "no record of when he agreed"


# ----------------------------------------------------------- reachability
def test_tiers_match_what_each_tool_does():
    from jarvis import tools
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    expected = {
        "vault_status": Tier.GREEN,
        "lock_vault": Tier.GREEN,
        "list_secrets": Tier.GREEN,
        "site_permission": Tier.GREEN,
        "list_site_decisions": Tier.GREEN,
        "unlock_vault": Tier.RED,
        "remember_site_decision": Tier.RED,
        "forget_site_decision": Tier.RED,
    }
    for name, tier in expected.items():
        assert name in tools.REGISTRY, f"{name} is not dispatchable"
        assert engine.classify(name, {}).tier is tier, f"{name} has the wrong tier"
