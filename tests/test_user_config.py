"""
Per-user configuration — HANDOFF item 6, "hardcoded to him".

config/jalen.yaml carried his name, his folders and his channel, and every
value in it is commented with WHY it is that value. Those comments are the
most useful documentation this project has, so the fix is not to strip them
out: jalen.yaml stays the defaults and the design record, and an optional,
gitignored config/user.yaml overlays it.

Two properties matter more than the merging itself:

  1. WITH NO user.yaml, NOTHING CHANGES. He has a working machine. A
     refactor that shifts one path or one destination breaks something he
     is using today, and would break it silently.

  2. THE PROTECTED PATHS ACTUALLY PROTECT. never_touch was pinned to
     C:/Users/user. On anyone else's machine that list reads as a full set
     of protections and guards nothing at all — the worst kind of security
     bug, because it looks present.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jalen import config as config_module


@pytest.fixture()
def with_user_config(tmp_path, monkeypatch):
    """Write a user.yaml and reload the config through it."""
    def build(body: str):
        path = tmp_path / "user.yaml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        monkeypatch.setattr(config_module, "USER_CONFIG", path)
        return config_module.load_config()
    return build


# ---------------------------------------------------------------------------
# The property that protects his working machine.
# ---------------------------------------------------------------------------
def test_with_no_user_config_nothing_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "USER_CONFIG", tmp_path / "absent.yaml")
    cfg = config_module.load_config()
    assert cfg.get_path("identity.name") == "Jalen"
    assert cfg.get_path("identity.user_name") == "User"
    assert cfg.get_path("identity.wake_word") == "hey jalen"
    assert cfg.get_path("tts.voice") == "en-US-AndrewNeural"


def test_the_paths_still_resolve_to_exactly_what_was_hardcoded(monkeypatch, tmp_path):
    """
    {home} replaced literal C:/Users/user. On HIS machine the result must be
    byte-identical, or a refactor for a hypothetical second user has broken
    the one real user.
    """
    monkeypatch.setattr(config_module, "USER_CONFIG", tmp_path / "absent.yaml")
    cfg = config_module.load_config()
    home = Path.home().as_posix()

    assert cfg.get_path("index.content_index_paths") == [
        f"{home}/Documents", f"{home}/Downloads", f"{home}/Desktop",
    ]
    protected = cfg.get_path("safety_tiers.never_touch.paths")
    assert f"{home}/.ssh" in protected
    assert f"{home}/AppData/Roaming/ProtonVPN" in protected
    assert not any("{home}" in p for p in protected), "a placeholder survived expansion"


def test_the_protected_paths_are_not_pinned_to_one_username(monkeypatch, tmp_path):
    """
    THE security property. Hardcoded to C:/Users/user, never_touch reads as a
    full list of protections on a second machine and guards nothing — a
    security bug that looks present, which is worse than an absent one.
    """
    monkeypatch.setattr(config_module, "USER_CONFIG", tmp_path / "absent.yaml")
    raw = (config_module.CONFIG_DIR / "safety.yaml").read_text(encoding="utf-8")
    personal = [
        line for line in raw.splitlines()
        if line.strip().startswith('- "C:/Users/')
    ]
    assert not personal, f"never_touch still names a specific user: {personal}"


# ---------------------------------------------------------------------------
# Overlaying.
# ---------------------------------------------------------------------------
def test_a_second_user_can_take_over_the_identity(with_user_config):
    cfg = with_user_config("""
        identity:
          name: "Ada"
          user_name: "Sam"
          address_user_as: "Sam"
          wake_word: "hey ada"
    """)
    assert cfg.get_path("identity.name") == "Ada"
    assert cfg.get_path("identity.user_name") == "Sam"
    assert cfg.get_path("identity.wake_word") == "hey ada"


def test_the_overlaid_name_reaches_the_persona_prompt(with_user_config):
    """
    The prompt interpolates {user_name} and {address_user_as}. If the overlay
    were merged AFTER expansion, a second user would get an assistant that
    called them by the first user's name — in every single reply.
    """
    cfg = with_user_config("""
        identity:
          user_name: "Sam"
          address_user_as: "Chief"
    """)
    style = cfg.get_path("persona.style")
    assert "Sam's voice assistant" in style
    assert '"Chief"' in style
    assert "O'ktam" not in style


def test_only_the_keys_you_set_are_replaced(with_user_config):
    """A partial overlay must not wipe its siblings."""
    cfg = with_user_config("""
        identity:
          user_name: "Sam"
    """)
    assert cfg.get_path("identity.user_name") == "Sam"
    assert cfg.get_path("identity.name") == "Jalen"       # untouched
    assert cfg.get_path("identity.address_user_as") == "Boss"


def test_nested_sections_merge_rather_than_replace(with_user_config):
    cfg = with_user_config("""
        ui:
          theme:
            idle: "#ffffff"
    """)
    assert cfg.get_path("ui.theme.idle") == "#ffffff"
    assert cfg.get_path("ui.theme.listening") is not None  # sibling survives
    assert cfg.get_path("ui.orb_position") == "center"     # sibling section survives


def test_lists_replace_instead_of_appending(with_user_config):
    """
    Deliberate, and the reason is safety. These lists are allowlists — safe
    folders, pre-approved send destinations. "These are my folders" must not
    silently mean "mine AND his", or a second user's assistant keeps write
    access to the first user's Desktop and permission to post in his channel.
    """
    cfg = with_user_config("""
        index:
          content_index_paths:
            - "D:/work"
    """)
    assert cfg.get_path("index.content_index_paths") == ["D:/work"]


def test_a_user_can_point_the_placeholders_at_their_own_home(with_user_config):
    cfg = with_user_config("""
        index:
          content_index_paths:
            - "{home}/Projects"
    """)
    assert cfg.get_path("index.content_index_paths") == [
        f"{Path.home().as_posix()}/Projects"
    ]


def test_the_pre_approved_destinations_can_be_replaced(with_user_config):
    """
    His channel is pre-approved for sending without confirmation. A second
    user inheriting that would have an assistant able to post into a stranger's
    channel unprompted — the single most dangerous default to leave behind.
    """
    cfg = with_user_config("""
        telegram:
          personal:
            send_without_asking_to:
              - "Saved Messages"
    """)
    from jalen.safety import SafetyEngine

    engine = SafetyEngine(cfg)
    assert engine._is_preapproved("send_telegram_message", {"to": "Saved Messages"})
    assert not engine._is_preapproved(
        "send_telegram_message", {"to": "AI engineering & Machine learning"}
    ), "a second user inherited his channel as a pre-approved destination"


# ---------------------------------------------------------------------------
# Failure modes. An optional file must never lock anyone out.
# ---------------------------------------------------------------------------
def test_a_malformed_user_config_is_ignored_not_fatal(tmp_path, monkeypatch, capsys):
    """
    A YAML typo must not take the assistant down. There is no voice available
    to explain the failure at that point — config load happens before
    anything can speak — so the only symptom would be an app that no longer
    starts, with a traceback in a window that does not exist under pythonw.
    """
    bad = tmp_path / "user.yaml"
    bad.write_text("identity:\n  name: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "USER_CONFIG", bad)

    cfg = config_module.load_config()
    assert cfg.get_path("identity.name") == "Jalen"
    assert "couldn't read" in capsys.readouterr().out


def test_a_user_config_that_is_not_a_mapping_is_ignored(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "user.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "USER_CONFIG", bad)

    cfg = config_module.load_config()
    assert cfg.get_path("identity.name") == "Jalen"
    assert "not a mapping" in capsys.readouterr().out


def test_an_empty_user_config_is_harmless(tmp_path, monkeypatch):
    empty = tmp_path / "user.yaml"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(config_module, "USER_CONFIG", empty)
    assert config_module.load_config().get_path("identity.name") == "Jalen"


def test_the_user_config_is_gitignored():
    """
    It holds a person's name, folders and channels. Committing it would put
    one user's details into everyone else's checkout, and would make every
    pull a merge conflict over whose name the assistant uses.
    """
    ignored = (config_module.ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "user.yaml" in ignored


# ---------------------------------------------------------------------------
# Merge semantics, directly.
# ---------------------------------------------------------------------------
def test_merge_does_not_mutate_the_base():
    base = {"a": {"b": 1, "c": 2}}
    config_module._merge(base, {"a": {"b": 9}})
    assert base == {"a": {"b": 1, "c": 2}}


def test_merge_replaces_a_scalar_with_a_dict_and_back():
    assert config_module._merge({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}
    assert config_module._merge({"a": {"b": 2}}, {"a": 1}) == {"a": 1}


def test_merge_handles_a_missing_overlay():
    assert config_module._merge({"a": 1}, {}) == {"a": 1}
    assert config_module._merge({"a": 1}, None) == {"a": 1}


# ---------------------------------------------------------------------------
# The two things that are unavoidably about ONE person.
# ---------------------------------------------------------------------------
def test_his_own_voice_skill_is_still_found(monkeypatch, tmp_path):
    """
    The lookup gained a config source. It must not have displaced the
    automatic one — his 35 KB my-voice skill lives in ~/.claude/skills and is
    found without any configuration, and that has to keep working.
    """
    monkeypatch.setattr(config_module, "USER_CONFIG", tmp_path / "absent.yaml")
    monkeypatch.delenv("JARVIS_VOICE_SKILL", raising=False)
    assert config_module.load_config().get_path("personal.voice_guide") == ""


def test_a_second_user_can_point_at_their_own_writing(tmp_path, monkeypatch):
    from jalen.tools import voice

    sample = tmp_path / "my-essays.md"
    sample.write_text("I write in short sentences. Like this.", encoding="utf-8")

    monkeypatch.delenv("JARVIS_VOICE_SKILL", raising=False)
    monkeypatch.setattr(voice, "_SEARCH_PATHS", [])
    monkeypatch.setattr(voice, "_cache", {})

    from jalen.config import CONFIG

    monkeypatch.setitem(CONFIG, "personal", {"voice_guide": str(sample)})
    guide = voice.voice_guide()
    assert "I write in short sentences" in guide
    assert "This is how HE writes" in guide


def test_with_no_writing_sample_it_says_it_would_be_guessing(monkeypatch):
    """
    An assistant that writes AS you, badly, under your name, is worse than
    one that declines. The message must name the fix, not just the failure —
    the old one listed three paths, which is only actionable if you already
    know a my-voice skill is a thing that exists.
    """
    from jalen.tools import voice

    monkeypatch.delenv("JARVIS_VOICE_SKILL", raising=False)
    monkeypatch.setattr(voice, "_SEARCH_PATHS", [])
    monkeypatch.setattr(voice, "_cache", {})

    from jalen.config import CONFIG

    monkeypatch.setitem(CONFIG, "personal", {"voice_guide": ""})
    out = voice.voice_guide()
    assert "guessing" in out
    assert "personal.voice_guide" in out
    assert "user.yaml" in out


def test_a_second_user_signs_posts_with_their_own_handle(monkeypatch):
    """
    The post format signs off with a Telegram handle in four worked examples.
    Left alone, a second user's assistant tells readers to DM somebody else.
    """
    from jalen.tools import voice
    from jalen.config import CONFIG

    monkeypatch.setitem(CONFIG, "personal", {"channel_handle": "@someone_else"})
    guide = voice.community_post_guide()
    assert "@someone_else" in guide
    assert voice.DEFAULT_HANDLE not in guide


def test_his_handle_survives_when_nothing_is_configured(monkeypatch):
    from jalen.tools import voice
    from jalen.config import CONFIG

    monkeypatch.setitem(CONFIG, "personal", {"channel_handle": ""})
    assert voice.DEFAULT_HANDLE in voice.community_post_guide()
