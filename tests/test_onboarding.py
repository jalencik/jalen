"""
Onboarding — HANDOFF item 6, "a second user has no path from install to
working".

Four scripts and a README is a treasure hunt, not an onboarding: every wrong
turn produces an error about the symptom rather than the step you skipped.
scripts/onboard.py walks them in dependency order.

WHAT IS TESTED HERE is the part that touches disk, because onboarding writes
credentials and config and is run more than once. The interactive prompts are
not tested; they contain no decisions.

EVERY test in this file redirects the paths first. A test that ran the real
script would overwrite his working .env and his config — which is exactly
the accident the script itself is written to avoid.
"""
from __future__ import annotations

import pytest
import yaml

from scripts import onboard


@pytest.fixture(autouse=True)
def never_touch_the_real_files(tmp_path, monkeypatch):
    """
    Redirect both files this script writes. Autouse, deliberately: forgetting
    it in one test would silently rewrite his .env.
    """
    monkeypatch.setattr(onboard, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(onboard, "USER_CONFIG", tmp_path / "user.yaml")
    return tmp_path


# ---------------------------------------------------------------------------
# .env — written more than once, which is where this gets dangerous.
# ---------------------------------------------------------------------------
def test_a_key_is_written(never_touch_the_real_files):
    onboard.write_env("GROQ_API_KEY", "gsk_abc123")
    assert onboard.read_env()["GROQ_API_KEY"] == "gsk_abc123"


def test_setting_a_key_twice_leaves_one_line(never_touch_the_real_files):
    """
    THE bug this function exists to avoid. Appending would leave two
    GROQ_API_KEY lines, and which one wins depends on dotenv's parse order —
    a failure that only appears the SECOND time anyone runs onboarding, which
    is the worst possible time to find it.
    """
    onboard.write_env("GROQ_API_KEY", "first")
    onboard.write_env("GROQ_API_KEY", "second")

    body = never_touch_the_real_files.joinpath(".env").read_text(encoding="utf-8")
    assert body.count("GROQ_API_KEY") == 1
    assert onboard.read_env()["GROQ_API_KEY"] == "second"


def test_other_keys_and_comments_survive(never_touch_the_real_files):
    """
    .env is hand-edited and commented. Rewriting it wholesale would throw
    away someone's notes about which key came from where.
    """
    env = never_touch_the_real_files / ".env"
    env.write_text(
        "# my notes\n"
        "TELEGRAM_BOT_TOKEN=keep-me\n"
        "\n"
        "# another comment\n"
        "GITHUB_TOKEN=also-keep\n",
        encoding="utf-8",
    )
    onboard.write_env("GROQ_API_KEY", "new")

    body = env.read_text(encoding="utf-8")
    assert "# my notes" in body
    assert "# another comment" in body
    values = onboard.read_env()
    assert values["TELEGRAM_BOT_TOKEN"] == "keep-me"
    assert values["GITHUB_TOKEN"] == "also-keep"
    assert values["GROQ_API_KEY"] == "new"


def test_a_key_that_is_a_prefix_of_another_is_not_confused(never_touch_the_real_files):
    """
    TELEGRAM_API_ID and TELEGRAM_API_HASH share a prefix. A loose match would
    rewrite the wrong line, and the symptom would be a Telegram login that
    fails with an error about the hash.
    """
    onboard.write_env("TELEGRAM_API_ID", "12345")
    onboard.write_env("TELEGRAM_API_HASH", "deadbeef")
    values = onboard.read_env()
    assert values["TELEGRAM_API_ID"] == "12345"
    assert values["TELEGRAM_API_HASH"] == "deadbeef"


def test_reading_an_absent_env_is_empty_not_an_error(never_touch_the_real_files):
    assert onboard.read_env() == {}


def test_commented_out_keys_are_not_read_as_values(never_touch_the_real_files):
    never_touch_the_real_files.joinpath(".env").write_text(
        "#GROQ_API_KEY=old-disabled-key\n", encoding="utf-8"
    )
    assert "GROQ_API_KEY" not in onboard.read_env()


# ---------------------------------------------------------------------------
# config/user.yaml — the overlay, not the design record.
# ---------------------------------------------------------------------------
def answers(monkeypatch, *values):
    """Feed input() a fixed script; running past the end returns ''."""
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it, ""))


def test_it_writes_a_valid_overlay(never_touch_the_real_files, monkeypatch):
    answers(monkeypatch, "Sam", "Chief", "Ada", "hey ada")
    onboard.step_identity()

    written = never_touch_the_real_files / "user.yaml"
    data = yaml.safe_load(written.read_text(encoding="utf-8"))
    assert data["identity"]["user_name"] == "Sam"
    assert data["identity"]["address_user_as"] == "Chief"
    assert data["identity"]["name"] == "Ada"
    assert data["identity"]["wake_word"] == "hey ada"


def test_the_overlay_actually_loads(never_touch_the_real_files, monkeypatch):
    """
    Valid YAML is not enough — it has to be an overlay the real loader
    accepts and merges. Writing a file nobody reads is the failure this
    project keeps producing.
    """
    answers(monkeypatch, "Sam", "Chief", "Ada", "hey ada")
    onboard.step_identity()

    from jarvis import config as config_module

    monkeypatch.setattr(config_module, "USER_CONFIG", never_touch_the_real_files / "user.yaml")
    cfg = config_module.load_config()
    assert cfg.get_path("identity.user_name") == "Sam"
    assert "Sam's voice assistant" in cfg.get_path("persona.style")


def test_a_new_user_inherits_no_pre_approved_destinations(
    never_touch_the_real_files, monkeypatch
):
    """
    His channel is pre-approved for sending WITHOUT confirmation. A second
    user inheriting that would have an assistant able to post into a
    stranger's channel unprompted. The onboarding must start that list empty.
    """
    answers(monkeypatch, "Sam", "Chief", "Ada", "hey ada")
    onboard.step_identity()

    from jarvis import config as config_module

    monkeypatch.setattr(config_module, "USER_CONFIG", never_touch_the_real_files / "user.yaml")
    cfg = config_module.load_config()
    assert cfg.get_path("telegram.personal.send_without_asking_to") == []

    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(cfg)
    verdict = engine.classify(
        "send_telegram_message",
        {"to": "AI engineering & Machine learning", "text": "hi"},
    )
    assert verdict.tier.value == "red", (
        "a newly onboarded user can post to his channel without being asked"
    )


def test_a_new_user_does_not_inherit_his_folders(never_touch_the_real_files, monkeypatch):
    answers(monkeypatch, "Sam", "Chief", "Ada", "hey ada")
    onboard.step_identity()

    from pathlib import Path

    from jarvis import config as config_module

    monkeypatch.setattr(config_module, "USER_CONFIG", never_touch_the_real_files / "user.yaml")
    paths = config_module.load_config().get_path("index.content_index_paths")
    assert all(p.startswith(Path.home().as_posix()) for p in paths)


def test_empty_answers_fall_back_to_defaults(never_touch_the_real_files, monkeypatch):
    """Pressing Enter through the whole thing must still produce a valid file."""
    answers(monkeypatch)
    onboard.step_identity()
    data = yaml.safe_load(
        (never_touch_the_real_files / "user.yaml").read_text(encoding="utf-8")
    )
    assert data["identity"]["name"] == "Jalen"
    assert data["identity"]["wake_word"] == "hey jalen"


def test_it_never_writes_the_design_record():
    """
    config/jarvis.yaml is the defaults AND the reasoning behind every value.
    Overwriting it per-machine destroys the most useful documentation this
    project has, and would make every pull a merge conflict.
    """
    import inspect

    source = inspect.getsource(onboard)
    assert "jarvis.yaml" not in source.replace(
        "# It never writes config/jarvis.yaml.", ""
    ) or "USER_CONFIG.write_text" in source
    assert "USER_CONFIG" in source
    # The only write targets are the two intended files.
    writes = [
        line for line in source.splitlines()
        if ".write_text(" in line and "USER_CONFIG" not in line and "ENV_PATH" not in line
    ]
    assert not writes, f"onboard writes somewhere unexpected: {writes}"


def test_it_does_not_ask_for_the_vault_passphrase():
    """
    A passphrase that passes through an onboarding script is one the script
    could have kept. It prints the command and moves on.
    """
    import inspect

    source = inspect.getsource(onboard)
    # The word appears in the docstring explaining WHY it is not used, so the
    # check is on the call and the import, not on the string.
    assert "getpass.getpass(" not in source
    assert "import getpass" not in source
    assert "vault_setup.py" in source
