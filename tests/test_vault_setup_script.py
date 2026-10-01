"""
The one thing he has to run himself — HANDOFF item 5.

data/vault.json does not exist. It cannot be created by an assistant: the
passphrase is typed with getpass and must never pass through anything but
his own keyboard. So this is the one blocking item on the whole list, and
the entire Phase 4 credential path sits behind it.

The vault's CRYPTO is covered in test_vault.py — round trip, wrong
passphrase, tampering, no plaintext on disk. What was never covered is
scripts/vault_setup.py, the interactive script wrapped around it. That is
the part he runs exactly once, and a bug in it is discovered at the worst
possible moment: with the passphrase already typed.

So it is driven here with getpass and input faked, and every branch that can
lose data is pinned. Most importantly the one that would be a disaster:

    typing the existing passphrase wrong MUST NOT destroy the vault.
"""
from __future__ import annotations

import json

import pytest

from jarvis.tools import vault
from scripts import vault_setup


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch a real vault."""
    path = tmp_path / "vault.json"
    monkeypatch.setattr(vault, "VAULT_PATH", path)
    monkeypatch.setattr(vault_setup, "VAULT_PATH", path)
    vault.lock_vault()
    yield path
    vault.lock_vault()


def drive(monkeypatch, *, secrets: list[str], typed: list[str]):
    """
    Run vault_setup.main() with a script of answers.

    `secrets` feeds getpass (passphrases and values), `typed` feeds input()
    (the "another secret?" names). Both are consumed in order, and running
    off the end raises rather than blocking — a script that asks more
    questions than the test anticipated is a change in behaviour worth
    failing on, not something to hang on.
    """
    secret_iter = iter(secrets)
    typed_iter = iter(typed)
    monkeypatch.setattr(
        vault_setup.getpass, "getpass",
        lambda prompt="": next(secret_iter, ""),
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed_iter, ""))
    return vault_setup.main()


# ---------------------------------------------------------------------------
# The happy path — the one he will actually walk.
# ---------------------------------------------------------------------------
def test_a_fresh_vault_is_created_and_opens_with_the_passphrase(isolated, monkeypatch):
    code = drive(
        monkeypatch,
        secrets=[
            "my-long-passphrase",     # choose
            "my-long-passphrase",     # confirm
            "SIGN-IN-CODE-123",       # sign_in_code
            "+998 90 000 0000",       # phone
            "",                       # email: skipped
        ],
        typed=[""],                   # no extra secrets
    )
    assert code == 0
    assert isolated.exists(), "no vault file was written"

    opened = vault._open(json.loads(isolated.read_text(encoding="utf-8")),
                         "my-long-passphrase")
    assert opened == {
        "sign_in_code": "SIGN-IN-CODE-123",
        "phone": "+998 90 000 0000",
    }, "a blank answer was stored instead of skipped"


def test_the_secret_is_not_in_the_file_in_plain_text(isolated, monkeypatch):
    """
    Belt and braces over test_vault.py's version of this: that one seals a
    dict directly, this one goes through the script he actually runs.
    """
    drive(monkeypatch,
          secrets=["a-good-passphrase", "a-good-passphrase", "PLAINTEXT-CANARY", "", ""],
          typed=[""])
    raw = isolated.read_text(encoding="utf-8")
    assert "PLAINTEXT-CANARY" not in raw
    assert "a-good-passphrase" not in raw, "the passphrase was written to disk"


def test_extra_secrets_can_be_added_by_name(isolated, monkeypatch):
    drive(
        monkeypatch,
        secrets=["a-good-passphrase", "a-good-passphrase", "", "", "", "GH-TOKEN"],
        typed=["github_token", ""],
    )
    opened = vault._open(json.loads(isolated.read_text(encoding="utf-8")),
                         "a-good-passphrase")
    assert opened == {"github_token": "GH-TOKEN"}


# ---------------------------------------------------------------------------
# Refusals. Each must write nothing at all.
# ---------------------------------------------------------------------------
def test_a_short_passphrase_is_refused_and_writes_nothing(isolated, monkeypatch):
    assert drive(monkeypatch, secrets=["short"], typed=[]) == 1
    assert not isolated.exists()


def test_a_mistyped_confirmation_is_refused_and_writes_nothing(isolated, monkeypatch):
    assert drive(monkeypatch,
                 secrets=["a-good-passphrase", "a-good-passphrasE"],
                 typed=[]) == 1
    assert not isolated.exists()


def test_a_vault_with_no_secrets_is_not_written(isolated, monkeypatch):
    """
    An empty vault is worse than none: vault_status() would report one
    exists, and every "fill my password" would then fail for a reason nobody
    could see.
    """
    assert drive(monkeypatch,
                 secrets=["a-good-passphrase", "a-good-passphrase", "", "", ""],
                 typed=[""]) == 1
    assert not isolated.exists()


# ---------------------------------------------------------------------------
# Re-running it. This is where data gets lost.
# ---------------------------------------------------------------------------
def test_rerunning_adds_to_the_vault_without_dropping_what_was_there(isolated, monkeypatch):
    drive(monkeypatch,
          secrets=["a-good-passphrase", "a-good-passphrase", "FIRST-CODE", "", ""],
          typed=[""])

    drive(monkeypatch,
          secrets=["a-good-passphrase",     # unlock the existing one
                   "", "PHONE-NUMBER", ""],  # skip sign_in_code, add phone
          typed=[""])

    opened = vault._open(json.loads(isolated.read_text(encoding="utf-8")),
                         "a-good-passphrase")
    assert opened == {"sign_in_code": "FIRST-CODE", "phone": "PHONE-NUMBER"}


def test_a_wrong_passphrase_on_rerun_destroys_nothing(isolated, monkeypatch):
    """
    THE disaster case. A typo at the "existing passphrase" prompt must leave
    the vault exactly as it was — not overwrite it, not truncate it, not
    start a fresh one on top of it. He would have no way to know until the
    next time he needed a credential, by which point the original is gone.
    """
    drive(monkeypatch,
          secrets=["a-good-passphrase", "a-good-passphrase", "IRREPLACEABLE", "", ""],
          typed=[""])
    before = isolated.read_bytes()

    assert drive(monkeypatch, secrets=["wrong-passphrase"], typed=[]) == 1

    assert isolated.read_bytes() == before, "a mistyped passphrase modified the vault"
    opened = vault._open(json.loads(isolated.read_text(encoding="utf-8")),
                         "a-good-passphrase")
    assert opened == {"sign_in_code": "IRREPLACEABLE"}


def test_starting_over_is_possible_but_only_deliberately(isolated, monkeypatch):
    """
    Pressing Enter on a blank line at the existing-passphrase prompt starts a
    fresh vault. That is destructive, so it must require a NEW passphrase and
    its confirmation — never happen as a side effect of an empty answer.
    """
    drive(monkeypatch,
          secrets=["first-passphrase", "first-passphrase", "OLD-CODE", "", ""],
          typed=[""])

    drive(monkeypatch,
          secrets=["",                       # blank: start over
                   "second-passphrase", "second-passphrase",
                   "NEW-CODE", "", ""],
          typed=[""])

    blob = json.loads(isolated.read_text(encoding="utf-8"))
    assert vault._open(blob, "first-passphrase") is None, "the old passphrase still works"
    assert vault._open(blob, "second-passphrase") == {"sign_in_code": "NEW-CODE"}


# ---------------------------------------------------------------------------
# Fitting into the rest of the system.
# ---------------------------------------------------------------------------
def test_the_names_it_offers_are_names_the_autofill_path_can_use(isolated, monkeypatch):
    """
    Storing "sign_in_code" and then having fill_credential expect something
    else would be a vault full of unreachable secrets.
    """
    drive(monkeypatch,
          secrets=["a-good-passphrase", "a-good-passphrase", "CODE", "PHONE", "MAIL"],
          typed=[""])
    vault.unlock_vault("a-good-passphrase")
    listed = vault.list_secrets()
    for name, _ in vault_setup.SUGGESTED:
        assert name in listed, f"{name} was stored but list_secrets cannot see it"


def test_he_says_which_site_each_secret_is_for_here(isolated, monkeypatch,
                                                    tmp_path):
    """
    The one deliberate place a secret is tied to its site - no tool can do
    it (tests/test_a_secret_goes_only_where_he_said.py). Asked after the
    vault is saved, one input() per secret in name order; Enter keeps what
    is there, so re-running to add one secret costs no retyping.
    """
    monkeypatch.setattr(vault, "SECRET_SITES_PATH", tmp_path / "secret_sites.json")
    drive(monkeypatch,
          secrets=["a-good-passphrase", "a-good-passphrase", "CODE", "PHONE", ""],
          typed=["",                   # no extra secrets
                 "*",                  # phone: any application form
                 "commonapp.org"])     # sign_in_code
    assert vault.secret_binding("phone", "https://anything.example/") == "here"
    assert vault.secret_binding("sign_in_code", "https://apply.commonapp.org/") == "here"
    assert vault.secret_binding("sign_in_code", "https://evil.example/") == "elsewhere"

    # Again, pressing Enter at every site: the ties survive.
    drive(monkeypatch, secrets=["a-good-passphrase", "", "", ""],
          typed=["", "", ""])
    assert vault.secret_binding("sign_in_code", "https://evil.example/") == "elsewhere"

    # Something that is not a site changes nothing.
    drive(monkeypatch, secrets=["a-good-passphrase", "", "", ""],
          typed=["", "", "not a site at all"])
    assert vault.secret_binding("sign_in_code", "https://commonapp.org/") == "here"


def test_the_setup_script_reads_and_writes_through_the_vault_module():
    """
    Everything the script does to disk must go through jarvis.tools.vault.

    Two reasons. First, a second implementation of the file format is a
    second thing to keep in step, and the failure would be a vault the
    assistant cannot open. Second, the fixture above isolates these tests by
    rebinding vault.VAULT_PATH — if the script grew its own open() call, the
    isolation would silently miss and this suite would be writing to his real
    data directory.

    Checked in the source rather than by reloading the module: a reload here
    re-imports from the already-patched vault module, so the obvious version
    of this assertion compares two temp paths and passes for free.
    """
    import inspect
    import re

    source = inspect.getsource(vault_setup)
    assert "from jarvis.tools.vault import" in source
    for helper in ("_load_blob", "_save_blob", "_seal", "_open"):
        assert helper in source, f"the script no longer uses vault.{helper}"

    # The lookbehind matters: a plain "open(" substring also matches the
    # vault's own _open(), which is exactly the call this is meant to allow.
    direct_io = re.findall(r"(?<![\w.])(open|write_text|write_bytes)\s*\(", source)
    assert not direct_io, (
        f"vault_setup writes to disk directly ({direct_io}) instead of "
        "through vault.py"
    )


def test_status_tells_him_to_run_it_when_there_is_no_vault(isolated):
    """
    The one manual step must be discoverable from inside Jalen, not only from
    a handoff document nobody reads twice.
    """
    out = vault.vault_status()
    assert "vault_setup.py" in out
    assert "never stores it" in out
